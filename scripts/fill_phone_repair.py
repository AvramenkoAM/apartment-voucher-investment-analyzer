#!/usr/bin/env python3
"""Add and fill `телефон` and `ремонт` columns."""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "data/apartments_multi_source.csv"
DEFAULT_OUTPUT = PROJECT_ROOT / "data/apartments_multi_source.csv"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fill phone and repair columns in apartment CSV.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help=f"Input CSV. Default: {DEFAULT_INPUT}")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help=f"Output CSV. Default: {DEFAULT_OUTPUT}")
    parser.add_argument("--overwrite-repair", action="store_true", help="Overwrite existing repair values.")
    parser.add_argument("--overwrite-phone", action="store_true", help="Overwrite existing phone values.")
    return parser.parse_args()


def insert_after(fieldnames: list[str], new_field: str, anchor: str) -> list[str]:
    if new_field in fieldnames:
        return fieldnames
    if anchor not in fieldnames:
        return [*fieldnames, new_field]
    index = fieldnames.index(anchor) + 1
    return [*fieldnames[:index], new_field, *fieldnames[index:]]


def ensure_columns(fieldnames: list[str]) -> list[str]:
    fieldnames = insert_after(fieldnames, "телефон", "price")
    fieldnames = insert_after(fieldnames, "ремонт", "дата публікації")
    return fieldnames


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 12 and digits.startswith("380"):
        return f"+{digits}"
    if len(digits) == 10 and digits.startswith("0"):
        return "+38" + digits
    return ""


def extract_phone(text: str) -> str:
    phones = []
    for match in re.finditer(r"(?:\+?38)?\s*\(?0\d{2}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}", text):
        phone = normalize_phone(match.group(0))
        if phone and phone not in phones:
            phones.append(phone)
    return "; ".join(phones)


def detect_repair(text: str) -> str:
    lower = text.lower()

    negative_patterns = [
        (r"без\s+ремонт", "без ремонту"),
        (r"под\s+ремонт|під\s+ремонт|потребує\s+ремонту|требует\s+ремонт", "під ремонт"),
        (r"від\s+будівельник|от\s+строител|стан\s+від\s+будівельник", "від забудовника"),
        (r"состояние\s+от\s+строител", "від забудовника"),
    ]
    for pattern, label in negative_patterns:
        if re.search(pattern, lower):
            return label

    positive_patterns = [
        (r"євроремонт|евроремонт", "євроремонт"),
        (r"капітальн\w*\s+ремонт|капитальн\w*\s+ремонт", "капітальний ремонт"),
        (r"дизайнерськ\w*\s+ремонт|дизайнерск\w*\s+ремонт", "дизайнерський ремонт"),
        (r"нов\w*\s+ремонт|новый\s+ремонт|новий\s+ремонт", "новий ремонт"),
        (r"якісн\w*\s+ремонт|качественн\w*\s+ремонт", "якісний ремонт"),
        (r"косметичн\w*\s+ремонт|косметическ\w*\s+ремонт", "косметичний ремонт"),
        (r"з\s+ремонтом|с\s+ремонтом|ремонт\s+зроблено|ремонт\s+сделан", "є ремонт"),
        (r"житловий\s+стан|жилое\s+состояние|жилая\s+состояние", "житловий стан"),
    ]
    for pattern, label in positive_patterns:
        if re.search(pattern, lower):
            return label

    if "ремонт" in lower:
        return "ремонт згадується"
    return ""


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)

    with input_path.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        rows = list(reader)
        fieldnames = ensure_columns(reader.fieldnames or [])

    updated_phone = 0
    updated_repair = 0
    for row in rows:
        text = clean_text(" ".join([row.get("опис", ""), row.get("url", "")]))

        if args.overwrite_phone or not row.get("телефон", "").strip():
            phone = extract_phone(text)
            if phone:
                row["телефон"] = phone
                updated_phone += 1
            else:
                row.setdefault("телефон", "")

        if args.overwrite_repair or not row.get("ремонт", "").strip():
            repair = detect_repair(text)
            if repair:
                row["ремонт"] = repair
                updated_repair += 1
            else:
                row.setdefault("ремонт", "")

    with output_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Filled phone/repair: phone_updated={updated_phone}, repair_updated={updated_repair}, output={output_path}")


if __name__ == "__main__":
    main()
