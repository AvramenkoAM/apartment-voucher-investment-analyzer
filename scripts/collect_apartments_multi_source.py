#!/usr/bin/env python3
"""Collect apartments from multiple listing sources.

Default price corridor: 35 000 - 45 000 USD.

Supported HTML extractors:
- REM.ua
- DIM.RIA
- OLX

LUN is included in the source list, but often returns a Cloudflare challenge to
plain HTTP clients. The script logs that instead of failing the whole run.
"""

from __future__ import annotations

import argparse
import csv
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup


CSV_COLUMNS = [
    "source",
    "url",
    "city",
    "price",
    "телефон",
    "adress",
    "площа",
    "кількість кімнат",
    "поверх",
    "поверховість",
    "балкон",
    "район",
    "км від центру",
    "url_google_maps",
    "дата публікації",
    "ремонт",
    "опис",
]

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "data/apartments_multi_source.csv"
DEFAULT_MIN_PRICE_USD = 35_000
DEFAULT_MAX_PRICE_USD = 45_000
DEFAULT_USD_UAH_RATE = 44.15

CITY_CENTERS = {
    "Одеса": (46.4825, 30.7233),
    "Дніпро": (48.4674, 35.0407),
}


@dataclass(frozen=True)
class SearchPage:
    source: str
    city: str
    url: str
    extractor: Callable[[str, "SearchPage", float], list[dict[str, str]]]
    base_url: str


def extract_blocked(*_args) -> list[dict[str, str]]:
    return []


