#!/usr/bin/env python3
"""Create a CSV and contact sheets for manual repair labels."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CSV = PROJECT_ROOT / "data/apartments_multi_source.csv"
DEFAULT_IMAGE_DIR = PROJECT_ROOT / "data/repair_photo_images"
DEFAULT_OUTPUT = PROJECT_ROOT / "data/repair_photo_labels.csv"
DEFAULT_SHEETS_DIR = PROJECT_ROOT / "data/repair_photo_label_sheets"

TARGET_CLASSES = [
    "радянський ремонт",
    "євроремонт",
    "під ремонт",
    "косметичний ремонт",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export repair labeling CSV and photo contact sheets.")
    parser.add_argument("--csv", default=str(DEFAULT_CSV), help=f"Apartment CSV. Default: {DEFAULT_CSV}")
    parser.add_argument("--image-dir", default=str(DEFAULT_IMAGE_DIR), help=f"Image cache. Default: {DEFAULT_IMAGE_DIR}")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help=f"Label CSV. Default: {DEFAULT_OUTPUT}")
    parser.add_argument("--sheets-dir", default=str(DEFAULT_SHEETS_DIR), help=f"Contact sheets dir. Default: {DEFAULT_SHEETS_DIR}")
    parser.add_argument("--thumb-width", type=int, default=260, help="Thumbnail width.")
    parser.add_argument("--thumb-height", type=int, default=190, help="Thumbnail height.")
    parser.add_argument("--max-images", type=int, default=4, help="Images per contact sheet.")
    return parser.parse_args()


def log(message: str) -> None:
    print(message, flush=True)


def row_image_paths(image_dir: Path, row_id: str, max_images: int) -> list[Path]:
    row_dir = image_dir / row_id
    if not row_dir.exists():
        return []
    return sorted(row_dir.glob("*.jpg"))[:max_images]


def fit_image(path: Path, width: int, height: int) -> Image.Image:
    image = Image.open(path).convert("RGB")
    image.thumbnail((width, height))
    canvas = Image.new("RGB", (width, height), "white")
    x = (width - image.width) // 2
    y = (height - image.height) // 2
    canvas.paste(image, (x, y))
    return canvas


def make_contact_sheet(row: dict[str, str], image_paths: list[Path], output_path: Path, thumb_width: int, thumb_height: int) -> bool:
    if not image_paths:
        return False
    header_height = 72
    width = thumb_width * len(image_paths)
    height = header_height + thumb_height
    sheet = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    header = f"#{row.get('№')} | {row.get('source')} | {row.get('city')} | current: {row.get('ремонт')}"
    draw.text((10, 10), header[:180], fill="black", font=font)
    draw.text((10, 30), f"{row.get('price')} USD | {row.get('adress')}"[:180], fill="black", font=font)
    draw.text((10, 50), row.get("url", "")[:180], fill="black", font=font)
    for index, path in enumerate(image_paths):
        thumb = fit_image(path, thumb_width, thumb_height)
        sheet.paste(thumb, (index * thumb_width, header_height))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path, quality=90)
    return True


def main() -> None:
    args = parse_args()
    csv_path = Path(args.csv)
    image_dir = Path(args.image_dir)
    output_path = Path(args.output)
    sheets_dir = Path(args.sheets_dir)

    with csv_path.open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))

    output_rows = []
    created_sheets = 0
    for row in rows:
        row_id = row.get("№", "").strip()
        image_paths = row_image_paths(image_dir, row_id, args.max_images)
        sheet_path = sheets_dir / f"{int(row_id):03d}.jpg" if row_id.isdigit() else sheets_dir / f"{row_id}.jpg"
        if make_contact_sheet(row, image_paths, sheet_path, args.thumb_width, args.thumb_height):
            created_sheets += 1
            sheet_value = str(sheet_path)
        else:
            sheet_value = ""
        output_rows.append(
            {
                "№": row.get("№", ""),
                "source": row.get("source", ""),
                "city": row.get("city", ""),
                "price": row.get("price", ""),
                "current_repair": row.get("ремонт", ""),
                "manual_repair": "",
                "allowed_values": " | ".join(TARGET_CLASSES),
                "contact_sheet": sheet_value,
                "url": row.get("url", ""),
                "adress": row.get("adress", ""),
                "опис": row.get("опис", "")[:500],
            }
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as file:
        fieldnames = [
            "№",
            "source",
            "city",
            "price",
            "current_repair",
            "manual_repair",
            "allowed_values",
            "contact_sheet",
            "url",
            "adress",
            "опис",
        ]
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(output_rows)

    log(f"Exported {len(output_rows)} label rows to {output_path}")
    log(f"Created {created_sheets} contact sheets in {sheets_dir}")
    log("Fill `manual_repair` with one of: " + ", ".join(TARGET_CLASSES))


if __name__ == "__main__":
    main()
