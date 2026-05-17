#!/usr/bin/env python3
"""Collect apartment listings and fill the target CSV columns.

Current version supports REM.ua sale pages for Odesa and Dnipro.
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

DEFAULT_OUTPUT = Path("data/apartments.csv")
DEFAULT_MAX_PRICE_USD = 45_000

CITY_CENTERS = {
    "Одеса": (46.4825, 30.7233),
    "Дніпро": (48.4674, 35.0407),
}


@dataclass(frozen=True)
class SearchPage:
    source: str
    city: str
    url: str
    extractor: Callable[[str, "SearchPage"], list[dict[str, str]]]


SEARCH_PAGES = [
    SearchPage(
        source="REM.ua",
        city="Одеса",
        url="https://rem.ua/ua/prodazha-kvartir-odessa",
        extractor=lambda html, page: extract_rem_listings(html, page),
    ),
    SearchPage(
        source="REM.ua",
        city="Дніпро",
        url="https://rem.ua/ua/prodazha-kvartir-dnepr",
        extractor=lambda html, page: extract_rem_listings(html, page),
    ),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect apartment listings and fill the target CSV columns."
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help=f"Output CSV path. Default: {DEFAULT_OUTPUT}",
    )
    parser.add_argument(
        "--max-price",
        type=int,
        default=DEFAULT_MAX_PRICE_USD,
        help=f"Maximum listing price in USD. Default: {DEFAULT_MAX_PRICE_USD}",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=1,
        help="How many pages to request per search URL. Default: 1",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=1.0,
        help="Pause between requests in seconds. Default: 1.0",
    )
    return parser.parse_args()


def build_page_url(base_url: str, page_number: int) -> str:
    if page_number <= 1:
        return base_url

    parsed = urlparse(base_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["page"] = str(page_number)
    return urlunparse(parsed._replace(query=urlencode(query)))


def fetch_html(session: requests.Session, url: str) -> str:
    response = session.get(url, timeout=25)
    response.raise_for_status()
    return response.text


def normalize_url(url: str) -> str:
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return "https://rem.ua" + url
    return url


def parse_price_usd(raw_text: str) -> int | None:
    match = re.search(r"(\d[\d\s]*)\s*\$", raw_text)
    if not match:
        return None
    digits = re.sub(r"\D", "", match.group(1))
    return int(digits) if digits else None


def clean_text(raw_text: str) -> str:
    return " ".join(raw_text.split())


def first_text(node, selector: str) -> str:
    selected = node.select_one(selector)
    if not selected:
        return ""
    return clean_text(selected.get_text(" ", strip=True))


def parse_rooms_and_area(location_text: str) -> tuple[str, str]:
    rooms = ""
    area = ""

    room_match = re.search(r"(\d+)\s*-\s*комн", location_text, flags=re.IGNORECASE)
    if room_match:
        rooms = room_match.group(1)

    area_match = re.search(r"(\d+(?:[.,]\d+)?)\s*кв\.\s*м", location_text, flags=re.IGNORECASE)
    if area_match:
        area = area_match.group(1).replace(",", ".")

    return rooms, area


def parse_address_and_district(location_text: str, city: str) -> tuple[str, str]:
    parts = [part.strip() for part in location_text.split(",") if part.strip()]
    if parts and parts[-1].lower() == city.lower():
        parts = parts[:-1]

    # Expected REM.ua pattern:
    # "1-комн., 42.9 кв. м., street, microdistrict, admin district, city"
    address_parts = parts[2:-2] if len(parts) >= 5 else parts[2:-1]
    address = ", ".join(address_parts)
    district = parts[-1] if len(parts) >= 3 else ""

    return address, district


def parse_floor(floor_text: str) -> tuple[str, str]:
    match = re.search(r"(\d+)\s*з\s*(\d+)", floor_text)
    if not match:
        return "", ""
    return match.group(1), match.group(2)


def parse_balcony(text: str) -> str:
    text_lower = text.lower()
    if re.search(r"без\s+(балкон|лодж)", text_lower):
        return "ні"
    if "балкон" in text_lower or "лодж" in text_lower:
        return "так"
    return ""


def parse_coordinates(card) -> tuple[float | None, float | None]:
    longitude_text = first_text(card, ".object_lg")
    latitude_text = first_text(card, ".object_lt")
    if not longitude_text or not latitude_text:
        return None, None

    try:
        return float(latitude_text), float(longitude_text)
    except ValueError:
        return None, None


def haversine_km(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
) -> float:
    radius_km = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return radius_km * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def google_maps_url(latitude: float | None, longitude: float | None) -> str:
    if latitude is None or longitude is None:
        return ""
    return f"https://www.google.com/maps/search/?api=1&query={latitude:.6f},{longitude:.6f}"


def distance_to_city_center(city: str, latitude: float | None, longitude: float | None) -> str:
    if latitude is None or longitude is None or city not in CITY_CENTERS:
        return ""
    center_latitude, center_longitude = CITY_CENTERS[city]
    return f"{haversine_km(center_latitude, center_longitude, latitude, longitude):.1f}"


def empty_row() -> dict[str, str]:
    return {column: "" for column in CSV_COLUMNS}


def extract_rem_listings(html: str, page: SearchPage) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    listings: list[dict[str, str]] = []

    for card in soup.select(".object-item"):
        link = card.select_one('a[href*="prodazha-kvartira"]')
        if not link:
            continue

        price_node = card.select_one(".object-price")
        price_text = price_node.get_text(" ", strip=True) if price_node else card.get_text(" ", strip=True)
        price = parse_price_usd(price_text)
        if price is None:
            continue

        location_text = first_text(card, ".object-city-region")
        title = first_text(card, ".object-address")
        description = first_text(card, ".object-card-description")
        floor_text = first_text(card, ".object-param__value") or first_text(card, ".object-parameters")
        publication_date = first_text(card, ".object-card-updated .value") or first_text(card, ".object-card-updated")
        rooms, area = parse_rooms_and_area(location_text)
        address, district = parse_address_and_district(location_text, page.city)
        floor, floors_total = parse_floor(floor_text)
        latitude, longitude = parse_coordinates(card)
        full_card_text = clean_text(card.get_text(" ", strip=True))

        row = empty_row()
        row["source"] = page.source
        row["url"] = normalize_url(link.get("href", ""))
        row["city"] = page.city
        row["price"] = str(price)
        row["adress"] = address
        row["площа"] = area
        row["кількість кімнат"] = rooms
        row["поверх"] = floor
        row["поверховість"] = floors_total
        row["балкон"] = parse_balcony(full_card_text)
        row["район"] = district
        row["км від центру"] = distance_to_city_center(page.city, latitude, longitude)
        row["url_google_maps"] = google_maps_url(latitude, longitude)
        row["дата публікації"] = publication_date
        row["опис"] = description
        listings.append(row)

    return listings


def collect_listings(max_price: int, max_pages: int, sleep_seconds: float) -> list[dict[str, str]]:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "uk-UA,uk;q=0.9,en;q=0.8",
        }
    )

    rows: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    for search_page in SEARCH_PAGES:
        for page_number in range(1, max_pages + 1):
            page_url = build_page_url(search_page.url, page_number)
            print(f"Fetching {search_page.source} {search_page.city}: {page_url}")
            html = fetch_html(session, page_url)

            extracted = search_page.extractor(html, search_page)
            kept = 0
            for row in extracted:
                listing_url = row["url"]
                price = int(row["price"])
                if price > max_price or listing_url in seen_urls:
                    continue
                rows.append(row)
                seen_urls.add(listing_url)
                kept += 1

            print(f"  extracted={len(extracted)} kept_under_{max_price}={kept}")
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
    rows = collect_listings(
        max_price=args.max_price,
        max_pages=args.max_pages,
        sleep_seconds=args.sleep,
    )
    output_path = Path(args.output)
    write_csv(rows, output_path)
    print(f"Saved {len(rows)} rows to {output_path}")


if __name__ == "__main__":
    main()
