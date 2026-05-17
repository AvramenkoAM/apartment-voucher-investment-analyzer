#!/usr/bin/env python3
"""Collect LUN listings with a real browser via Playwright.

LUN often blocks plain HTTP clients with Cloudflare. This script uses
Playwright and can run in a visible browser with a persistent profile so a
human can pass the safety check once and reuse cookies later.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from collect_apartments_multi_source import (
    CSV_COLUMNS,
    SearchPage,
    clean_text,
    fill_common,
    parse_price_usd,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CSV = PROJECT_ROOT / "data/apartments_multi_source.csv"
DEFAULT_PROFILE = PROJECT_ROOT / ".playwright/lun-profile"
DEFAULT_USD_UAH_RATE = 44.15

LUN_PAGES = [
    SearchPage("LUN", "Одеса", "https://lun.ua/sale/odesa/flats-45000-usd", lambda *_: [], "https://lun.ua"),
    SearchPage("LUN", "Дніпро", "https://lun.ua/sale/dnipro/flats-45000-usd", lambda *_: [], "https://lun.ua"),
]

CHALLENGE_MARKERS = [
    "триває перевірка безпеки",
    "just a moment",
    "enable javascript and cookies",
    "cloudflare",
]


@dataclass
class LunExtractResult:
    rows: list[dict[str, str]]
    blocked: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect LUN listings through Playwright.")
    parser.add_argument("--csv", default=str(DEFAULT_CSV), help=f"CSV to append/update. Default: {DEFAULT_CSV}")
    parser.add_argument("--min-price", type=int, default=35_000, help="Minimum price in USD.")
    parser.add_argument("--max-price", type=int, default=45_000, help="Maximum price in USD.")
    parser.add_argument("--usd-uah-rate", type=float, default=DEFAULT_USD_UAH_RATE, help="UAH to USD rate.")
    parser.add_argument("--headful", action="store_true", help="Run a visible browser.")
    parser.add_argument(
        "--manual-wait",
        type=int,
        default=0,
        help="Seconds to keep the page open for manual Cloudflare verification.",
    )
    parser.add_argument(
        "--profile-dir",
        default=str(DEFAULT_PROFILE),
        help=f"Persistent browser profile dir. Default: {DEFAULT_PROFILE}",
    )
    parser.add_argument("--timeout", type=int, default=60_000, help="Navigation timeout in ms.")
    parser.add_argument("--dry-run", action="store_true", help="Print result without modifying CSV.")
    return parser.parse_args()


def is_challenge(text: str) -> bool:
    lower = text.lower()
    return any(marker in lower for marker in CHALLENGE_MARKERS)


def normalize_lun_url(url: str, page_url: str, index: int) -> str:
    if not url:
        return f"{page_url}#lun-{index}"
    if url.startswith("/"):
        return "https://lun.ua" + url
    return url


def candidate_text_blocks(body_text: str) -> list[str]:
    chunks = []
    for chunk in re.split(r"\n\s*Поскаржитися\s*\n", body_text):
        chunk = clean_text(chunk)
        if "м²" not in chunk or "поверх" not in chunk or "кімнат" not in chunk:
            continue
        if not re.search(r"(?:грн|\$)", chunk):
            continue
        chunks.append(chunk)
    return chunks


def address_from_block(block: str) -> str:
    address_patterns = [
        r"((?:вул\.?|вулиця|проспект|просп\.?|провулок|пров\.?|бульвар|бульв\.?|шосе|узвіз|площа)\s+[^,|]{3,80})",
        r"([А-ЯІЇЄҐ][А-Яа-яІіЇїЄєҐґ'’ʼ\-\s]{3,60}\s+(?:вулиця|проспект|провулок|бульвар|шосе),?\s*\d+[А-Яа-яA-Za-zА-ЯІЇЄҐ\-\/]*)",
        r"(ЖК\s+[А-ЯІЇЄҐA-Z][^,|]{2,60})",
        r"(ж/м\s+[А-ЯІЇЄҐA-Z][^,|]{2,60})",
    ]
    for pattern in address_patterns:
        matches = re.findall(pattern, block, flags=re.IGNORECASE)
        if matches:
            return clean_text(matches[-1]).strip(" ,.;:-")
    return ""


def title_from_block(block: str, address: str) -> str:
    if address:
        return address
    match = re.search(r"((?:\d+|[1-5])\s+кімнат[аи]?\s+[^.]{0,80})", block, flags=re.IGNORECASE)
    if match:
        return clean_text(match.group(1))
    return "Продаж квартири LUN"


def extract_lun_rows_from_text(body_text: str, page: SearchPage, min_price: int, max_price: int, usd_uah_rate: float) -> list[dict[str, str]]:
    rows = []
    for index, block in enumerate(candidate_text_blocks(body_text), start=1):
        price = parse_price_usd(block, usd_uah_rate=usd_uah_rate)
        if price is None or price < min_price or price > max_price:
            continue

        address = address_from_block(block)
        title = title_from_block(block, address)
        rows.append(
            fill_common(
                page=page,
                url=normalize_lun_url("", page.url, index),
                price=price,
                title=title,
                text=block,
                address=address,
                description=block[:900],
            )
        )
    return rows


def extract_lun_rows_from_dom(page_obj, search_page: SearchPage, min_price: int, max_price: int, usd_uah_rate: float) -> list[dict[str, str]]:
    body_text = page_obj.locator("body").inner_text(timeout=10_000)
    rows = extract_lun_rows_from_text(body_text, search_page, min_price, max_price, usd_uah_rate)

    hrefs = page_obj.evaluate(
        """() => Array.from(document.querySelectorAll('a[href]'))
            .map(a => a.href)
            .filter(href => href.includes('/sale/') && !href.includes('/flats-45000-usd'))"""
    )
    for row, href in zip(rows, hrefs):
        row["url"] = normalize_lun_url(href, search_page.url, int(row.get("№") or 0))
    return rows


def collect_lun(args: argparse.Namespace) -> LunExtractResult:
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        print("Playwright is not installed. Run: python3 -m pip install playwright && python3 -m playwright install chromium")
        raise SystemExit(1) from error

    all_rows: list[dict[str, str]] = []
    blocked = False
    profile_dir = Path(args.profile_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        launch_kwargs = {
            "headless": not args.headful,
            "viewport": {"width": 1440, "height": 1000},
            "locale": "uk-UA",
        }
        try:
            context = playwright.chromium.launch_persistent_context(
                str(profile_dir),
                channel="chrome",
                **launch_kwargs,
            )
        except Exception:
            context = playwright.chromium.launch_persistent_context(str(profile_dir), **launch_kwargs)

        page_obj = context.pages[0] if context.pages else context.new_page()
        for search_page in LUN_PAGES:
            print(f"Fetching LUN via browser {search_page.city}: {search_page.url}")
            try:
                page_obj.goto(search_page.url, wait_until="domcontentloaded", timeout=args.timeout)
                page_obj.wait_for_timeout(6_000)
                body_text = page_obj.locator("body").inner_text(timeout=10_000)
            except PlaywrightTimeoutError as error:
                print(f"  skipped: browser timeout: {error}")
                continue

            if is_challenge(body_text) and args.manual_wait:
                print(f"  Cloudflare challenge detected. Waiting {args.manual_wait}s for manual verification...")
                page_obj.wait_for_timeout(args.manual_wait * 1000)
                body_text = page_obj.locator("body").inner_text(timeout=10_000)

            if is_challenge(body_text):
                print("  skipped: Cloudflare challenge is still active")
                blocked = True
                continue

            rows = extract_lun_rows_from_dom(page_obj, search_page, args.min_price, args.max_price, args.usd_uah_rate)
            all_rows.extend(rows)
            print(f"  extracted={len(rows)}")

        context.close()

    return LunExtractResult(rows=all_rows, blocked=blocked)


def read_existing_rows(csv_path: Path) -> list[dict[str, str]]:
    if not csv_path.exists():
        return []
    with csv_path.open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def write_rows(csv_path: Path, rows: list[dict[str, str]]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["№", *CSV_COLUMNS] if rows and "№" in rows[0] else CSV_COLUMNS
    with csv_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def append_lun_rows(csv_path: Path, lun_rows: list[dict[str, str]]) -> int:
    existing_rows = read_existing_rows(csv_path)
    existing_urls = {row.get("url", "") for row in existing_rows if row.get("url")}
    appended = 0

    for row in lun_rows:
        if row.get("url") in existing_urls:
            continue
        if "№" in existing_rows[0] if existing_rows else False:
            row["№"] = ""
        existing_rows.append(row)
        existing_urls.add(row.get("url", ""))
        appended += 1

    write_rows(csv_path, existing_rows)
    return appended


def main() -> None:
    args = parse_args()
    result = collect_lun(args)
    csv_path = Path(args.csv)

    if args.dry_run:
        print(f"LUN dry run: extracted={len(result.rows)}, blocked={result.blocked}")
        return

    appended = append_lun_rows(csv_path, result.rows)
    print(f"LUN browser collection: extracted={len(result.rows)}, appended={appended}, blocked={result.blocked}")


if __name__ == "__main__":
    main()
