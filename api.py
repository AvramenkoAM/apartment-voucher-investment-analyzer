#!/usr/bin/env python3
"""Local JSON API for apartment sources and collected listings."""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import requests


PROJECT_ROOT = Path(__file__).resolve().parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
DEFAULT_CSV = PROJECT_ROOT / "data/apartments_multi_source.csv"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import collect_apartments_multi_source as collector  # noqa: E402
import enrich_phone_details as phone_details  # noqa: E402
import fill_phone_repair as phone_tools  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run local apartment source API.")
    parser.add_argument("--host", default="127.0.0.1", help="API host. Default: 127.0.0.1")
    parser.add_argument("--port", type=int, default=8000, help="API port. Default: 8000")
    parser.add_argument("--csv", default=str(DEFAULT_CSV), help=f"CSV file. Default: {DEFAULT_CSV}")
    return parser.parse_args()


def json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def split_csv(value: str | None) -> set[str]:
    return {item.strip() for item in (value or "").split(",") if item.strip()}


def int_param(params: dict[str, list[str]], name: str, default: int) -> int:
    raw = params.get(name, [""])[0]
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def float_param(params: dict[str, list[str]], name: str, default: float) -> float:
    raw = params.get(name, [""])[0]
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def bool_param(params: dict[str, list[str]], name: str, default: bool = False) -> bool:
    raw = params.get(name, [""])[0].strip().lower()
    if raw in {"1", "true", "yes", "y", "так"}:
        return True
    if raw in {"0", "false", "no", "n", "ні"}:
        return False
    return default


def source_rows() -> list[dict[str, Any]]:
    rows = []
    for page in collector.SEARCH_PAGES:
        rows.append(
            {
                "source": page.source,
                "city": page.city,
                "url": page.url,
                "base_url": page.base_url,
                "collectable_by_http": page.extractor is not collector.extract_blocked,
            }
        )
    return rows


def source_summary() -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in source_rows():
        source = row["source"]
        item = grouped.setdefault(
            source,
            {
                "source": source,
                "cities": [],
                "urls": {},
                "collectable_by_http": False,
            },
        )
        item["cities"].append(row["city"])
        item["urls"][row["city"]] = row["url"]
        item["collectable_by_http"] = item["collectable_by_http"] or row["collectable_by_http"]
    return list(grouped.values())


def read_listings(csv_path: Path) -> list[dict[str, str]]:
    if not csv_path.exists():
        return []
    with csv_path.open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def read_csv_with_fieldnames(csv_path: Path) -> tuple[list[dict[str, str]], list[str]]:
    if not csv_path.exists():
        return [], []
    with csv_path.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        return list(reader), reader.fieldnames or []


