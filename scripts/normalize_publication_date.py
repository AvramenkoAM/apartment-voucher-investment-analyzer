#!/usr/bin/env python3
"""Normalize `дата публікації` to one datetime format."""

from __future__ import annotations

import argparse
import csv
import re
from datetime import date, datetime, timedelta
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "data/apartments_multi_source.csv"
DEFAULT_OUTPUT = PROJECT_ROOT / "data/apartments_multi_source.csv"
OUTPUT_FORMAT = "%Y-%m-%d %H:%M:%S"

MONTHS = {
    "січ": 1,
    "січня": 1,
    "янв": 1,
    "января": 1,
    "лют": 2,
    "лютого": 2,
    "фев": 2,
    "февраля": 2,
    "бер": 3,
    "березня": 3,
    "мар": 3,
    "марта": 3,
    "кві": 4,
    "квітня": 4,
    "апр": 4,
    "апреля": 4,
    "тра": 5,
    "травня": 5,
    "мая": 5,
    "чер": 6,
    "червня": 6,
    "июн": 6,
    "июня": 6,
    "лип": 7,
    "липня": 7,
    "июл": 7,
    "июля": 7,
    "сер": 8,
    "серпня": 8,
    "авг": 8,
    "августа": 8,
    "вер": 9,
    "вересня": 9,
    "сен": 9,
    "сентября": 9,
    "жов": 10,
    "жовтня": 10,
    "окт": 10,
    "октября": 10,
    "лис": 11,
    "листопада": 11,
    "ноя": 11,
    "ноября": 11,
    "гру": 12,
    "грудня": 12,
    "дек": 12,
    "декабря": 12,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Normalize publication date values in apartment CSV.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help=f"Input CSV. Default: {DEFAULT_INPUT}")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help=f"Output CSV. Default: {DEFAULT_OUTPUT}")
    parser.add_argument(
        "--reference-date",
        default=date.today().isoformat(),
        help="Date for relative values like 'сьогодні'/'вчора' in YYYY-MM-DD format.",
    )
    parser.add_argument("--overwrite-empty-only", action="store_true", help="Only fill empty date cells.")
    parser.add_argument(
        "--fallback-reference-date",
        action="store_true",
        help="Use reference date at 00:00:00 when no publication date can be parsed.",
    )
    return parser.parse_args()


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def build_datetime(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime | None:
    try:
        return datetime(year, month, day, hour, minute)
    except ValueError:
        return None


def parse_absolute_numeric(text: str) -> datetime | None:
    match = re.search(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})(?:\s+(\d{1,2}):(\d{2}))?\b", text)
    if not match:
        return None
    day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
    if year < 100:
        year += 2000
    hour = int(match.group(4) or 0)
    minute = int(match.group(5) or 0)
    return build_datetime(year, month, day, hour, minute)


def parse_iso_datetime(text: str) -> datetime | None:
    match = re.search(r"\b(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?\b", text)
    if not match:
        return None
    year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
    hour = int(match.group(4) or 0)
    minute = int(match.group(5) or 0)
    second = int(match.group(6) or 0)
    try:
        return datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None


def parse_relative(text: str, reference_date: date) -> datetime | None:
    lower = text.lower()
    if re.search(r"\b(?:сьогодні|сегодня)\b", lower):
        base = reference_date
    elif re.search(r"\b(?:вчора|вчера)\b", lower):
        base = reference_date - timedelta(days=1)
    else:
        return None

    match = re.search(r"(?:о|в)?\s*(\d{1,2}):(\d{2})", lower)
    hour = int(match.group(1)) if match else 0
    minute = int(match.group(2)) if match else 0
    return datetime(base.year, base.month, base.day, hour, minute)


def parse_month_name(text: str, reference_date: date) -> datetime | None:
    lower = text.lower()
    pattern = r"\b(\d{1,2})\s+([а-яіїєґ]+)\.?(?:\s+(\d{4}))?(?:\s*(?:р\.?|року|г\.?))?(?:\s*(?:о|в)?\s*(\d{1,2}):(\d{2}))?"
    for match in re.finditer(pattern, lower, flags=re.IGNORECASE):
        month_name = match.group(2).rstrip(".")
        month = MONTHS.get(month_name)
        if not month:
            continue
        day = int(match.group(1))
        year = int(match.group(3) or reference_date.year)
        hour = int(match.group(4) or 0)
        minute = int(match.group(5) or 0)
        parsed = build_datetime(year, month, day, hour, minute)
        if parsed:
            return parsed
    return None


