#!/usr/bin/env python3
"""Train a transparent local model for extracting floor and building floors."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "data/apartments_multi_source.csv"
DEFAULT_MODEL = PROJECT_ROOT / "models/floor_extractor.json"

FLOOR_PATTERNS = {
    "floor_of_total_ua": r"\b(\d{1,2})\s*поверх\s*(?:з|із|/)\s*(\d{1,2})\b",
    "floor_of_total_ru": r"\b(\d{1,2})\s*этаж\s*(?:из|с|/)\s*(\d{1,2})\b",
    "floor_slash": r"\b(\d{1,2})\s*/\s*(\d{1,2})\s*(?:пов|поверх|эт|этаж)?\b",
    "floor_before_ua": r"\b(?:на|розташована на|розташований на)\s*(\d{1,2})\s*(?:му|м|й|ому|ий)?\s*поверсі\b",
    "floor_before_ru": r"\b(?:на|расположена на|расположен на)\s*(\d{1,2})\s*(?:м|ом|й)?\s*этаже\b",
    "first_floor_ua": r"\b(?:перший|першому|першім|першого)\s+поверх\w*\b",
    "first_floor_ru": r"\b(?:первый|первом|первого)\s+этаж\w*\b",
    "middle_floor_ua": r"\b(?:середній|средний)\s+поверх\b",
    "middle_floor_ru": r"\b(?:средний)\s+этаж\b",
    "floor_only_ua": r"\b(\d{1,2})\s*поверх\b",
    "floor_only_ru": r"\b(\d{1,2})\s*этаж\b",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train transparent floor extractor model.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help=f"Input CSV. Default: {DEFAULT_INPUT}")
    parser.add_argument("--model", default=str(DEFAULT_MODEL), help=f"Output model JSON. Default: {DEFAULT_MODEL}")
    return parser.parse_args()


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def normalize_int(value: str) -> str:
    try:
        number = int(value)
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


def main() -> None:
    args = parse_args()
    with Path(args.input).open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))

    pattern_hits = Counter()
    pattern_correct_floor = Counter()
    pattern_correct_total = Counter()
    floor_counter = Counter()
    total_counter = Counter()
    trained_rows = 0

    for row in rows:
        floor_label = normalize_int(row.get("поверх", ""))
        total_label = normalize_int(row.get("поверховість", ""))
        if not floor_label:
            continue
        trained_rows += 1
        floor_counter[floor_label] += 1
        if total_label:
            total_counter[total_label] += 1

        text = clean_text(" ".join([row.get("опис", ""), row.get("url", "")]))
        for name, pattern in FLOOR_PATTERNS.items():
            for match in re.finditer(pattern, text, flags=re.IGNORECASE):
                floor, floors_total = candidate_from_match(name, match)
                if not floor:
                    continue
                pattern_hits[name] += 1
                if floor == floor_label:
                    pattern_correct_floor[name] += 1
                if floors_total and floors_total == total_label:
                    pattern_correct_total[name] += 1

    pattern_stats = {}
    for name in FLOOR_PATTERNS:
        hits = pattern_hits[name]
        pattern_stats[name] = {
            "hits": hits,
            "correct_floor": pattern_correct_floor[name],
            "correct_total": pattern_correct_total[name],
            "floor_precision": round(pattern_correct_floor[name] / hits, 4) if hits else 0,
            "total_precision": round(pattern_correct_total[name] / hits, 4) if hits else 0,
        }

    model = {
        "version": 1,
        "trained_rows": trained_rows,
        "total_rows": len(rows),
        "patterns": FLOOR_PATTERNS,
        "pattern_stats": pattern_stats,
        "floor_distribution": dict(floor_counter),
        "total_distribution": dict(total_counter),
    }

    output_path = Path(args.model)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Trained floor model on {trained_rows}/{len(rows)} labeled rows. Saved to {output_path}")


if __name__ == "__main__":
    main()
