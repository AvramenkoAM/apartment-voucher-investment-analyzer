#!/usr/bin/env python3
"""Fill publication dates from listing detail pages when card text is incomplete."""

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

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "uk-UA,uk;q=0.9,ru;q=0.8,en;q=0.7",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch detail pages and fill created publication dates.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help=f"Input CSV. Default: {DEFAULT_INPUT}")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help=f"Output CSV. Default: {DEFAULT_OUTPUT}")
    parser.add_argument("--sleep", type=float, default=0.4, help="Pause between detail requests.")
    parser.add_argument("--max-rows", type=int, default=0, help="Limit rows to process. 0 means no limit.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing publication dates.")
    return parser.parse_args()


def normalize_iso_datetime(value: str) -> str:
    match = re.search(r"\b(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?\b", value or "")
    if not match:
        return ""
    second = match.group(6) or "00"
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)} {match.group(4)}:{match.group(5)}:{second}"


def html_text(html: str) -> str:
    return re.sub(r"\s+", " ", BeautifulSoup(html, "html.parser").get_text(" ", strip=True)).strip()


def extract_dim_ria_publication_date(html: str) -> str:
    for field in ("publishing_date", "created_at"):
        match = re.search(rf'"{field}"\s*:\s*"([^"]+)"', html)
        if match:
            normalized = normalize_iso_datetime(match.group(1))
            if normalized:
                return normalized
    return ""


def extract_rem_publication_date(html: str) -> str:
    text = html_text(html)
    patterns = [
        r"(?:Створений|Створено|Создан|Создано|Опубліковано|Опубликовано|Оновлений|Оновлено|Обновлен|Обновлено)\s*:\s*(\d{2}-\d{2}-\d{4}\s+\d{2}:\d{2}:\d{2})",
        r"(?:Створений|Створено|Создан|Создано|Опубліковано|Опубликовано|Оновлений|Оновлено|Обновлен|Обновлено)\s*:\s*(\d{2}[./]\d{2}[./]\d{4}(?:\s+\d{2}:\d{2}(?::\d{2})?)?)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        value = match.group(1).replace(".", "-").replace("/", "-")
        date_match = re.search(r"(\d{2})-(\d{2})-(\d{4})(?:\s+(\d{2}):(\d{2})(?::(\d{2}))?)?", value)
        if date_match:
            hour = date_match.group(4) or "00"
            minute = date_match.group(5) or "00"
            second = date_match.group(6) or "00"
            return f"{date_match.group(3)}-{date_match.group(2)}-{date_match.group(1)} {hour}:{minute}:{second}"
    return ""


def extract_publication_date(source: str, html: str) -> str:
    if source == "DIM.RIA":
        return extract_dim_ria_publication_date(html)
    if source == "REM.ua":
        return extract_rem_publication_date(html)
    return ""


def should_process(row: dict[str, str], overwrite: bool) -> bool:
    if row.get("source") not in {"DIM.RIA", "REM.ua"}:
        return False
    if overwrite:
        return True
    return not (row.get("дата публікації") or "").strip()


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
    failed = 0
    for row in rows:
        if not should_process(row, args.overwrite):
            continue
        if args.max_rows and processed >= args.max_rows:
            break

        processed += 1
        url = row.get("url", "")
        try:
            response = session.get(url, timeout=30)
            response.raise_for_status()
        except requests.RequestException as error:
            failed += 1
            print(f"Publication detail failed #{row.get('№')}: {error}")
            continue

        publication_date = extract_publication_date(row.get("source", ""), response.text)
        if publication_date:
            row["дата публікації"] = publication_date
            updated += 1
            print(f"Publication date #{row.get('№')}: {publication_date}")

        time.sleep(args.sleep)

    with output_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(
        f"Publication date detail enrichment: processed={processed}, updated={updated}, "
        f"failed={failed}, output={output_path}"
    )


if __name__ == "__main__":
    main()
