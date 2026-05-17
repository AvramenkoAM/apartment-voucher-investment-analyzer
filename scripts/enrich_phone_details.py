#!/usr/bin/env python3
"""Fetch listing detail pages and fill `телефон` when the source exposes it."""

from __future__ import annotations

import argparse
import csv
import json
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

import fill_phone_repair


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

OLX_PHONE_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.olx.ua/",
    "X-Requested-With": "XMLHttpRequest",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fill phone numbers from listing detail pages.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help=f"Input CSV. Default: {DEFAULT_INPUT}")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help=f"Output CSV. Default: {DEFAULT_OUTPUT}")
    parser.add_argument("--sleep", type=float, default=0.4, help="Pause between detail requests.")
    parser.add_argument("--max-rows", type=int, default=0, help="Limit rows to process. 0 means no limit.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing phone values.")
    parser.add_argument("--sources", default="", help="Comma-separated sources to process. Empty means all.")
    parser.add_argument(
        "--olx-phone-delay",
        type=float,
        default=1.2,
        help="Extra pause before OLX limited-phones API request. Default: 1.2.",
    )
    return parser.parse_args()


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def append_phone(phones: list[str], raw_phone: str) -> None:
    phone = fill_phone_repair.normalize_phone(raw_phone)
    if phone and phone not in phones:
        phones.append(phone)


def walk_json(value: Any) -> list[Any]:
    values = [value]
    if isinstance(value, dict):
        for child in value.values():
            values.extend(walk_json(child))
    elif isinstance(value, list):
        for child in value:
            values.extend(walk_json(child))
    return values


def extract_jsonld_phones(soup: BeautifulSoup) -> list[str]:
    phones: list[str] = []
    for script in soup.select('script[type="application/ld+json"]'):
        content = script.string or script.get_text("", strip=True)
        if not content:
            continue
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            continue
        for item in walk_json(parsed):
            if isinstance(item, dict) and "telephone" in item:
                value = item.get("telephone")
                if isinstance(value, list):
                    for phone in value:
                        append_phone(phones, str(phone))
                else:
                    append_phone(phones, str(value))
    return phones


def extract_raw_json_phones(html: str) -> list[str]:
    phones: list[str] = []
    for match in re.finditer(r'"(?:telephone|phone|phoneNumber)"\s*:\s*"([^"]+)"', html, flags=re.IGNORECASE):
        append_phone(phones, match.group(1))
    return phones


def extract_tel_links(soup: BeautifulSoup) -> list[str]:
    phones: list[str] = []
    for link in soup.select('a[href^="tel:"]'):
        append_phone(phones, link.get("href", "").removeprefix("tel:"))
        append_phone(phones, link.get_text(" ", strip=True))
    return phones


def extract_visible_phones(soup: BeautifulSoup) -> list[str]:
    text = clean_text(soup.get_text(" ", strip=True))
    phone_text = fill_phone_repair.extract_phone(text)
    return [phone.strip() for phone in phone_text.split(";") if phone.strip()]


def extract_rem_phones(html: str, soup: BeautifulSoup) -> list[str]:
    phones = []
    for extractor in (extract_jsonld_phones, extract_tel_links, extract_visible_phones):
        for phone in extractor(soup):
            append_phone(phones, phone)
    for phone in extract_raw_json_phones(html):
        append_phone(phones, phone)
    return phones


def extract_dim_ria_phones(html: str, soup: BeautifulSoup) -> list[str]:
    phones = []
    for phone in extract_tel_links(soup):
        append_phone(phones, phone)
    for phone in extract_raw_json_phones(html):
        append_phone(phones, phone)
    for phone in extract_visible_phones(soup):
        append_phone(phones, phone)
    return phones


def extract_olx_phones(html: str, soup: BeautifulSoup) -> list[str]:
    phones = []
    for phone in extract_tel_links(soup):
        append_phone(phones, phone)
    for node in soup.select('[data-testid="contact-phone"]'):
        append_phone(phones, node.get_text(" ", strip=True))
    for phone in extract_raw_json_phones(html):
        append_phone(phones, phone)
    for phone in extract_visible_phones(soup):
        append_phone(phones, phone)
    return phones


def extract_olx_offer_id(html: str) -> str:
    """Find OLX numeric offer id from static page HTML."""
    patterns = [
        r'"sku"\s*:\s*"(\d{6,})"',
        r'"id"\s*:\s*(\d{6,})',
        r'ID:\s*<!--\s*-->\s*(\d{6,})',
        r'ad-id=(\d{6,})',
        r'ad-id%3D(\d{6,})',
        r'content_ids\]=(\d{6,})',
        r'content_ids%5D=(\d{6,})',
    ]
    for pattern in patterns:
        match = re.search(pattern, html)
        if match:
            return match.group(1)
    return ""


