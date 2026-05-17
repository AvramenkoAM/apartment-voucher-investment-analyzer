#!/usr/bin/env python3
"""Apply the local address extraction model to fill the `adress` column."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from fill_address_from_url import address_from_url


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "data/apartments_multi_source.csv"
DEFAULT_OUTPUT = PROJECT_ROOT / "data/apartments_multi_source.csv"
DEFAULT_MODEL = PROJECT_ROOT / "models/address_extractor.json"

BAD_CANDIDATES = {
    "центр",
    "район",
    "ремонт",
    "середній поверх",
    "новому малоповерховому будинку",
    "квартира",
    "продам",
    "uk / obyavlenie",
    "ua / ua",
    "ua",
    "uk",
    "obyavlenie",
    "дорога",
    "район",
    "парк",
    "ринок",
    "правий берег",
    "лівий берег",
    "жк ваша идеальная",
    "жк ваша ідеальная",
}

WEAK_EXISTING_VALUES = BAD_CANDIDATES | {
    "центр",
    "таїрова",
    "черемушки",
    "аркадія",
    "перемога",
}


@dataclass
class Candidate:
    value: str
    source: str
    score: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply trained address model to a CSV.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help=f"Input CSV. Default: {DEFAULT_INPUT}")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help=f"Output CSV. Default: {DEFAULT_OUTPUT}")
    parser.add_argument("--model", default=str(DEFAULT_MODEL), help=f"Model JSON. Default: {DEFAULT_MODEL}")
    parser.add_argument("--min-score", type=float, default=2.2, help="Minimum candidate confidence score.")
    parser.add_argument("--overwrite-empty-only", action="store_true", default=True, help="Fill only empty adress cells.")
    return parser.parse_args()


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip(" ,.;:-")


def tokenize(text: str) -> list[str]:
    return [
        token.lower()
        for token in re.findall(r"[A-Za-zА-Яа-яІіЇїЄєҐґ0-9']+", text)
        if len(token) > 1
    ]


def normalize_candidate(value: str) -> str:
    value = clean_text(value)
    value = re.split(r"[!‼?]", value, maxsplit=1)[0]
    value = re.sub(r"\b(?:https?|www|olx|obyava|dom|ria|rem|rieltor|ua|uk|obyavlenie)\b", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\b(?:одеса|дніпро|днепр)\b.*$", "", value, flags=re.IGNORECASE).strip(" ,.;:-")
    value = re.sub(r"\b(?:\d{2,3}(?:[.,]\d+)?)\s*(?:м²|м2|кв\.?\s*м)\b.*$", "", value, flags=re.IGNORECASE).strip(" ,.;:-")
    value = re.sub(r"\b(?:\d+)\s*(?:пов|этаж|поверх)\b.*$", "", value, flags=re.IGNORECASE).strip(" ,.;:-")
    value = re.sub(
        r"\b(?:пропонується|предлагается|продаж|продажа|продам|квартира|квартири|квартиру)\b.*$",
        "",
        value,
        flags=re.IGNORECASE,
    ).strip(" ,.;:-")
    value = re.sub(r"\s*/\s*", " / ", value)
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"\b(?:program(?:me|my|y|i)?|da|nizkiy|pod|iz|sredina|raschet|doma|yaht|mira|сегодня|сьогодні)\b", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\b(?:primorskiy|klub|provskiy|dniprovskiy|dneprovskiy)\b", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\b[A-Za-z]\b$", "", value).strip(" ,.;:-")
    value = re.sub(r"\s+-\s+.*\d{1,2}:\d{2}.*$", "", value)
    value = re.sub(r"\s+", " ", value)
    if value.lower().startswith("жк ") and len(value.split()) > 4:
        value = " ".join(value.split()[:4])
    while value.split() and value.split()[-1].lower() in {"на", "po", "pod", "iz", "n"}:
        value = " ".join(value.split()[:-1])
    return value.strip(" ,.;:-")


def title_case_candidate(value: str) -> str:
    keep_lower = {"вул.", "ул.", "просп.", "пров.", "ж/м", "р-н"}
    words = []
    for word in value.split():
        if word.lower() in keep_lower:
            words.append(word.lower())
        elif word.isupper() and len(word) <= 3:
            words.append(word)
        else:
            words.append(word[:1].upper() + word[1:])
    return " ".join(words)


def regex_candidates(text: str) -> list[Candidate]:
    patterns = [
        r"(?:за адресою|по адресу|адреса|адрес)\s+([^.!?;]{4,90})",
        r"(?:на|по|біля|возле|район|р-н)\s+((?:вул\.?|ул\.?|улиц[аы]|вулиц[яі]|просп\.?|проспект|пров\.?|провулок|пер\.?|переулок|ж/м|жм|ЖК)\s*[^.!?;,$]{3,80})",
        r"((?:вул\.?|ул\.?|улиц[аы]|вулиц[яі]|просп\.?|проспект|пров\.?|провулок|пер\.?|переулок|ж/м|жм|ЖК)\s*[^.!?;,$]{3,80})",
        r"\b([А-ЯІЇЄҐA-Z][А-Яа-яІіЇїЄєҐґA-Za-z']+\s*/\s*[А-ЯІЇЄҐA-Z][А-Яа-яІіЇїЄєҐґA-Za-z']+)\b",
    ]
    candidates = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            value = normalize_candidate(match.group(1))
            if value:
                candidates.append(Candidate(title_case_candidate(value), "description_regex", 1.8))
    return candidates


def known_address_candidates(text: str, model: dict) -> list[Candidate]:
    lower_text = text.lower()
    candidates = []
    for address in model.get("known_addresses", []):
        if len(address) >= 5 and address.lower() in lower_text:
            candidates.append(Candidate(address, "known_address", 2.6))
    return candidates


def url_candidate(url: str) -> list[Candidate]:
    address = address_from_url(url)
    if not address:
        return []
    return [Candidate(address, "url_slug", 1.5)]


def token_candidates(text: str, city: str, model: dict) -> list[Candidate]:
    tokens = set(tokenize(text))
    city_tokens = model.get("city_address_tokens", {}).get(city, [])
    candidates = []
    for token in city_tokens:
        if token in tokens and len(token) >= 5:
            candidates.append(Candidate(title_case_candidate(token), "learned_token", 1.1))
    return candidates[:8]


def score_candidate(candidate: Candidate, row: dict[str, str], model: dict) -> Candidate:
    value = normalize_candidate(candidate.value)
    if not value:
        return Candidate("", candidate.source, 0)

    lower = value.lower()
    if lower in BAD_CANDIDATES:
        return Candidate("", candidate.source, 0)
    if any(word in lower for word in ["идеальная", "ідеальная", "сегодня", "сьогодні"]):
        return Candidate("", candidate.source, 0)
    if re.search(r"\d{1,2}:\d{2}", lower):
        return Candidate("", candidate.source, 0)
    candidate_tokens = set(tokenize(value))
    if len(candidate_tokens) == 1 and not any(marker in lower for marker in ["вул", "ул", "просп", "пров", "ж/м", "жк"]):
        score = candidate.score - 0.8
    else:
        score = candidate.score

    model_tokens = set(model.get("address_tokens", []))
    city_tokens = set(model.get("city_address_tokens", {}).get(row.get("city", ""), []))
    marker_words = {"вул", "ул", "улица", "вулиця", "просп", "проспект", "пров", "провулок", "жм", "жк", "дорога", "бульв"}

    score += min(1.5, 0.35 * len(candidate_tokens & model_tokens))
    score += min(1.0, 0.30 * len(candidate_tokens & city_tokens))
    if candidate_tokens & marker_words or any(marker in lower for marker in ["вул.", "ул.", "просп.", "пров.", "ж/м", "жк"]):
        score += 1.0
    if "/" in value:
        score += 0.4
    if len(value) < 4:
        score -= 2.0
    if len(value.split()) > 8:
        score -= 0.8
    if row.get("source") == "OBYAVA.ua" and candidate.source == "description_regex":
        score += 0.4

    return Candidate(value, candidate.source, score)


def is_weak_existing_address(address: str) -> bool:
    normalized = normalize_candidate(address).lower()
    if not normalized:
        return True
    if normalized in WEAK_EXISTING_VALUES:
        return True
    if normalized.startswith("жк ") and len(normalized.split()) > 4:
        return True
    return False


def best_address(row: dict[str, str], model: dict) -> Candidate:
    body_text = " ".join(
        [
            row.get("опис", ""),
        ]
    )
    candidates = []
    candidates.extend(regex_candidates(body_text))
    candidates.extend(known_address_candidates(body_text, model))
    candidates.extend(url_candidate(row.get("url", "")))
    candidates.extend(token_candidates(body_text, row.get("city", ""), model))

    scored = [score_candidate(candidate, row, model) for candidate in candidates]
    scored = [candidate for candidate in scored if candidate.value]
    if not scored:
        return Candidate("", "", 0)
    return max(scored, key=lambda candidate: candidate.score)


def main() -> None:
    args = parse_args()
    model = json.loads(Path(args.model).read_text(encoding="utf-8"))

    with Path(args.input).open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        rows = list(reader)
        fieldnames = reader.fieldnames or []

    updated = 0
    skipped_low_confidence = 0
    for row in rows:
        current_address = row.get("adress", "").strip()
        if args.overwrite_empty_only and current_address and not is_weak_existing_address(current_address):
            continue
        candidate = best_address(row, model)
        if candidate.score >= args.min_score:
            row["adress"] = candidate.value
            updated += 1
        else:
            skipped_low_confidence += 1

    with Path(args.output).open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(
        f"Applied address model: updated={updated}, "
        f"skipped_low_confidence={skipped_low_confidence}, output={args.output}"
    )


if __name__ == "__main__":
    try:
        main()
    except FileNotFoundError as error:
        print(f"Missing file: {error}", file=sys.stderr)
        raise SystemExit(1)
