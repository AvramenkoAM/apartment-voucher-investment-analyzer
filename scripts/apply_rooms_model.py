#!/usr/bin/env python3
"""Apply the local rooms extraction model to fill `кількість кімнат`."""

from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "data/apartments_multi_source.csv"
DEFAULT_OUTPUT = PROJECT_ROOT / "data/apartments_multi_source.csv"
DEFAULT_MODEL = PROJECT_ROOT / "models/rooms_extractor.json"


@dataclass
class Candidate:
    rooms: str
    pattern: str
    score: float
    snippet: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply rooms extractor model to a CSV.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help=f"Input CSV. Default: {DEFAULT_INPUT}")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help=f"Output CSV. Default: {DEFAULT_OUTPUT}")
    parser.add_argument("--model", default=str(DEFAULT_MODEL), help=f"Model JSON. Default: {DEFAULT_MODEL}")
    parser.add_argument("--min-score", type=float, default=1.6, help="Minimum confidence score.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing room counts too.")
    return parser.parse_args()


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def normalize_rooms(value: str) -> str:
    value = clean_text(value)
    if value in {"1", "2", "3", "4"}:
        return value
    if value in {"5", "5+"}:
        return "5+"
    return ""


def candidate_from_match(pattern_name: str, match: re.Match[str]) -> str:
    word_rooms = {
        "word_one_ua": "1",
        "word_one_ru": "1",
        "word_two_ua": "2",
        "word_two_ru": "2",
        "word_three_ua": "3",
        "word_three_ru": "3",
        "word_four_ua": "4",
        "word_four_ru": "4",
    }
    if pattern_name in word_rooms:
        return word_rooms[pattern_name]
    if pattern_name == "studio":
        return "1"
    if pattern_name == "more_than_four":
        return "5+"
    return normalize_rooms(match.group(1))


def snippet(text: str, start: int, end: int) -> str:
    return clean_text(text[max(0, start - 24) : min(len(text), end + 36)])


def pattern_candidates(text: str, model: dict) -> list[Candidate]:
    candidates: list[Candidate] = []
    for name, pattern in model.get("patterns", {}).items():
        stats = model.get("pattern_stats", {}).get(name, {})
        precision = float(stats.get("precision") or 0)
        base_score = 1.0 + precision
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            rooms = candidate_from_match(name, match)
            if not rooms:
                continue
            local_snippet = snippet(text, match.start(), match.end())
            score = base_score
            if name in {
                "digit_k",
                "digit_dash_x",
                "digit_dash_oh",
                "digit_short_flat",
                "digit_room_ua",
                "digit_no_room_ua",
                "digit_room_ru",
                "digit_room_word_ua",
                "digit_room_word_ru",
                "word_one_ua",
                "word_two_ua",
                "word_three_ua",
                "word_four_ua",
                "word_one_ru",
                "word_two_ru",
                "word_three_ru",
                "word_four_ru",
            }:
                score += 0.5
            if name == "studio":
                score += 0.7
            if re.search(r"(?:квартира|кв\.?|продаж|продам|продається|продается)", local_snippet, flags=re.IGNORECASE):
                score += 0.4
            if re.search(r"(?:м²|м2|кв\.?\s*м)", local_snippet, flags=re.IGNORECASE):
                score += 0.2
            if re.search(r"(?:поверх|этаж)", local_snippet, flags=re.IGNORECASE):
                score += 0.1
            candidates.append(Candidate(rooms=rooms, pattern=name, score=score, snippet=local_snippet))
    return candidates


def url_slug_candidates(url: str) -> list[Candidate]:
    slug = re.sub(r"[._/?=&-]+", " ", url.lower())
    candidates = []
    patterns = {
        "url_digit_k": r"\b([1-5])\s*[кk]\b",
        "url_digit_room": r"\b([1-5])\s*(?:komn|komnat|kimnat|kmnat)\b",
        "url_studio": r"\b(?:studiya|studiyu|studio|smart)\b",
    }
    for name, pattern in patterns.items():
        for match in re.finditer(pattern, slug, flags=re.IGNORECASE):
            rooms = "1" if name == "url_studio" else normalize_rooms(match.group(1))
            if rooms:
                candidates.append(Candidate(rooms=rooms, pattern=name, score=1.4, snippet=snippet(slug, match.start(), match.end())))
    return candidates


def best_rooms(row: dict[str, str], model: dict) -> Candidate | None:
    text = clean_text(" ".join([row.get("опис", ""), row.get("adress", "")]))
    candidates = pattern_candidates(text, model)
    candidates.extend(url_slug_candidates(row.get("url", "")))
    if not candidates:
        return None

    votes: dict[str, float] = {}
    for candidate in candidates:
        votes[candidate.rooms] = votes.get(candidate.rooms, 0.0) + candidate.score

    best_room, vote_score = max(votes.items(), key=lambda item: item[1])
    best_candidate = max((candidate for candidate in candidates if candidate.rooms == best_room), key=lambda item: item.score)
    best_candidate.score = max(best_candidate.score, vote_score)
    return best_candidate


def main() -> None:
    args = parse_args()
    model = json.loads(Path(args.model).read_text(encoding="utf-8"))

    with Path(args.input).open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        rows = list(reader)
        fieldnames = reader.fieldnames or []

    updated = 0
    skipped_low_confidence = 0
    skipped_existing = 0
    for row in rows:
        current = normalize_rooms(row.get("кількість кімнат", ""))
        if current and not args.overwrite:
            skipped_existing += 1
            continue
        candidate = best_rooms(row, model)
        if candidate and candidate.score >= args.min_score:
            row["кількість кімнат"] = candidate.rooms
            updated += 1
        else:
            skipped_low_confidence += 1

    with Path(args.output).open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(
        f"Applied rooms model: updated={updated}, skipped_existing={skipped_existing}, "
        f"skipped_low_confidence={skipped_low_confidence}, output={args.output}"
    )


if __name__ == "__main__":
    main()
