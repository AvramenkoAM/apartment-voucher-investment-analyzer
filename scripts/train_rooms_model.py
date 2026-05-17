#!/usr/bin/env python3
"""Train a transparent local model for extracting room counts from listings."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "data/apartments_multi_source.csv"
DEFAULT_MODEL = PROJECT_ROOT / "models/rooms_extractor.json"

ROOM_PATTERNS = {
    "digit_k": r"\b([1-5])\s*[- ]?\s*[кk]\b",
    "digit_dash_x": r"\b([1-5])\s*[- ]?\s*[хx]\s*[- ]?\s*(?:комн|кімн|кв|ком|кім)\w*\b",
    "digit_dash_oh": r"\b([1-5])\s*[- ]?\s*о[хx]\s+(?:комн|кімн|кв|ком|кім)\w*\b",
    "digit_room_ua": r"\b([1-5])\s*[- ]?\s*(?:кімн(?:ат(?:а|и|ну|на|ний|них)?)?|кім\.?)\b",
    "digit_room_ru": r"\b([1-5])\s*[- ]?\s*(?:комн(?:ат(?:а|ы|ную|ная|ных)?)?|ком\.?)\b",
    "digit_room_word_ua": r"\b([1-5])\s+(?:кімнатна|кімнатну|кімнатної|кімнатні|кімнати|кімната)\b",
    "digit_no_room_ua": r"\b([1-5])\s*но\s*(?:к[іi]мнатн\w*|к[іi]мн\w*)\b",
    "digit_room_word_ru": r"\b([1-5])\s+(?:комнатная|комнатную|комнатной|комнаты|комната)\b",
    "digit_x": r"\b([1-5])\s*[хx]\b",
    "digit_short_flat": r"\b(?:продам|продаж|продажа)\s+([1-5])\s*(?:кв\.?|квартир[ауиы])\b",
    "word_one_ua": r"\b(?:однокімнатн\w*|однокімн\w*)\b",
    "word_two_ua": r"\b(?:двокімнатн\w*|двокімн\w*)\b",
    "word_three_ua": r"\b(?:трикімнатн\w*|трикімн\w*)\b",
    "word_four_ua": r"\b(?:чотирикімнатн\w*|чотирикімн\w*)\b",
    "word_one_ru": r"\b(?:однокомнатн\w*|однокомн\w*)\b",
    "word_two_ru": r"\b(?:двухкомнатн\w*|двухкомн\w*|двушка|двушк\w*)\b",
    "word_three_ru": r"\b(?:трехкомнатн\w*|трёхкомнатн\w*|трехкомн\w*|трёхкомн\w*|трешка|трёшка|трешк\w*|трёшк\w*)\b",
    "word_four_ru": r"\b(?:четырехкомнатн\w*|четырёхкомнатн\w*|четырехкомн\w*|четырёхкомн\w*)\b",
    "studio": r"\b(?:студія|студію|студия|студию|студийн\w*|смарт|smart)\b",
    "more_than_four": r"\b(?:більше\s*4|более\s*4|5\+)\b",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train transparent rooms extractor model.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help=f"Input CSV. Default: {DEFAULT_INPUT}")
    parser.add_argument("--model", default=str(DEFAULT_MODEL), help=f"Output model JSON. Default: {DEFAULT_MODEL}")
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


def pattern_candidates(text: str) -> list[tuple[str, str]]:
    candidates = []
    for name, pattern in ROOM_PATTERNS.items():
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            rooms = candidate_from_match(name, match)
            if rooms:
                candidates.append((name, rooms))
    return candidates


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    output_path = Path(args.model)

    with input_path.open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))

    pattern_hits = Counter()
    pattern_correct = Counter()
    pattern_by_room: dict[str, Counter] = defaultdict(Counter)
    room_counter = Counter()
    trained_rows = 0

    for row in rows:
        label = normalize_rooms(row.get("кількість кімнат", ""))
        if not label:
            continue
        trained_rows += 1
        room_counter[label] += 1
        text = clean_text(" ".join([row.get("опис", ""), row.get("adress", ""), row.get("url", "")]))
        seen_patterns = set()
        for pattern_name, rooms in pattern_candidates(text):
            seen_patterns.add((pattern_name, rooms))
        for pattern_name, rooms in seen_patterns:
            pattern_hits[pattern_name] += 1
            pattern_by_room[pattern_name][rooms] += 1
            if rooms == label:
                pattern_correct[pattern_name] += 1

    pattern_stats = {}
    for pattern_name in ROOM_PATTERNS:
        hits = pattern_hits[pattern_name]
        correct = pattern_correct[pattern_name]
        pattern_stats[pattern_name] = {
            "hits": hits,
            "correct": correct,
            "precision": round(correct / hits, 4) if hits else 0,
            "rooms": dict(pattern_by_room[pattern_name]),
        }

    model = {
        "version": 1,
        "trained_rows": trained_rows,
        "total_rows": len(rows),
        "room_distribution": dict(room_counter),
        "patterns": ROOM_PATTERNS,
        "pattern_stats": pattern_stats,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Trained rooms model on {trained_rows}/{len(rows)} labeled rows. Saved to {output_path}")


if __name__ == "__main__":
    main()