def write_listings(csv_path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def filtered_listings(csv_path: Path, params: dict[str, list[str]]) -> list[dict[str, str]]:
    listings = read_listings(csv_path)
    sources = split_csv(params.get("source", [""])[0])
    cities = split_csv(params.get("city", [""])[0])
    min_price = int_param(params, "min_price", 0)
    max_price = int_param(params, "max_price", 0)
    limit = int_param(params, "limit", 0)

    filtered = []
    for row in listings:
        if sources and row.get("source") not in sources:
            continue
        if cities and row.get("city") not in cities:
            continue
        try:
            price = int(row.get("price") or 0)
        except ValueError:
            price = 0
        if min_price and price < min_price:
            continue
        if max_price and price > max_price:
            continue
        filtered.append(row)
        if limit and len(filtered) >= limit:
            break
    return filtered


def missing_phone_listings(csv_path: Path, params: dict[str, list[str]]) -> list[dict[str, str]]:
    params = dict(params)
    rows = filtered_listings(csv_path, params)
    limit = int_param(params, "limit", 0)
    missing = [row for row in rows if not (row.get("телефон") or "").strip()]
    return missing[:limit] if limit else missing


def normalize_phone_values(value: Any) -> str:
    if isinstance(value, list):
        raw_values = [str(item) for item in value]
    else:
        raw_values = re_split_phones(str(value or ""))

    phones = []
    for raw_phone in raw_values:
        phone = phone_tools.normalize_phone(raw_phone)
        if phone and phone not in phones:
            phones.append(phone)
    return "; ".join(phones)


def re_split_phones(value: str) -> list[str]:
    return [item.strip() for item in value.replace(",", ";").split(";") if item.strip()]


def update_phone_values(csv_path: Path, updates: list[dict[str, Any]], overwrite: bool) -> dict[str, Any]:
    rows, fieldnames = read_csv_with_fieldnames(csv_path)
    fieldnames = phone_tools.ensure_columns(fieldnames)
    by_number = {row.get("№"): row for row in rows if row.get("№")}
    by_url = {row.get("url"): row for row in rows if row.get("url")}

    updated = []
    skipped = []
    for update in updates:
        row = None
        row_id = str(update.get("№") or update.get("number") or "").strip()
        url = str(update.get("url") or "").strip()
        if row_id:
            row = by_number.get(row_id)
        if row is None and url:
            row = by_url.get(url)
        if row is None:
            skipped.append({"update": update, "reason": "row not found"})
            continue

        phone = normalize_phone_values(update.get("телефон") or update.get("phone"))
        if not phone:
            skipped.append({"update": update, "reason": "invalid phone"})
            continue
        if row.get("телефон", "").strip() and not overwrite:
            skipped.append({"№": row.get("№"), "url": row.get("url"), "reason": "phone already exists"})
            continue

        row["телефон"] = phone
        updated.append({"№": row.get("№"), "url": row.get("url"), "телефон": phone})

    write_listings(csv_path, rows, fieldnames)
    return {"updated_count": len(updated), "skipped_count": len(skipped), "updated": updated, "skipped": skipped}


def extract_phone_values(csv_path: Path, overwrite: bool) -> dict[str, Any]:
    rows, fieldnames = read_csv_with_fieldnames(csv_path)
    fieldnames = phone_tools.ensure_columns(fieldnames)
    updated = []
    for row in rows:
        if row.get("телефон", "").strip() and not overwrite:
            continue
        text = phone_tools.clean_text(" ".join([row.get("опис", ""), row.get("url", "")]))
        phone = phone_tools.extract_phone(text)
        if not phone:
            row.setdefault("телефон", "")
            continue
        row["телефон"] = phone
        updated.append({"№": row.get("№"), "url": row.get("url"), "телефон": phone})

    write_listings(csv_path, rows, fieldnames)
    return {"updated_count": len(updated), "updated": updated}


def make_source_filter(sources: set[str]) -> set[str]:
    known_sources = {row["source"] for row in source_rows()}
    return sources & known_sources if sources else set()


def collect_selected(
    *,
    csv_path: Path,
    sources: set[str],
    cities: set[str],
    min_price: int,
    max_price: int,
    max_pages: int,
    sleep_seconds: float,
    usd_uah_rate: float,
) -> tuple[list[dict[str, str]], list[str]]:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "uk-UA,uk;q=0.9,en;q=0.8",
        }
    )

    rows: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    warnings: list[str] = []
    source_filter = make_source_filter(sources)

    for search_page in collector.SEARCH_PAGES:
        if source_filter and search_page.source not in source_filter:
            continue
        if cities and search_page.city not in cities:
            continue
        if search_page.extractor is collector.extract_blocked:
            warnings.append(f"{search_page.source} {search_page.city}: skipped because HTTP collection is blocked.")
            continue

        for page_number in range(1, max_pages + 1):
            page_url = collector.build_page_url(search_page.url, page_number)
            try:
                html = collector.fetch_html(session, page_url)
            except requests.RequestException as error:
                warnings.append(f"{search_page.source} {search_page.city} page {page_number}: request failed: {error}")
                continue
            if collector.is_cloudflare_challenge(html):
                warnings.append(f"{search_page.source} {search_page.city} page {page_number}: Cloudflare/JS challenge.")
                continue

            extracted = search_page.extractor(html, search_page, usd_uah_rate)
            for row in extracted:
                listing_url = row["url"]
                try:
                    price = int(row["price"])
                except ValueError:
                    continue
                if price < min_price or price > max_price or listing_url in seen_urls:
                    continue
                rows.append(row)
                seen_urls.add(listing_url)
            time.sleep(sleep_seconds)

    collector.write_csv(rows, csv_path)
    return rows, warnings


