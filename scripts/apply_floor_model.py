#!/usr/bin/env python3
"""Apply the local floor extraction model to fill `поверх` and `поверховість`."""

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
DEFAULT_MODEL = PROJECT_ROOT / "models/floor_extractor.json"


@dataclass
class Candidate:
    floor: str
    floors_total: str
    pattern: str
    score: float
    snippet: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply floor extractor model to a CSV.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help=f"Input CSV. Default: {DEFAULT_INPUT}")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help=f"Output CSV. Default: {DEFAULT_OUTPUT}")
    parser.add_argument("--model", default=str(DEFAULT_MODEL), help=f"Model JSON. Default: {DEFAULT_MODEL}")
    parser.add_argument("--min-score", type=float, default=1.7, help="Minimum confidence score.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing floor values too.")
    return parser.parse_args()


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def normalize_int(value: str) -> str:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return ""
    return str(number) if 0 < number <= 40 else ""


def candidate_from_match(name: str, match: re.Match[str]) -> tuple[str, str]:
    if name in {"first_floor_ua", "first_floor_ru"}:
        return "1", ""
    if name in {"middle_floor_ua", "middle_floor_ru"}:
        return "", ""
    floor = normalize_int(match.group(1))
    floors_total = normalize_int(match.group(2)) if name in {"floor_of_total_ua", "floor_of_total_ru", "floor_slash"} else ""
    if floors_total and floor and int(floor) > int(floors_total):
        return "", ""
    return floor, floors_total


def snippet(text: str, start: int, end: int) -> str:
    return clean_text(text[max(0, start - 36) : min(len(text), end + 48)])


def pattern_candidates(text: str, model: dict) -> list[Candidate]:
    candidates = []
    for name, pattern in model.get("patterns", {}).items():
        stats = model.get("pattern_stats", {}).get(name, {})
        precision = float(stats.get("floor_precision") or 0)
        base_score = 1.0 + precision
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            floor, floors_total = candidate_from_match(name, match)
            if not floor:
                continue

            local_snippet = snippet(text, match.start(), match.end())
            score = base_score
            if floors_total:
                score += 0.7
            if name in {"floor_of_total_ua", "floor_of_total_ru", "floor_slash"}:
                score += 0.4
            if re.search(r"(?:поверх|поверсі|этаж|этаже)", local_snippet, flags=re.IGNORECASE):
                score += 0.4
            if re.search(r"(?:кімнат|комнат|м²|м2|кв\.?\s*м)", local_snippet, flags=re.IGNORECASE):
                score += 0.2
            candidates.append(Candidate(floor=floor, floors_total=floors_total, pattern=name, score=score, snippet=local_snippet))
    return candidates


def best_floor(row: dict[str, str], model: dict) -> Candidate | None:
    text = clean_text(" ".join([row.get("опис", ""), row.get("url", "")]))
    candidates = pattern_candidates(text, model)
    if not candidates:
        return None

    # Prefer explicit floor/total pairs, then strongest confidence.
    return max(candidates, key=lambda candidate: (bool(candidate.floors_total), candidate.score))


def is_weak_floor(floor: str, floors_total: str) -> bool:
    floor_value = normalize_int(floor)
    total_value = normalize_int(floors_total)
    if not floor_value:
        return True
    if total_value and int(floor_value) > int(total_value):
        return True
    return False


def main() -> None:
    args = parse_args()
    model = json.loads(Path(args.model).read_text(encoding="utf-8"))

    with Path(args.input).open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        rows = list(reader)
        fieldnames = reader.fieldnames or []

    updated = 0
    skipped_existing = 0
    skipped_low_confidence = 0
    for row in rows:
        current_floor = row.get("поверх", "").strip()
        current_total = row.get("поверховість", "").strip()
        if current_floor and not args.overwrite and not is_weak_floor(current_floor, current_total):
            skipped_existing += 1
            continue

        candidate = best_floor(row, model)
        if candidate and candidate.score >= args.min_score:
            row["поверх"] = candidate.floor
            if candidate.floors_total:
                row["поверховість"] = candidate.floors_total
            updated += 1
        else:
            skipped_low_confidence += 1

    with Path(args.output).open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(
        f"Applied floor model: updated={updated}, skipped_existing={skipped_existing}, "
        f"skipped_low_confidence={skipped_low_confidence}, output={args.output}"
    )


if __name__ == "__main__":
    main()
