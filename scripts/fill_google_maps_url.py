#!/usr/bin/env python3
"""Fill `url_google_maps` from apartment city and address.

The script does not call a geocoding API. It creates a Google Maps search URL
from the best available address text, so it works offline and does not need an
API key.
"""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path
from urllib.parse import quote_plus


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "data/apartments_multi_source.csv"
DEFAULT_OUTPUT = PROJECT_ROOT / "data/apartments_multi_source.csv"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fill url_google_maps from city and adress columns.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help=f"Input CSV. Default: {DEFAULT_INPUT}")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help=f"Output CSV. Default: {DEFAULT_OUTPUT}")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing url_google_maps values.")
    return parser.parse_args()


def clean_text(value: str) -> str:
    value = re.sub(r"\s+", " ", value or "")
    return value.strip(" ,.;:-")


def normalize_address(address: str) -> str:
    address = clean_text(address)
    address = re.sub(r"\b(?:program(?:me|my|y|i)?|vaucher|voucher|sertif(?:ikat|кат)?)\b", "", address, flags=re.IGNORECASE)
    address = re.sub(r"\s+", " ", address)
    return address.strip(" ,.;:-")


def maps_query(row: dict[str, str]) -> str:
    city = clean_text(row.get("city", ""))
    address = normalize_address(row.get("adress", ""))
    district = clean_text(row.get("район", ""))

    if not address:
        return ""

    parts = [address, city]
    if district and district.lower() not in address.lower():
        parts.append(district)
    parts.append("Україна")
    return ", ".join(part for part in parts if part)


def google_maps_url(query: str) -> str:
    return f"https://www.google.com/maps/search/?api=1&query={quote_plus(query)}"


def fill_maps_urls(input_path: Path, output_path: Path, overwrite: bool) -> tuple[int, int]:
    with input_path.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        rows = list(reader)
        fieldnames = reader.fieldnames or []

    if "url_google_maps" not in fieldnames:
        fieldnames.append("url_google_maps")

    updated = 0
    skipped_without_address = 0
    for row in rows:
        if row.get("url_google_maps", "").strip() and not overwrite:
            continue

        query = maps_query(row)
        if not query:
            skipped_without_address += 1
            continue

        row["url_google_maps"] = google_maps_url(query)
        updated += 1

    with output_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return updated, skipped_without_address


def main() -> None:
    args = parse_args()
    updated, skipped_without_address = fill_maps_urls(Path(args.input), Path(args.output), args.overwrite)
    print(
        f"Filled url_google_maps: updated={updated}, "
        f"skipped_without_address={skipped_without_address}, output={args.output}"
    )


if __name__ == "__main__":
    main()