class ApartmentApiHandler(BaseHTTPRequestHandler):
    csv_path = DEFAULT_CSV

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))

    def send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json_bytes(payload)
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        if not raw.strip():
            return {}
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("JSON body must be an object.")
        return parsed

    def do_OPTIONS(self) -> None:
        self.send_json({"ok": True})

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        path = parsed.path.rstrip("/") or "/"

        if path == "/health":
            self.send_json({"ok": True})
            return

        if path == "/api/sources":
            self.send_json({"sources": source_summary()})
            return

        if path.startswith("/api/sources/"):
            source_name = unquote(path.removeprefix("/api/sources/"))
            rows = [row for row in source_rows() if row["source"].lower() == source_name.lower()]
            if not rows:
                self.send_json({"error": f"Unknown source: {source_name}"}, HTTPStatus.NOT_FOUND)
                return
            self.send_json({"source": source_name, "pages": rows})
            return

        if path == "/api/listings":
            listings = filtered_listings(Path(self.csv_path), params)
            self.send_json({"count": len(listings), "listings": listings})
            return

        if path == "/api/phones/missing":
            listings = missing_phone_listings(Path(self.csv_path), params)
            self.send_json({"count": len(listings), "listings": listings})
            return

        self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path == "/api/phones/extract":
            try:
                payload = self.read_json_body()
                overwrite = bool(payload.get("overwrite", False))
            except (ValueError, json.JSONDecodeError) as error:
                self.send_json({"error": f"Invalid request body: {error}"}, HTTPStatus.BAD_REQUEST)
                return
            result = extract_phone_values(Path(self.csv_path), overwrite=overwrite)
            self.send_json(result)
            return

        if path == "/api/phones/enrich":
            try:
                payload = self.read_json_body()
                overwrite = bool(payload.get("overwrite", False))
                max_rows = int(payload.get("max_rows", 0))
                sleep_seconds = float(payload.get("sleep", 0.4))
                olx_phone_delay = float(payload.get("olx_phone_delay", 1.2))
                sources = set(payload.get("sources") or [])
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                self.send_json({"error": f"Invalid request body: {error}"}, HTTPStatus.BAD_REQUEST)
                return

            try:
                result = phone_details.enrich_phone_details(
                    Path(self.csv_path),
                    Path(self.csv_path),
                    sleep_seconds=sleep_seconds,
                    max_rows=max_rows,
                    overwrite=overwrite,
                    sources=sources,
                    olx_phone_delay=olx_phone_delay,
                )
            except OSError as error:
                self.send_json({"error": f"Cannot update CSV: {error}"}, HTTPStatus.INTERNAL_SERVER_ERROR)
                return

            self.send_json(result)
            return

        if path == "/api/phones":
            try:
                payload = self.read_json_body()
                updates = payload.get("updates")
                if updates is None:
                    updates = [payload]
                if not isinstance(updates, list):
                    raise ValueError("updates must be a list.")
                overwrite = bool(payload.get("overwrite", False))
            except (ValueError, json.JSONDecodeError) as error:
                self.send_json({"error": f"Invalid request body: {error}"}, HTTPStatus.BAD_REQUEST)
                return
            result = update_phone_values(Path(self.csv_path), updates=updates, overwrite=overwrite)
            self.send_json(result)
            return

        if path != "/api/collect":
            self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return

        try:
            payload = self.read_json_body()
            sources = set(payload.get("sources") or [])
            cities = set(payload.get("cities") or [])
            min_price = int(payload.get("min_price", collector.DEFAULT_MIN_PRICE_USD))
            max_price = int(payload.get("max_price", collector.DEFAULT_MAX_PRICE_USD))
            max_pages = int(payload.get("max_pages", 1))
            sleep_seconds = float(payload.get("sleep", 1.0))
            usd_uah_rate = float(payload.get("usd_uah_rate", collector.DEFAULT_USD_UAH_RATE))
            overwrite_csv = bool(payload.get("overwrite_csv", True))
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            self.send_json({"error": f"Invalid request body: {error}"}, HTTPStatus.BAD_REQUEST)
            return

        if not overwrite_csv:
            self.send_json({"error": "overwrite_csv=false is not supported yet."}, HTTPStatus.BAD_REQUEST)
            return

        started_at = time.time()
        rows, warnings = collect_selected(
            csv_path=Path(self.csv_path),
            sources=sources,
            cities=cities,
            min_price=min_price,
            max_price=max_price,
            max_pages=max_pages,
            sleep_seconds=sleep_seconds,
            usd_uah_rate=usd_uah_rate,
        )

        self.send_json(
            {
                "count": len(rows),
                "csv": str(self.csv_path),
                "elapsed_seconds": round(time.time() - started_at, 2),
                "warnings": warnings,
            }
        )


def main() -> None:
    args = parse_args()
    ApartmentApiHandler.csv_path = Path(args.csv)
    server = ThreadingHTTPServer((args.host, args.port), ApartmentApiHandler)
    print(f"Apartment source API: http://{args.host}:{args.port}")
    print("Endpoints: GET /api/sources, GET /api/listings, POST /api/collect")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping API server.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
