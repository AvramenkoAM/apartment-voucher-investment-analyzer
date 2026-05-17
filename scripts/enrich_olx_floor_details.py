#!/usr/bin/env python3
"""Enrich OLX rows with floor data from listing detail pages."""

from __future__ import annotations

import argparse
import csv
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "data/apartments_multi_source.csv"
DEFAULT_OUTPUT = PROJECT_ROOT / "data/apartments_multi_source.csv"
OUTPUT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

MONTHS = {
    "січня": 1,
    "лютого": 2,
    "березня": 3,
    "квітня": 4,
    "травня": 5,
    "червня": 6,
    "липня": 7,
    "серпня": 8,
    "вересня": 9,
    "жовтня": 10,
    "листопада": 11,
    "грудня": 12,
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "uk-UA,uk;q=0.9,ru;q=0.8,en;q=0.7",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch OLX detail pages and fill floor columns.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help=f"Input CSV. Default: {DEFAULT_INPUT}")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help=f"Output CSV. Default: {DEFAULT_OUTPUT}")
    parser.add_argument("--sleep", type=float, default=0.4, help="Pause between OLX detail requests.")
    parser.add_argument("--max-rows", type=int, default=0, help="Limit OLX rows to process. 0 means no limit.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing valid floor values.")
    return parser.parse_args()


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def normalize_int(value: str) -> str:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return ""
    return str(number) if 0 < number <= 40 else ""


def is_valid_floor(floor: str, floors_total: str) -> bool:
    floor_value = normalize_int(floor)
    total_value = normalize_int(floors_total)
    if not floor_value:
        return False
    if total_value and int(floor_value) > int(total_value):
        return False
    return True


def parse_floor_from_text(text: str) -> tuple[str, str]:
    patterns = [
        r"Поверх:\s*(\d{1,2})\s+Поверховість:\s*(\d{1,2})",
        r"Этаж:\s*(\d{1,2})\s+Этажность:\s*(\d{1,2})",
        r"\b(\d{1,2})\s*(?:й|ий|ий)?\s*поверх\s*(?:з|із|/)\s*(\d{1,2})\b",
        r"\b(\d{1,2})\s*(?:й|ый)?\s*этаж\s*(?:из|/)\s*(\d{1,2})\b",
        r"\bповерх\s*(\d{1,2})\s*(?:з|із|/)\s*(\d{1,2})\b",
        r"\bэтаж\s*(\d{1,2})\s*(?:из|/)\s*(\d{1,2})\b",
        r"\b(\d{1,2})\s*/\s*(\d{1,2})\s*(?:пов|поверх|эт|этаж)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        floor = normalize_int(match.group(1))
        floors_total = normalize_int(match.group(2))
        if is_valid_floor(floor, floors_total):
            return floor, floors_total

    first_floor_match = re.search(r"\b(?:перший|первый)\s+(?:поверх|этаж)\b", text, flags=re.IGNORECASE)
    if first_floor_match:
        return "1", ""

    return "", ""


def parse_publication_date_from_text(text: str) -> str:
    match = re.search(r"Опубліковано\s+(\d{1,2})\s+([а-яіїєґ]+)\s+(\d{4})\s*р\.?", text, flags=re.IGNORECASE)
    if not match:
        return ""
    day = int(match.group(1))
    month = MONTHS.get(match.group(2).lower())
    year = int(match.group(3))
    if not month:
        return ""
    return f"{year:04d}-{month:02d}-{day:02d} 00:00:00"


def fetch_detail_text(session: requests.Session, url: str) -> str:
    response = session.get(url, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    return clean_text(soup.get_text(" ", strip=True))


def should_process(row: dict[str, str], overwrite: bool) -> bool:
    if row.get("source") != "OLX":
        return False
    if overwrite:
        return True
    return not is_valid_floor(row.get("поверх", ""), row.get("поверховість", ""))


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)

    with input_path.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        rows = list(reader)
        fieldnames = reader.fieldnames or []

    session = requests.Session()
    session.headers.update(HEADERS)

    processed = 0
    updated = 0
    updated_date = 0
    failed = 0
    for row in rows:
        if not should_process(row, args.overwrite):
            continue
        if args.max_rows and processed >= args.max_rows:
            break

        processed += 1
        url = row.get("url", "")
        try:
            text = fetch_detail_text(session, url)
            floor, floors_total = parse_floor_from_text(text)
            publication_date = parse_publication_date_from_text(text)
        except requests.RequestException as error:
            failed += 1
            print(f"OLX detail failed #{row.get('№')}: {error}")
            continue

        if floor:
            row["поверх"] = floor
            if floors_total:
                row["поверховість"] = floors_total
            updated += 1
            print(f"OLX floor #{row.get('№')}: {floor}/{floors_total or '?'}")
        if publication_date:
            row["дата публікації"] = publication_date
            updated_date += 1

        time.sleep(args.sleep)

    with output_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(
        f"OLX floor detail enrichment: processed={processed}, updated={updated}, "
        f"date_updated={updated_date}, failed={failed}, output={output_path}"
    )


if __name__ == "__main__":
    main()
