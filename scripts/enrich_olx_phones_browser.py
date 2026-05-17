#!/usr/bin/env python3
"""Fill OLX phone numbers through a real browser session.

The HTTP-only OLX endpoint often returns "suspicious activity" for direct
requests. This script opens the listing page, uses the page's own "show phone"
flow, captures the official limited-phones response, and writes discovered
numbers back to the CSV.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import time
from pathlib import Path
from typing import Any

import fill_phone_repair


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "data/apartments_multi_source.csv"
DEFAULT_OUTPUT = PROJECT_ROOT / "data/apartments_multi_source.csv"
DEFAULT_PROFILE = PROJECT_ROOT / ".playwright/olx-profile"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fill OLX phones using Playwright browser flow.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help=f"Input CSV. Default: {DEFAULT_INPUT}")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help=f"Output CSV. Default: {DEFAULT_OUTPUT}")
    parser.add_argument("--max-rows", type=int, default=0, help="Limit OLX rows to process. 0 means no limit.")
    parser.add_argument("--sleep", type=float, default=5.0, help="Pause between listings. Default: 5.0.")
    parser.add_argument(
        "--phone-wait",
        type=float,
        default=12.0,
        help="Seconds to wait after clicking the OLX show-phone button. Default: 12.0.",
    )
    parser.add_argument("--timeout", type=int, default=45_000, help="Page timeout in ms. Default: 45000.")
    parser.add_argument("--headless", action="store_true", help="Run browser without visible window.")
    parser.add_argument(
        "--manual-wait",
        type=int,
        default=90,
        help="Seconds to wait if OLX asks for manual verification. Default: 90.",
    )
    parser.add_argument("--profile-dir", default=str(DEFAULT_PROFILE), help=f"Browser profile. Default: {DEFAULT_PROFILE}")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing phone values.")
    parser.add_argument("--dry-run", action="store_true", help="Do not write CSV.")
    return parser.parse_args()


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def append_phone(phones: list[str], raw_phone: str) -> None:
    phone = fill_phone_repair.normalize_phone(raw_phone)
    if phone and phone not in phones:
        phones.append(phone)


def extract_phones_from_payload(payload: Any) -> list[str]:
    phones: list[str] = []
    if isinstance(payload, dict):
        data = payload.get("data")
        raw_phones = data.get("phones") if isinstance(data, dict) else None
        if isinstance(raw_phones, list):
            for raw_phone in raw_phones:
                append_phone(phones, str(raw_phone))
    return phones


def extract_visible_phones(text: str) -> list[str]:
    phones: list[str] = []
    for phone in fill_phone_repair.extract_phone(text).split(";"):
        append_phone(phones, phone)
    return phones


def is_blocked_text(text: str) -> bool:
    lower = text.lower()
    markers = [
        "підозрілу активність",
        "suspicious activity",
        "captcha",
        "перевірте, що ви не робот",
        "verify you are human",
    ]
    return any(marker in lower for marker in markers)


def log(message: str) -> None:
    print(message, flush=True)


def read_rows(input_path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with input_path.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        rows = list(reader)
        fieldnames = fill_phone_repair.ensure_columns(reader.fieldnames or [])
    return rows, fieldnames


def write_rows(output_path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def candidate_rows(rows: list[dict[str, str]], overwrite: bool) -> list[dict[str, str]]:
    result = []
    for row in rows:
        if row.get("source") != "OLX":
            continue
        if not row.get("url"):
            continue
        if not overwrite and (row.get("телефон") or "").strip():
            continue
        result.append(row)
    return result


def click_show_phone(page: Any) -> bool:
    selectors = [
        '[data-testid="show-phone"]',
        '[data-testid="ad-contact-phone"]',
        'button:has-text("Показати")',
        'button:has-text("Показать")',
        'button:has-text("Show")',
        '[data-testid="contact-phone"]',
    ]
    for selector in selectors:
        try:
            locators = page.locator(selector)
            for index in range(locators.count()):
                locator = locators.nth(index)
                if locator.is_visible(timeout=1_000):
                    locator.click(timeout=5_000)
                    return True
        except Exception:
            continue

    try:
        return bool(
            page.evaluate(
                """() => {
                    const nodes = Array.from(document.querySelectorAll('button, [role="button"], a, div'));
                    const node = nodes.find((el) => /показати|показать|show/i.test(el.innerText || ''));
                    if (!node) return false;
                    node.click();
                    return true;
                }"""
            )
        )
    except Exception:
        return False


def extract_one(
    page: Any,
    row: dict[str, str],
    timeout: int,
    manual_wait: int,
    phone_wait: float,
) -> tuple[list[str], str]:
    phones: list[str] = []
    notes: list[str] = []

    def on_response(response: Any) -> None:
        if "/limited-phones/" not in response.url:
            return
        try:
            payload = response.json()
        except Exception as error:
            notes.append(f"limited-phones json error: {error}")
            return
        for phone in extract_phones_from_payload(payload):
            append_phone(phones, phone)
        if not phones and isinstance(payload, dict) and payload.get("error"):
            detail = payload.get("error", {}).get("detail") or payload.get("error", {}).get("title")
            if detail:
                notes.append(clean_text(str(detail)))

    page.on("response", on_response)
    try:
        page.goto(row["url"], wait_until="domcontentloaded", timeout=timeout)
        page.wait_for_timeout(2_000)
        body_text = page.locator("body").inner_text(timeout=10_000)

        if is_blocked_text(body_text) and manual_wait:
            log(f"  OLX verification detected. Waiting {manual_wait}s for manual action in browser...")
            page.wait_for_timeout(manual_wait * 1000)
            body_text = page.locator("body").inner_text(timeout=10_000)

        for phone in extract_visible_phones(body_text):
            append_phone(phones, phone)

        if phones:
            return phones, "; ".join(notes)

        clicked = click_show_phone(page)
        if not clicked:
            notes.append("show phone button not found")

        page.wait_for_timeout(int(phone_wait * 1000))
        body_text = page.locator("body").inner_text(timeout=10_000)
        for phone in extract_visible_phones(body_text):
            append_phone(phones, phone)
    finally:
        page.remove_listener("response", on_response)

    return phones, "; ".join(dict.fromkeys(notes))


def enrich_olx_phones_browser(args: argparse.Namespace) -> dict[str, Any]:
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        log("Playwright is not installed. Run: python3 -m pip install playwright && python3 -m playwright install chromium")
        raise SystemExit(1) from error

    input_path = Path(args.input)
    output_path = Path(args.output)
    rows, fieldnames = read_rows(input_path)
    candidates = candidate_rows(rows, args.overwrite)
    if args.max_rows:
        candidates = candidates[: args.max_rows]

    updated = 0
    failed = 0
    skipped = 0
    profile_dir = Path(args.profile_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        launch_kwargs = {
            "headless": args.headless,
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

        page = context.pages[0] if context.pages else context.new_page()
        for row in candidates:
            log(f"OLX phone #{row.get('№')}: {row.get('url')}")
            try:
                phones, note = extract_one(page, row, args.timeout, args.manual_wait, args.phone_wait)
            except PlaywrightTimeoutError as error:
                failed += 1
                log(f"  timeout: {error}")
                continue
            except Exception as error:
                failed += 1
                log(f"  failed: {error}")
                continue

            if phones:
                row["телефон"] = "; ".join(phones)
                updated += 1
                log(f"  phone: {row['телефон']}")
                if not args.dry_run:
                    write_rows(output_path, rows, fieldnames)
            else:
                skipped += 1
                log(f"  no phone{': ' + note if note else ''}")

            time.sleep(args.sleep)

        context.close()

    if not args.dry_run:
        write_rows(output_path, rows, fieldnames)

    return {
        "processed": len(candidates),
        "updated": updated,
        "failed": failed,
        "no_phone": skipped,
        "output": str(output_path),
        "dry_run": args.dry_run,
    }


def main() -> None:
    args = parse_args()
    result = enrich_olx_phones_browser(args)
    log(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
