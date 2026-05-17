#!/usr/bin/env python3
"""Train a small local address extraction model from the current CSV.

The model is intentionally transparent: it stores frequent address tokens,
source statistics, and marker patterns learned from rows where `adress` is
already known.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "data/apartments_multi_source.csv"
DEFAULT_MODEL = PROJECT_ROOT / "models/address_extractor.json"

GENERIC_TOKENS = {
    "вул",
    "вулиця",
    "ул",
    "улица",
    "просп",
    "проспект",
    "пров",
    "провулок",
    "дорога",
    "бульв",
    "бульвар",
    "жм",
    "ж",
    "м",
    "район",
    "рн",
    "будинок",
    "дом",
}

MARKERS = [
    "вул",
    "вулиця",
    "ул",
    "улица",
    "просп",
    "проспект",
    "пров",
    "провулок",
    "пер",
    "переулок",
    "дорога",
    "бульв",
    "бульвар",
    "площа",
    "площадь",
    "ж/м",
    "жм",
    "жк",
    "масив",
    "массив",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train transparent address extractor model.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help=f"Input CSV. Default: {DEFAULT_INPUT}")
    parser.add_argument("--model", default=str(DEFAULT_MODEL), help=f"Output model JSON. Default: {DEFAULT_MODEL}")
    return parser.parse_args()


def tokenize(text: str) -> list[str]:
    return [
        token.lower()
        for token in re.findall(r"[A-Za-zА-Яа-яІіЇїЄєҐґ0-9']+", text)
        if len(token) > 1
    ]


def normalize_address(address: str) -> str:
    address = re.sub(r"\s+", " ", address).strip(" ,.;:-")
    return address


def contains_address(text: str, address: str) -> bool:
    if not address:
        return False
    return address.lower() in text.lower()


def marker_stats(text: str, address: str) -> Counter:
    stats = Counter()
    lower_text = text.lower()
    lower_address = address.lower()
    address_index = lower_text.find(lower_address)
    if address_index < 0:
        return stats
    window = lower_text[max(0, address_index - 24) : address_index + len(lower_address) + 24]
    for marker in MARKERS:
        if marker in window:
            stats[marker] += 1
    return stats


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    output_path = Path(args.model)

    with input_path.open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))

    address_counter = Counter()
    token_counter = Counter()
    city_token_counter: dict[str, Counter] = defaultdict(Counter)
    source_counter = Counter()
    source_known_counter = Counter()
    markers_counter = Counter()

    trained_rows = 0
    for row in rows:
        address = normalize_address(row.get("adress", ""))
        if not address:
            continue
        # OLX/OBYAVA addresses are often weak labels generated from title/url.
        # Use them for inference later, but do not let noisy weak labels train
        # the core address vocabulary unless they contain a clear marker.
        source = row.get("source", "")
        lower_address = address.lower()
        has_clear_marker = any(
            marker in lower_address
            for marker in ["вул", "ул", "просп", "пров", "дорога", "бульв", "ж/м", "жк"]
        )
        if source in {"OLX", "OBYAVA.ua"} and not has_clear_marker:
            continue

        trained_rows += 1
        city = row.get("city", "")
        text = " ".join(
            [
                row.get("url", ""),
                row.get("опис", ""),
                row.get("район", ""),
            ]
        )

        source_known_counter[source] += 1
        address_counter[address] += 1
        for token in tokenize(address):
            if token not in GENERIC_TOKENS:
                token_counter[token] += 1
                if city:
                    city_token_counter[city][token] += 1
        if contains_address(text, address):
            markers_counter.update(marker_stats(text, address))

    for row in rows:
        source_counter[row.get("source", "")] += 1

    model = {
        "version": 1,
        "trained_rows": trained_rows,
        "total_rows": len(rows),
        "known_addresses": [address for address, _ in address_counter.most_common()],
        "address_tokens": [token for token, count in token_counter.most_common() if count >= 1],
        "city_address_tokens": {
            city: [token for token, count in counter.most_common() if count >= 1]
            for city, counter in city_token_counter.items()
        },
        "source_known_ratio": {
            source: round(source_known_counter[source] / total, 4)
            for source, total in source_counter.items()
            if total
        },
        "learned_markers": [marker for marker, _ in markers_counter.most_common()] or MARKERS,
        "generic_tokens": sorted(GENERIC_TOKENS),
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"Trained address model on {trained_rows}/{len(rows)} labeled rows. "
        f"Saved {len(model['address_tokens'])} address tokens to {output_path}"
    )


if __name__ == "__main__":
    main()
