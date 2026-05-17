#!/usr/bin/env python3
"""Fill `км від центру` using coordinates from Maps URLs or geocoded addresses."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote_plus, urlparse

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "data/apartments_multi_source.csv"
DEFAULT_OUTPUT = PROJECT_ROOT / "data/apartments_multi_source.csv"
DEFAULT_CACHE = PROJECT_ROOT / "data/geocode_cache.json"

CITY_CENTERS = {
    "Одеса": (46.4825, 30.7233),
    "Дніпро": (48.4674, 35.0407),
}

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "roma-apartment-research/1.0 (local CSV enrichment)"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fill distance to city center in apartment CSV.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help=f"Input CSV. Default: {DEFAULT_INPUT}")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help=f"Output CSV. Default: {DEFAULT_OUTPUT}")
    parser.add_argument("--cache", default=str(DEFAULT_CACHE), help=f"Geocode cache JSON. Default: {DEFAULT_CACHE}")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing distances.")
    parser.add_argument("--no-geocode", action="store_true", help="Only use coordinates already present in url_google_maps.")
    parser.add_argument("--sleep", type=float, default=1.0, help="Pause between geocoding requests.")
    parser.add_argument("--max-rows", type=int, default=0, help="Maximum rows to geocode. 0 means no limit.")
    return parser.parse_args()


def clean_text(value: str) -> str:
    value = re.sub(r"\s+", " ", value or "")
    return value.strip(" ,.;:-")


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_km = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    return radius_km * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def distance_to_center(city: str, lat: float, lon: float) -> str:
    if city not in CITY_CENTERS:
        return ""
    center_lat, center_lon = CITY_CENTERS[city]
    return f"{haversine_km(center_lat, center_lon, lat, lon):.1f}"


def coordinates_from_maps_url(url: str) -> tuple[float | None, float | None]:
    if not url:
        return None, None

    parsed = urlparse(url)
    query = parse_qs(parsed.query).get("query", [""])[0]
    query = unquote_plus(query)

    patterns = [
        r"^\s*(-?\d{1,2}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)\s*$",
        r"[?&]q=(-?\d{1,2}(?:\.\d+)?),(-?\d{1,3}(?:\.\d+)?)",
        r"@(-?\d{1,2}(?:\.\d+)?),(-?\d{1,3}(?:\.\d+)?)",
    ]
    haystack = query or url
    for pattern in patterns:
        match = re.search(pattern, haystack)
        if match:
            return float(match.group(1)), float(match.group(2))
    return None, None


def query_from_maps_url(url: str) -> str:
    if not url:
        return ""
    query = parse_qs(urlparse(url).query).get("query", [""])[0]
    query = clean_text(unquote_plus(query))
    if re.match(r"^-?\d{1,2}(?:\.\d+)?\s*,\s*-?\d{1,3}(?:\.\d+)?$", query):
        return ""
    return query


def geocode_queries(row: dict[str, str]) -> list[str]:
    address = clean_text(row.get("adress", ""))
    city = clean_text(row.get("city", ""))
    district = clean_text(row.get("район", ""))

    queries = []
    if address and city:
        parts = [address, city]
        if district and district.lower() not in address.lower():
            parts.append(district)
        parts.append("Україна")
        queries.append(", ".join(parts))
        queries.append(", ".join([address, city, "Україна"]))

    maps_query = query_from_maps_url(row.get("url_google_maps", ""))
    if maps_query:
        queries.append(maps_query)

    if district and city:
        queries.append(", ".join([district, city, "Україна"]))

    unique_queries = []
    seen = set()
    for query in queries:
        query = clean_text(query)
        if query and query not in seen:
            unique_queries.append(query)
            seen.add(query)
    return unique_queries


def load_cache(path: Path) -> dict[str, dict[str, float]]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_cache(path: Path, cache: dict[str, dict[str, float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def geocode_one(session: requests.Session, query: str, cache: dict[str, dict[str, float]], sleep_seconds: float) -> tuple[float | None, float | None]:
    if not query:
        return None, None
    if query in cache:
        cached = cache[query]
        return cached.get("lat"), cached.get("lon")

    response = session.get(
        NOMINATIM_URL,
        params={"q": query, "format": "json", "limit": 1, "addressdetails": 0},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    time.sleep(sleep_seconds)

    if not data:
        cache[query] = {}
        return None, None

    lat = float(data[0]["lat"])
    lon = float(data[0]["lon"])
    cache[query] = {"lat": lat, "lon": lon}
    return lat, lon


def geocode_any(session: requests.Session, queries: list[str], cache: dict[str, dict[str, float]], sleep_seconds: float) -> tuple[float | None, float | None]:
    for query in queries:
        lat, lon = geocode_one(session, query, cache, sleep_seconds)
        if lat is not None and lon is not None:
            return lat, lon
    return None, None


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)
    cache_path = Path(args.cache)
    cache = load_cache(cache_path)

    with input_path.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        rows = list(reader)
        fieldnames = reader.fieldnames or []

    if "км від центру" not in fieldnames:
        fieldnames.append("км від центру")

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "uk,en;q=0.8"})

    updated = 0
    from_maps_url = 0
    from_geocode = 0
    skipped_without_location = 0
    failed = 0
    geocoded_rows = 0

    for row in rows:
        if row.get("км від центру", "").strip() and not args.overwrite:
            continue

        lat, lon = coordinates_from_maps_url(row.get("url_google_maps", ""))
        source = "maps_url"

        if (lat is None or lon is None) and not args.no_geocode:
            if args.max_rows and geocoded_rows >= args.max_rows:
                continue
            queries = geocode_queries(row)
            if not queries:
                skipped_without_location += 1
                continue
            try:
                lat, lon = geocode_any(session, queries, cache, args.sleep)
                geocoded_rows += 1
                source = "geocode"
            except requests.RequestException as error:
                failed += 1
                print(f"Geocode failed #{row.get('№')}: {error}")
                continue

        if lat is None or lon is None:
            skipped_without_location += 1
            continue

        distance = distance_to_center(row.get("city", ""), lat, lon)
        if not distance:
            skipped_without_location += 1
            continue

        row["км від центру"] = distance
        updated += 1
        if source == "maps_url":
            from_maps_url += 1
        else:
            from_geocode += 1

    save_cache(cache_path, cache)

    with output_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(
        f"Filled distance to center: updated={updated}, from_maps_url={from_maps_url}, "
        f"from_geocode={from_geocode}, skipped_without_location={skipped_without_location}, "
        f"failed={failed}, output={output_path}"
    )


if __name__ == "__main__":
    main()