def fetch_olx_limited_phones(session: requests.Session, offer_id: str, referer: str) -> list[str]:
    phones: list[str] = []
    if not offer_id:
        return phones

    api_url = f"https://www.olx.ua/api/v1/offers/{offer_id}/limited-phones/"
    headers = dict(OLX_PHONE_HEADERS)
    headers["Referer"] = referer
    try:
        response = session.get(api_url, headers=headers, timeout=30)
        response.raise_for_status()
        payload = response.json()
    except requests.HTTPError as error:
        detail = ""
        try:
            payload = response.json()
            detail = clean_text(str(payload.get("error", {}).get("detail", "")))
        except (ValueError, AttributeError):
            detail = clean_text(response.text[:240])
        if detail:
            print(f"OLX phone API failed offer_id={offer_id}: {response.status_code} {detail}")
        else:
            print(f"OLX phone API failed offer_id={offer_id}: {error}")
        return phones
    except (requests.RequestException, ValueError) as error:
        print(f"OLX phone API failed offer_id={offer_id}: {error}")
        return phones

    data = payload.get("data") if isinstance(payload, dict) else None
    raw_phones = data.get("phones") if isinstance(data, dict) else None
    if isinstance(raw_phones, list):
        for raw_phone in raw_phones:
            append_phone(phones, str(raw_phone))
    return phones


def extract_phones(
    source: str,
    html: str,
    *,
    session: requests.Session | None = None,
    url: str = "",
    olx_phone_delay: float = 0.0,
) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    if source == "REM.ua":
        return extract_rem_phones(html, soup)
    if source == "DIM.RIA":
        return extract_dim_ria_phones(html, soup)
    if source == "OLX":
        phones = extract_olx_phones(html, soup)
        if not phones and session is not None:
            offer_id = extract_olx_offer_id(html)
            if olx_phone_delay > 0:
                time.sleep(olx_phone_delay)
            phones = fetch_olx_limited_phones(session, offer_id, url)
        return phones
    return extract_visible_phones(soup)


def should_process(row: dict[str, str], overwrite: bool, sources: set[str]) -> bool:
    if sources and row.get("source") not in sources:
        return False
    if overwrite:
        return True
    return not (row.get("телефон") or "").strip()


def enrich_phone_details(
    input_path: Path,
    output_path: Path,
    *,
    sleep_seconds: float = 0.4,
    max_rows: int = 0,
    overwrite: bool = False,
    sources: set[str] | None = None,
    olx_phone_delay: float = 1.2,
) -> dict[str, Any]:
    sources = sources or set()
    with input_path.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        rows = list(reader)
        fieldnames = fill_phone_repair.ensure_columns(reader.fieldnames or [])

    session = requests.Session()
    session.headers.update(HEADERS)

    processed = 0
    updated = 0
    failed = 0
    no_phone = 0
    updated_rows = []
    no_phone_by_source: Counter[str] = Counter()
    updated_by_source: Counter[str] = Counter()

    for row in rows:
        if not should_process(row, overwrite, sources):
            continue
        if max_rows and processed >= max_rows:
            break

        processed += 1
        url = row.get("url", "")
        source = row.get("source", "")
        try:
            response = session.get(url, timeout=30)
            response.raise_for_status()
        except requests.RequestException as error:
            failed += 1
            print(f"Phone detail failed #{row.get('№')}: {error}")
            continue

        phones = extract_phones(
            source,
            response.text,
            session=session,
            url=url,
            olx_phone_delay=olx_phone_delay,
        )
        if phones:
            row["телефон"] = "; ".join(phones)
            updated += 1
            updated_by_source[source] += 1
            updated_rows.append({"№": row.get("№"), "source": source, "url": url, "телефон": row["телефон"]})
            print(f"Phone #{row.get('№')} {source}: {row['телефон']}")
        else:
            no_phone += 1
            no_phone_by_source[source] += 1

        time.sleep(sleep_seconds)

    with output_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return {
        "processed": processed,
        "updated": updated,
        "failed": failed,
        "no_phone": no_phone,
        "updated_by_source": dict(updated_by_source),
        "no_phone_by_source": dict(no_phone_by_source),
        "updated_rows": updated_rows,
        "output": str(output_path),
    }


def main() -> None:
    args = parse_args()
    sources = {source.strip() for source in args.sources.split(",") if source.strip()}
    result = enrich_phone_details(
        Path(args.input),
        Path(args.output),
        sleep_seconds=args.sleep,
        max_rows=args.max_rows,
        overwrite=args.overwrite,
        sources=sources,
        olx_phone_delay=args.olx_phone_delay,
    )
    print(
        "Phone detail enrichment: "
        f"processed={result['processed']}, updated={result['updated']}, "
        f"failed={result['failed']}, no_phone={result['no_phone']}, output={result['output']}"
    )


if __name__ == "__main__":
    main()
