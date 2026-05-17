#!/usr/bin/env python3
"""Single entrypoint for the apartment collection pipeline.

Pipeline:
1. Collect listings from LUN, DIM.RIA, OLX, and REM.
2. Fill address hints from listing URLs.
3. Train the local address extraction model.
4. Apply the model to fill missing or weak addresses.
5. Optionally sync the CSV to Google Sheets.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
DEFAULT_CSV = PROJECT_ROOT / "data/apartments_multi_source.csv"
DEFAULT_MODEL = PROJECT_ROOT / "models/address_extractor.json"
DEFAULT_ROOMS_MODEL = PROJECT_ROOT / "models/rooms_extractor.json"
DEFAULT_FLOOR_MODEL = PROJECT_ROOT / "models/floor_extractor.json"
DEFAULT_SOURCES = "REM.ua,DIM.RIA,OLX,LUN"
DEFAULT_REPLACE_ADDRESS_SOURCES = "OLX"
DEFAULT_SPREADSHEET = os.getenv("GOOGLE_SPREADSHEET_ID", "")
DEFAULT_SHEET = os.getenv("GOOGLE_SHEET_NAME", "Аркуш1")
DEFAULT_SERVICE_ACCOUNT = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the full apartment CSV pipeline.")
    parser.add_argument("--csv", default=str(DEFAULT_CSV), help=f"Output/input CSV. Default: {DEFAULT_CSV}")
    parser.add_argument("--model", default=str(DEFAULT_MODEL), help=f"Address model JSON. Default: {DEFAULT_MODEL}")
    parser.add_argument("--rooms-model", default=str(DEFAULT_ROOMS_MODEL), help=f"Rooms model JSON. Default: {DEFAULT_ROOMS_MODEL}")
    parser.add_argument("--floor-model", default=str(DEFAULT_FLOOR_MODEL), help=f"Floor model JSON. Default: {DEFAULT_FLOOR_MODEL}")
    parser.add_argument("--min-price", type=int, default=35_000, help="Minimum listing price in USD.")
    parser.add_argument("--max-price", type=int, default=45_000, help="Maximum listing price in USD.")
    parser.add_argument("--max-pages", type=int, default=1, help="Pages to request per source.")
    parser.add_argument("--sleep", type=float, default=1.0, help="Pause between listing requests in seconds.")
    parser.add_argument("--usd-uah-rate", type=float, default=44.15, help="Fallback UAH to USD conversion rate.")
    parser.add_argument("--sources", default=DEFAULT_SOURCES, help="Comma-separated sale sources to collect.")
    parser.add_argument(
        "--replace-address-sources",
        default=DEFAULT_REPLACE_ADDRESS_SOURCES,
        help="Comma-separated sources whose URL-derived addresses should be recalculated.",
    )
    parser.add_argument("--min-address-score", type=float, default=2.2, help="Minimum address model confidence.")
    parser.add_argument("--min-rooms-score", type=float, default=1.6, help="Minimum rooms model confidence.")
    parser.add_argument("--min-floor-score", type=float, default=1.7, help="Minimum floor model confidence.")
    parser.add_argument("--skip-collect", action="store_true", help="Do not fetch listings; reuse current CSV.")
    parser.add_argument("--skip-lun-browser", action="store_true", help="Do not try browser-based LUN collection.")
    parser.add_argument("--lun-headful", action="store_true", help="Run LUN Playwright collection in a visible browser.")
    parser.add_argument(
        "--lun-manual-wait",
        type=int,
        default=0,
        help="Seconds to wait for manual Cloudflare verification during LUN browser collection.",
    )
    parser.add_argument("--skip-url-address", action="store_true", help="Do not fill addresses from URLs.")
    parser.add_argument("--skip-train", action="store_true", help="Do not retrain address model.")
    parser.add_argument("--skip-apply-address", action="store_true", help="Do not apply address model.")
    parser.add_argument("--skip-train-rooms", action="store_true", help="Do not retrain rooms model.")
    parser.add_argument("--skip-apply-rooms", action="store_true", help="Do not apply rooms model.")
    parser.add_argument("--skip-train-floor", action="store_true", help="Do not retrain floor model.")
    parser.add_argument("--skip-apply-floor", action="store_true", help="Do not apply floor model.")
    parser.add_argument("--skip-olx-floor-details", action="store_true", help="Do not fetch OLX detail pages for floor data.")
    parser.add_argument("--olx-detail-sleep", type=float, default=0.4, help="Pause between OLX detail requests.")
    parser.add_argument("--skip-google-maps-url", action="store_true", help="Do not fill Google Maps search URLs.")
    parser.add_argument("--overwrite-google-maps-url", action="store_true", help="Regenerate existing Google Maps URLs too.")
    parser.add_argument("--skip-distance-to-center", action="store_true", help="Do not fill distance to city center.")
    parser.add_argument("--no-distance-geocode", action="store_true", help="Calculate distance only from existing coordinate URLs.")
    parser.add_argument("--distance-geocode-sleep", type=float, default=1.0, help="Pause between geocoding requests.")
    parser.add_argument("--skip-phone-repair", action="store_true", help="Do not fill phone and repair columns.")
    parser.add_argument("--skip-publication-date-details", action="store_true", help="Do not fetch detail pages for publication dates.")
    parser.add_argument("--publication-date-detail-sleep", type=float, default=0.4, help="Pause between publication detail requests.")
    parser.add_argument("--skip-normalize-date", action="store_true", help="Do not normalize publication dates.")
    parser.add_argument(
        "--date-reference",
        default="",
        help="Reference date for relative publication dates in YYYY-MM-DD. Default: today.",
    )
    parser.add_argument(
        "--date-fallback-reference",
        action="store_true",
        help="Use date reference at midnight when publication date cannot be parsed.",
    )
    parser.add_argument("--sync", action="store_true", help="Sync final CSV to Google Sheets.")
    parser.add_argument("--only-sync", action="store_true", help="Only sync current CSV to Google Sheets.")
    parser.add_argument("--spreadsheet", default=DEFAULT_SPREADSHEET, help="Google Sheets URL or spreadsheet ID.")
    parser.add_argument("--sheet", default=DEFAULT_SHEET, help=f"Google Sheets tab. Default: {DEFAULT_SHEET}")
    parser.add_argument(
        "--service-account",
        default=DEFAULT_SERVICE_ACCOUNT,
        help="Google service account JSON path. Can also be set via GOOGLE_SERVICE_ACCOUNT_JSON.",
    )
    return parser.parse_args()


def run_script(module_name: str, argv: list[str]) -> None:
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))

    module = __import__(module_name)
    previous_argv = sys.argv[:]
    sys.argv = [f"{module_name}.py", *argv]
    try:
        module.main()
    finally:
        sys.argv = previous_argv


def collect(args: argparse.Namespace) -> None:
    sources = args.sources
    if not args.skip_lun_browser:
        sources = ",".join(source for source in (item.strip() for item in args.sources.split(",")) if source and source != "LUN")
    run_script(
        "collect_apartments_multi_source",
        [
            "--output",
            args.csv,
            "--min-price",
            str(args.min_price),
            "--max-price",
            str(args.max_price),
            "--max-pages",
            str(args.max_pages),
            "--sleep",
            str(args.sleep),
            "--usd-uah-rate",
            str(args.usd_uah_rate),
            "--sources",
            sources,
        ],
    )


def collect_lun_browser(args: argparse.Namespace) -> None:
    argv = [
        "--csv",
        args.csv,
        "--min-price",
        str(args.min_price),
        "--max-price",
        str(args.max_price),
        "--usd-uah-rate",
        str(args.usd_uah_rate),
    ]
    if args.lun_headful:
        argv.append("--headful")
    if args.lun_manual_wait:
        argv.extend(["--manual-wait", str(args.lun_manual_wait)])
    run_script("collect_lun_playwright", argv)


def fill_address_from_url(args: argparse.Namespace) -> None:
    run_script(
        "fill_address_from_url",
        [
            "--input",
            args.csv,
            "--output",
            args.csv,
            "--replace-sources",
            args.replace_address_sources,
        ],
    )


def train_address_model(args: argparse.Namespace) -> None:
    run_script(
        "train_address_model",
        [
            "--input",
            args.csv,
            "--model",
            args.model,
        ],
    )


def apply_address_model(args: argparse.Namespace) -> None:
    run_script(
        "apply_address_model",
        [
            "--input",
            args.csv,
            "--output",
            args.csv,
            "--model",
            args.model,
            "--min-score",
            str(args.min_address_score),
        ],
    )


def train_rooms_model(args: argparse.Namespace) -> None:
    run_script(
        "train_rooms_model",
        [
            "--input",
            args.csv,
            "--model",
            args.rooms_model,
        ],
    )


def apply_rooms_model(args: argparse.Namespace) -> None:
    run_script(
        "apply_rooms_model",
        [
            "--input",
            args.csv,
            "--output",
            args.csv,
            "--model",
            args.rooms_model,
            "--min-score",
            str(args.min_rooms_score),
        ],
    )


def train_floor_model(args: argparse.Namespace) -> None:
    run_script(
        "train_floor_model",
        [
            "--input",
            args.csv,
            "--model",
            args.floor_model,
        ],
    )


def enrich_olx_floor_details(args: argparse.Namespace) -> None:
    run_script(
        "enrich_olx_floor_details",
        [
            "--input",
            args.csv,
            "--output",
            args.csv,
            "--sleep",
            str(args.olx_detail_sleep),
        ],
    )


def apply_floor_model(args: argparse.Namespace) -> None:
    run_script(
        "apply_floor_model",
        [
            "--input",
            args.csv,
            "--output",
            args.csv,
            "--model",
            args.floor_model,
            "--min-score",
            str(args.min_floor_score),
        ],
    )


def fill_google_maps_url(args: argparse.Namespace) -> None:
    argv = [
        "--input",
        args.csv,
        "--output",
        args.csv,
    ]
    if args.overwrite_google_maps_url:
        argv.append("--overwrite")
    run_script("fill_google_maps_url", argv)


def fill_distance_to_center(args: argparse.Namespace) -> None:
    argv = [
        "--input",
        args.csv,
        "--output",
        args.csv,
        "--sleep",
        str(args.distance_geocode_sleep),
    ]
    if args.no_distance_geocode:
        argv.append("--no-geocode")
    run_script("fill_distance_to_center", argv)


def fill_phone_repair(args: argparse.Namespace) -> None:
    run_script(
        "fill_phone_repair",
        [
            "--input",
            args.csv,
            "--output",
            args.csv,
        ],
    )


def enrich_publication_date_details(args: argparse.Namespace) -> None:
    run_script(
        "enrich_publication_date_details",
        [
            "--input",
            args.csv,
            "--output",
            args.csv,
            "--sleep",
            str(args.publication_date_detail_sleep),
        ],
    )


def normalize_publication_date(args: argparse.Namespace) -> None:
    argv = [
        "--input",
        args.csv,
        "--output",
        args.csv,
    ]
    if args.date_reference:
        argv.extend(["--reference-date", args.date_reference])
    if args.date_fallback_reference:
        argv.append("--fallback-reference-date")
    run_script("normalize_publication_date", argv)


def sync_to_google_sheets(args: argparse.Namespace) -> None:
    run_script(
        "sync_to_google_sheets",
        [
            "--csv",
            args.csv,
            "--spreadsheet",
            args.spreadsheet,
            "--sheet",
            args.sheet,
            "--service-account",
            args.service_account,
        ],
    )


def number_rows(args: argparse.Namespace) -> None:
    csv_path = Path(args.csv)
    with csv_path.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        rows = list(reader)
        fieldnames = reader.fieldnames or []

    if not fieldnames:
        return

    fieldnames = ["№", *[fieldname for fieldname in fieldnames if fieldname != "№"]]
    for index, row in enumerate(rows, start=1):
        row["№"] = str(index)

    with csv_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Numbered {len(rows)} apartment options in {csv_path}")


def main() -> None:
    args = parse_args()

    if args.only_sync:
        sync_to_google_sheets(args)
        return

    if not args.skip_collect:
        collect(args)
    if not args.skip_collect and not args.skip_lun_browser and "LUN" in {source.strip() for source in args.sources.split(",")}:
        collect_lun_browser(args)
    if not args.skip_url_address:
        fill_address_from_url(args)
    if not args.skip_train:
        train_address_model(args)
    if not args.skip_apply_address:
        apply_address_model(args)
    if not args.skip_train_rooms:
        train_rooms_model(args)
    if not args.skip_apply_rooms:
        apply_rooms_model(args)
    if not args.skip_olx_floor_details:
        enrich_olx_floor_details(args)
    if not args.skip_train_floor:
        train_floor_model(args)
    if not args.skip_apply_floor:
        apply_floor_model(args)
    if not args.skip_google_maps_url:
        fill_google_maps_url(args)
    if not args.skip_distance_to_center:
        fill_distance_to_center(args)
    if not args.skip_phone_repair:
        fill_phone_repair(args)
    if not args.skip_publication_date_details:
        enrich_publication_date_details(args)
    if not args.skip_normalize_date:
        normalize_publication_date(args)
    number_rows(args)
    if args.sync:
        sync_to_google_sheets(args)


if __name__ == "__main__":
    main()