SEARCH_PAGES = [
    SearchPage("REM.ua", "Одеса", "https://rem.ua/ua/prodazha-kvartir-odessa", lambda h, p, r: extract_rem(h, p), "https://rem.ua"),
    SearchPage("REM.ua", "Дніпро", "https://rem.ua/ua/prodazha-kvartir-dnepr", lambda h, p, r: extract_rem(h, p), "https://rem.ua"),
    SearchPage("DIM.RIA", "Одеса", "https://dom.ria.com/uk/prodazha-kvartir/odessa-45000usd/", lambda h, p, r: extract_dim_ria(h, p), "https://dom.ria.com"),
    SearchPage("DIM.RIA", "Дніпро", "https://dom.ria.com/uk/prodazha-kvartir/dnepr-45000usd/", lambda h, p, r: extract_dim_ria(h, p), "https://dom.ria.com"),
    SearchPage("OLX", "Одеса", "https://www.olx.ua/uk/nedvizhimost/kvartiry/prodazha-kvartir/odessa/?currency=USD&search%5Bfilter_float_price:from%5D=35000&search%5Bfilter_float_price:to%5D=45000", lambda h, p, r: extract_olx(h, p), "https://www.olx.ua"),
    SearchPage("OLX", "Дніпро", "https://www.olx.ua/uk/nedvizhimost/kvartiry/prodazha-kvartir/dnepr/?currency=USD&search%5Bfilter_float_price:from%5D=35000&search%5Bfilter_float_price:to%5D=45000", lambda h, p, r: extract_olx(h, p), "https://www.olx.ua"),
    SearchPage("LUN", "Одеса", "https://lun.ua/sale/odesa/flats-45000-usd", extract_blocked, "https://lun.ua"),
    SearchPage("LUN", "Дніпро", "https://lun.ua/sale/dnipro/flats-45000-usd", extract_blocked, "https://lun.ua"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect apartments from multiple sources.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help=f"Output CSV path. Default: {DEFAULT_OUTPUT}")
    parser.add_argument("--min-price", type=int, default=DEFAULT_MIN_PRICE_USD, help="Minimum price in USD.")
    parser.add_argument("--max-price", type=int, default=DEFAULT_MAX_PRICE_USD, help="Maximum price in USD.")
    parser.add_argument("--max-pages", type=int, default=1, help="How many pages to request per source.")
    parser.add_argument("--sleep", type=float, default=1.0, help="Pause between requests in seconds.")
    parser.add_argument("--usd-uah-rate", type=float, default=DEFAULT_USD_UAH_RATE, help="Fallback UAH to USD conversion rate.")
    parser.add_argument(
        "--sources",
        default="",
        help="Comma-separated source filter, for example: REM.ua,DIM.RIA,OLX. Empty means all.",
    )
    return parser.parse_args()


def clean_text(raw_text: str) -> str:
    return " ".join(raw_text.split())


def first_text(node, selector: str) -> str:
    selected = node.select_one(selector)
    return clean_text(selected.get_text(" ", strip=True)) if selected else ""


def empty_row() -> dict[str, str]:
    return {column: "" for column in CSV_COLUMNS}


def normalize_url(url: str, base_url: str) -> str:
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return base_url.rstrip("/") + url
    return url


def build_page_url(base_url: str, page_number: int) -> str:
    if page_number <= 1:
        return base_url
    parsed = urlparse(base_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["page"] = str(page_number)
    return urlunparse(parsed._replace(query=urlencode(query)))


def fetch_html(session: requests.Session, url: str) -> str:
    response = session.get(url, timeout=30)
    response.raise_for_status()
    return response.text


def is_cloudflare_challenge(html: str) -> bool:
    return "Just a moment..." in html and "challenge-platform" in html


def parse_price_usd(raw_text: str, usd_uah_rate: float = DEFAULT_USD_UAH_RATE) -> int | None:
    text = clean_text(raw_text)
    usd_match = re.search(r"(\d[\d\s]*)\s*(?:\$|у\.?о\.?|USD)", text, flags=re.IGNORECASE)
    if usd_match:
        return int(re.sub(r"\D", "", usd_match.group(1)))

    uah_match = re.search(r"(\d[\d\s]*)\s*(?:грн|uah)", text, flags=re.IGNORECASE)
    if uah_match and usd_uah_rate:
        uah = int(re.sub(r"\D", "", uah_match.group(1)))
        return round(uah / usd_uah_rate)

    return None


def parse_rooms(text: str) -> str:
    patterns = [
        r"(\d+)\s*-\s*комн",
        r"(\d+)\s*кімнат",
        r"(\d+)\s*кімната",
        r"(\d+)\s*к[іi]мн",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    if "Більше 4 кімнат" in text or "Более 4" in text:
        return "5+"
    return ""


def parse_area(text: str) -> str:
    patterns = [
        r"(\d+(?:[.,]\d+)?)\s*/\s*\d+(?:[.,]\d+)?\s*/",
        r"(\d+(?:[.,]\d+)?)\s*(?:кв\.\s*м|м²|м2)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).replace(",", ".")
    return ""


def parse_floor(text: str) -> tuple[str, str]:
    patterns = [
        r"поверх\s*(\d+)\s*(?:з|/)\s*(\d+)",
        r"(\d+)\s*(?:з|/)\s*(\d+)\s*пов",
        r"(\d+)\s*/\s*(\d+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1), match.group(2)
    match = re.search(r"(\d+)\s*пов\.", text, flags=re.IGNORECASE)
    return (match.group(1), "") if match else ("", "")


def parse_balcony(text: str) -> str:
    lower_text = text.lower()
    if re.search(r"без\s+(балкон|лодж)", lower_text):
        return "ні"
    if "балкон" in lower_text or "лодж" in lower_text:
        return "так"
    return ""


def google_maps_url(latitude: float | None, longitude: float | None) -> str:
    if latitude is None or longitude is None:
        return ""
    return f"https://www.google.com/maps/search/?api=1&query={latitude:.6f},{longitude:.6f}"


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_km = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    return radius_km * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def distance_to_center(city: str, latitude: float | None, longitude: float | None) -> str:
    if latitude is None or longitude is None or city not in CITY_CENTERS:
        return ""
    center_latitude, center_longitude = CITY_CENTERS[city]
    return f"{haversine_km(center_latitude, center_longitude, latitude, longitude):.1f}"


def fill_common(
    page: SearchPage,
    url: str,
    price: int,
    title: str,
    text: str,
    address: str = "",
    district: str = "",
    date: str = "",
    description: str = "",
    latitude: float | None = None,
    longitude: float | None = None,
) -> dict[str, str]:
    floor, floors_total = parse_floor(text)
    row = empty_row()
    row["source"] = page.source
    row["url"] = normalize_url(url, page.base_url)
    row["city"] = page.city
    row["price"] = str(price)
    row["adress"] = address
    row["площа"] = parse_area(text)
    row["кількість кімнат"] = parse_rooms(text)
    row["поверх"] = floor
    row["поверховість"] = floors_total
    row["балкон"] = parse_balcony(text)
    row["район"] = district
    row["км від центру"] = distance_to_center(page.city, latitude, longitude)
    row["url_google_maps"] = google_maps_url(latitude, longitude)
    row["дата публікації"] = date
    row["опис"] = description
    return row


def extract_rem(html: str, page: SearchPage) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for card in soup.select(".object-item"):
        link = card.select_one('a[href*="prodazha-kvartira"]')
        price = parse_price_usd(first_text(card, ".object-price"))
        if not link or price is None:
            continue

        location = first_text(card, ".object-city-region")
        parts = [part.strip() for part in location.split(",") if part.strip()]
        if parts and parts[-1].lower() == page.city.lower():
            parts = parts[:-1]
        address = ", ".join(parts[2:-2] if len(parts) >= 5 else parts[2:-1])
        district = parts[-1] if len(parts) >= 3 else ""
        longitude = first_text(card, ".object_lg")
        latitude = first_text(card, ".object_lt")
        try:
            lat = float(latitude) if latitude else None
            lon = float(longitude) if longitude else None
        except ValueError:
            lat = lon = None

        text = clean_text(card.get_text(" ", strip=True))
        rows.append(
            fill_common(
                page=page,
                url=link.get("href", ""),
                price=price,
                title=first_text(card, ".object-address"),
                text=text,
                address=address,
                district=district,
                date=first_text(card, ".object-card-updated .value") or first_text(card, ".object-card-updated"),
                description=first_text(card, ".object-card-description"),
                latitude=lat,
                longitude=lon,
            )
        )
    return rows


def extract_dim_ria(html: str, page: SearchPage) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for card in soup.select("section.realty-item"):
        link = card.select_one('a[href*="/uk/realty-"]')
        price = parse_price_usd(card.get_text(" ", strip=True))
        if not link or price is None:
            continue
        text = clean_text(card.get_text(" ", strip=True))
        title = clean_text(link.get_text(" ", strip=True))
        district = ""
        match = re.search(r"([А-ЯІЇЄҐA-Z][^·]{2,40})\s*·\s*" + re.escape(page.city), text)
        if match:
            district = match.group(1).strip()
        rows.append(
            fill_common(
                page=page,
                url=link.get("href", ""),
                price=price,
                title=title,
                text=text,
                address=title,
                district=district,
                date=publication_date_hint(text),
                description=text[:1600],
            )
        )
    return rows


def publication_date_hint(text: str) -> str:
    pattern = (
        r"(?:опубліковано|опубликовано|додано|добавлено|"
        r"продаж\s+квартири|продається\s+квартира|продаю\s+квартиру|продам\s+квартиру)"
        r"\s*(?:[·|:-]\s*)?"
        r"("
        r"(?:сьогодні|сегодня|вчора|вчера)(?:\s*(?:о|в)\s*\d{1,2}:\d{2})?"
        r"|\d{1,2}\s+[а-яіїєґ]+\.?(?:\s+\d{4})?(?:\s*р\.?)?(?:\s*(?:о|в)\s*\d{1,2}:\d{2})?"
        r"|\d{1,2}\s+(?:годин|години|годину|час(?:ов|а)?)\s+тому"
        r")"
    )
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return clean_text(match.group(1)) if match else ""


def extract_rieltor(html: str, page: SearchPage) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for card in soup.select(".catalog-card"):
        link = card.select_one('a[href*="/flats-sale/view/"]')
        price = parse_price_usd(first_text(card, ".catalog-card-price-title"))
        if not link or price is None:
            continue
        text = clean_text(card.get_text(" ", strip=True))
        district = ""
        district_match = re.search(re.escape(page.city) + r"\s*,\s*([^,]+?р-н)", text)
        if district_match:
            district = district_match.group(1).strip()
        address = ""
        address_match = re.search(r"\$/м²\s+(.+?)\s+" + re.escape(page.city), text)
        if address_match:
            address = address_match.group(1).strip()
        rows.append(
            fill_common(
                page=page,
                url=link.get("href", ""),
                price=price,
                title=address or "Продаж квартири",
                text=text,
                address=address,
                district=district,
                date="сьогодні" if " сьогодні " in f" {text} " else "",
                description=text[:900],
            )
        )
    return rows


def extract_olx(html: str, page: SearchPage) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for card in soup.select('[data-cy="l-card"]'):
        link = card.select_one('a[href*="/d/"]')
        text = clean_text(card.get_text(" ", strip=True))
        price = parse_price_usd(text)
        if not link or price is None:
            continue

        title = text.split("$", 1)[0]
        title = re.sub(r"\d[\d\s]*$", "", title).strip()
        district = ""
        date = ""
        location_match = re.search(re.escape(page.city) + r",\s*([^-]+)\s*-\s*([^0-9]+(?:\d{4}\s*р\.|[\d:]+)?)", text)
        if location_match:
            district = location_match.group(1).strip()
            date = location_match.group(2).strip()

        rows.append(
            fill_common(
                page=page,
                url=link.get("href", ""),
                price=price,
                title=title,
                text=text,
                district=district,
                date=date,
                description=text[:700],
            )
        )
    return rows


def extract_obyava(html: str, page: SearchPage, usd_uah_rate: float) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for card in soup.select(".single-item"):
        link = card.select_one("a[href]")
        text = clean_text(card.get_text(" ", strip=True))
        price = parse_price_usd(text, usd_uah_rate=usd_uah_rate)
        if not link or price is None:
            continue

        title = clean_text(link.get_text(" ", strip=True)) or text.split(" ГРН ", 1)[0]
        rows.append(
            fill_common(
                page=page,
                url=link.get("href", ""),
                price=price,
                title=title,
                text=text,
                date="",
                description=text[:700],
            )
        )
    return rows


def collect_listings(
    min_price: int,
    max_price: int,
    max_pages: int,
    sleep_seconds: float,
    usd_uah_rate: float,
    source_filter: set[str],
) -> list[dict[str, str]]:
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

    for search_page in SEARCH_PAGES:
        if source_filter and search_page.source not in source_filter:
            continue
        for page_number in range(1, max_pages + 1):
            page_url = build_page_url(search_page.url, page_number)
            print(f"Fetching {search_page.source} {search_page.city}: {page_url}")
            try:
                html = fetch_html(session, page_url)
            except requests.RequestException as error:
                print(f"  skipped: request failed: {error}")
                continue

            if is_cloudflare_challenge(html):
                print("  skipped: Cloudflare challenge / JS protection")
                continue

            extracted = search_page.extractor(html, search_page, usd_uah_rate)
            kept = 0
            for row in extracted:
                listing_url = row["url"]
                price = int(row["price"])
                if price < min_price or price > max_price or listing_url in seen_urls:
                    continue
                rows.append(row)
                seen_urls.add(listing_url)
                kept += 1

            print(f"  extracted={len(extracted)} kept_{min_price}_{max_price}={kept}")
            time.sleep(sleep_seconds)

    return rows


def write_csv(rows: Iterable[dict[str, str]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    args = parse_args()
    source_filter = {source.strip() for source in args.sources.split(",") if source.strip()}
    rows = collect_listings(
        min_price=args.min_price,
        max_price=args.max_price,
        max_pages=args.max_pages,
        sleep_seconds=args.sleep,
        usd_uah_rate=args.usd_uah_rate,
        source_filter=source_filter,
    )
    output_path = Path(args.output)
    write_csv(rows, output_path)
    print(f"Saved {len(rows)} rows to {output_path}")


if __name__ == "__main__":
    main()