def parse_created_marker(text: str, reference_date: date, reference_now: datetime) -> datetime | None:
    marker_pattern = (
        r"(?:опубліковано|опубликовано|додано|добавлено|"
        r"продаж\s+квартири|продається\s+квартира|продаю\s+квартиру|продам\s+квартиру)"
        r"\s*(?:[·|:-]\s*)?([^·|]{1,80})"
    )
    for match in re.finditer(marker_pattern, text.lower(), flags=re.IGNORECASE):
        fragment = clean_text(match.group(1))
        parsed = (
            parse_iso_datetime(fragment)
            or parse_absolute_numeric(fragment)
            or parse_relative(fragment, reference_date)
            or parse_month_name(fragment, reference_date)
            or parse_hours_ago(fragment, reference_now)
            or parse_day_only(fragment, reference_date)
        )
        if parsed:
            return parsed
    return None


def parse_hours_ago(text: str, reference_now: datetime) -> datetime | None:
    lower = text.lower()
    match = re.search(r"\b(\d{1,2})\s+(?:годин|години|годину|час(?:ов|а)?)\s+тому\b", lower)
    if not match:
        return None
    return reference_now - timedelta(hours=int(match.group(1)))


def parse_day_only(text: str, reference_date: date) -> datetime | None:
    if not re.fullmatch(r"\d{1,2}", text):
        return None
    day = int(text)
    return build_datetime(reference_date.year, reference_date.month, day)


def candidate_text(row: dict[str, str]) -> str:
    return clean_text(" ".join([row.get("дата публікації", ""), row.get("опис", "")]))


def parse_publication_datetime(row: dict[str, str], reference_date: date) -> datetime | None:
    value = clean_text(row.get("дата публікації", ""))
    description = clean_text(row.get("опис", ""))
    text = candidate_text(row)
    reference_now = datetime(reference_date.year, reference_date.month, reference_date.day, 23, 59)
    created_marker = parse_created_marker(description, reference_date, reference_now)
    if created_marker:
        return created_marker

    value_parsers = [
        lambda: parse_iso_datetime(value),
        lambda: parse_absolute_numeric(value),
        lambda: parse_relative(value, reference_date),
        lambda: parse_month_name(value, reference_date),
        lambda: parse_hours_ago(value, reference_now),
        lambda: parse_day_only(value, reference_date),
    ]
    for parser in value_parsers:
        parsed = parser()
        if parsed:
            return parsed

    if row.get("source") == "DIM.RIA":
        return None

    parsers = [
        lambda: parse_iso_datetime(text),
        lambda: parse_absolute_numeric(text),
        lambda: parse_relative(text, reference_date),
        lambda: parse_month_name(text, reference_date),
        lambda: parse_hours_ago(text, reference_now),
    ]
    for parser in parsers:
        parsed = parser()
        if parsed:
            return parsed
    return None


def main() -> None:
    args = parse_args()
    reference_date = datetime.strptime(args.reference_date, "%Y-%m-%d").date()

    with Path(args.input).open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        rows = list(reader)
        fieldnames = reader.fieldnames or []

    updated = 0
    skipped = 0
    failed = 0
    for row in rows:
        if args.overwrite_empty_only and row.get("дата публікації", "").strip():
            skipped += 1
            continue
        parsed = parse_publication_datetime(row, reference_date)
        if not parsed:
            if args.fallback_reference_date:
                parsed = datetime(reference_date.year, reference_date.month, reference_date.day)
            else:
                row["дата публікації"] = ""
                failed += 1
                continue
        row["дата публікації"] = parsed.strftime(OUTPUT_FORMAT)
        updated += 1

    with Path(args.output).open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(
        f"Normalized publication dates: updated={updated}, skipped={skipped}, "
        f"failed={failed}, reference_date={reference_date.isoformat()}, output={args.output}"
    )


if __name__ == "__main__":
    main()
