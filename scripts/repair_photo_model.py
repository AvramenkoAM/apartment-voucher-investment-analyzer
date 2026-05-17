#!/usr/bin/env python3
"""Train and apply a local photo-based repair classifier.

This model is intentionally lightweight: it uses existing `ремонт` values as
weak labels, downloads listing photos, extracts visual features, and trains a
small sklearn classifier. It does not use external AI APIs.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import time
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import cv2
import numpy as np
import requests
from bs4 import BeautifulSoup
from PIL import Image
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

import fill_phone_repair


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CSV = PROJECT_ROOT / "data/apartments_multi_source.csv"
DEFAULT_MODEL = PROJECT_ROOT / "models/repair_photo_model_4class.json"
DEFAULT_IMAGE_DIR = PROJECT_ROOT / "data/repair_photo_images"
DEFAULT_LABELS = PROJECT_ROOT / "data/repair_photo_labels.csv"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "uk-UA,uk;q=0.9,ru;q=0.8,en;q=0.7",
}

IMAGE_HEADERS = {
    **HEADERS,
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
}

TARGET_CLASSES = [
    "радянський ремонт",
    "євроремонт",
    "під ремонт",
    "косметичний ремонт",
]

EXACT_REPAIR_LABELS = {
    "радянський ремонт": "радянський ремонт",
    "советский ремонт": "радянський ремонт",
    "житловий стан": "радянський ремонт",
    "жилое состояние": "радянський ремонт",
    "євроремонт": "євроремонт",
    "евроремонт": "євроремонт",
    "капітальний ремонт": "євроремонт",
    "капитальный ремонт": "євроремонт",
    "дизайнерський ремонт": "євроремонт",
    "дизайнерский ремонт": "євроремонт",
    "новий ремонт": "євроремонт",
    "новый ремонт": "євроремонт",
    "якісний ремонт": "євроремонт",
    "качественный ремонт": "євроремонт",
    "під ремонт": "під ремонт",
    "под ремонт": "під ремонт",
    "без ремонту": "під ремонт",
    "без ремонта": "під ремонт",
    "від забудовника": "під ремонт",
    "от строителей": "під ремонт",
    "косметичний ремонт": "косметичний ремонт",
    "косметический ремонт": "косметичний ремонт",
}

TEXT_LABEL_PATTERNS = [
    (
        "під ремонт",
        [
            r"під\s+ремонт",
            r"под\s+ремонт",
            r"без\s+ремонт",
            r"потребує\s+ремонту",
            r"требует\s+ремонт",
            r"від\s+забудовник",
            r"от\s+строител",
            r"стан\s+від\s+будівельник",
            r"состояние\s+от\s+строител",
        ],
    ),
    ("косметичний ремонт", [r"косметичн\w*\s+ремонт", r"косметическ\w*\s+ремонт"]),
    (
        "радянський ремонт",
        [
            r"радян\w*\s+ремонт",
            r"советск\w*\s+ремонт",
            r"житловий\s+стан",
            r"жилое\s+состояние",
            r"стара\s+квартира",
            r"старый\s+ремонт",
            r"бабушкин\s+ремонт",
        ],
    ),
    (
        "євроремонт",
        [
            r"євроремонт",
            r"евроремонт",
            r"дизайнерськ\w*\s+ремонт",
            r"дизайнерск\w*\s+ремонт",
            r"нов\w*\s+ремонт",
            r"новый\s+ремонт",
            r"сучасн\w*\s+ремонт",
            r"современн\w*\s+ремонт",
            r"якісн\w*\s+ремонт",
            r"качественн\w*\s+ремонт",
            r"капітальн\w*\s+ремонт",
            r"капитальн\w*\s+ремонт",
        ],
    ),
]

FEATURE_NAMES = [
    "rgb_mean_r",
    "rgb_mean_g",
    "rgb_mean_b",
    "rgb_std_r",
    "rgb_std_g",
    "rgb_std_b",
    "hsv_sat_mean",
    "hsv_sat_std",
    "hsv_val_mean",
    "hsv_val_std",
    "gray_mean",
    "gray_std",
    "edge_density",
    "laplacian_var",
    "dark_ratio",
    "bright_ratio",
    "neutral_ratio",
    "colorfulness",
    "entropy",
]
FEATURE_NAMES.extend([f"hist_{channel}_{index}" for channel in ("r", "g", "b") for index in range(8)])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train/apply photo-based repair model.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    train = subparsers.add_parser("train", help="Train the repair photo model.")
    add_common_args(train)
    train.add_argument("--min-samples", type=int, default=10, help="Minimum labeled photo samples required.")

    apply = subparsers.add_parser("apply", help="Apply the repair photo model to CSV.")
    add_common_args(apply)
    apply.add_argument("--output", default=str(DEFAULT_CSV), help=f"Output CSV. Default: {DEFAULT_CSV}")
    apply.add_argument("--overwrite", action="store_true", help="Overwrite existing repair values.")
    apply.add_argument("--min-confidence", type=float, default=0.62, help="Minimum class probability.")

    return parser.parse_args()


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--csv", default=str(DEFAULT_CSV), help=f"CSV path. Default: {DEFAULT_CSV}")
    parser.add_argument("--model", default=str(DEFAULT_MODEL), help=f"Model JSON. Default: {DEFAULT_MODEL}")
    parser.add_argument("--image-dir", default=str(DEFAULT_IMAGE_DIR), help=f"Image cache dir. Default: {DEFAULT_IMAGE_DIR}")
    parser.add_argument("--labels-csv", default=str(DEFAULT_LABELS), help=f"Manual labels CSV. Default: {DEFAULT_LABELS}")
    parser.add_argument("--max-images", type=int, default=4, help="Max photos per listing.")
    parser.add_argument("--max-rows", type=int, default=0, help="Limit rows to process. 0 means no limit.")
    parser.add_argument("--sleep", type=float, default=0.7, help="Pause between listing page requests.")


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def log(message: str) -> None:
    print(message, flush=True)


def repair_to_label(value: str) -> str:
    repair = clean_text(value).lower()
    return EXACT_REPAIR_LABELS.get(repair, "")


def load_manual_labels(labels_path: Path) -> dict[str, str]:
    if not labels_path.exists():
        return {}
    labels: dict[str, str] = {}
    with labels_path.open(encoding="utf-8", newline="") as file:
        for row in csv.DictReader(file):
            row_id = clean_text(row.get("№", ""))
            label = repair_to_label(row.get("manual_repair", ""))
            if row_id and label:
                labels[row_id] = label
    return labels


def row_to_training_label(row: dict[str, str], manual_labels: dict[str, str] | None = None) -> str:
    manual_label = (manual_labels or {}).get(clean_text(row.get("№", "")))
    if manual_label:
        return manual_label

    exact_label = repair_to_label(row.get("ремонт", ""))
    if exact_label:
        return exact_label

    text = clean_text(" ".join([row.get("опис", ""), row.get("url", "")])).lower()
    for label, patterns in TEXT_LABEL_PATTERNS:
        if any(re.search(pattern, text) for pattern in patterns):
            return label
    return ""


def read_rows(csv_path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with csv_path.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        return list(reader), fill_phone_repair.ensure_columns(reader.fieldnames or [])


def write_rows(csv_path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def walk_json(value: Any) -> list[Any]:
    values = [value]
    if isinstance(value, dict):
        for child in value.values():
            values.extend(walk_json(child))
    elif isinstance(value, list):
        for child in value:
            values.extend(walk_json(child))
    return values


def image_from_jsonld(html: str, soup: BeautifulSoup) -> list[str]:
    urls: list[str] = []
    for script in soup.select('script[type="application/ld+json"]'):
        content = script.string or script.get_text("", strip=True)
        if not content:
            continue
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            continue
        for item in walk_json(parsed):
            if isinstance(item, dict) and "image" in item:
                value = item.get("image")
                if isinstance(value, str):
                    urls.append(value)
                elif isinstance(value, list):
                    urls.extend(str(url) for url in value)
                elif isinstance(value, dict) and value.get("url"):
                    urls.append(str(value["url"]))
    return urls


def split_srcset(value: str) -> list[str]:
    urls = []
    for part in (value or "").split(","):
        url = part.strip().split(" ", 1)[0]
        if url:
            urls.append(url)
    return urls


def extract_image_urls(html: str, page_url: str, max_images: int) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []

    for selector in [
        'meta[property="og:image"]',
        'meta[name="twitter:image"]',
        'meta[property="twitter:image"]',
    ]:
        for node in soup.select(selector):
            if node.get("content"):
                urls.append(node["content"])

    urls.extend(image_from_jsonld(html, soup))

    for node in soup.select("img, source"):
        for attr in ("src", "data-src", "data-original", "data-lazy", "content"):
            if node.get(attr):
                urls.append(node[attr])
        for attr in ("srcset", "data-srcset"):
            urls.extend(split_srcset(node.get(attr, "")))

    normalized: list[str] = []
    for url in urls:
        url = urljoin(page_url, clean_text(url))
        if not url.startswith("http"):
            continue
        lower = url.lower()
        if lower.endswith((".svg", ".gif")):
            continue
        if any(
            skip in lower
            for skip in (
                "facebook.com/tr",
                "google-analytics",
                "doubleclick",
                "logo",
                "avatar",
                "placeholder",
                "sprite",
            )
        ):
            continue
        if url not in normalized:
            normalized.append(url)
        if len(normalized) >= max_images:
            break
    return normalized


def cache_path(image_dir: Path, row: dict[str, str], image_url: str) -> Path:
    row_id = re.sub(r"\D", "", row.get("№", "")) or hashlib.sha1(row.get("url", "").encode()).hexdigest()[:8]
    digest = hashlib.sha1(image_url.encode()).hexdigest()[:14]
    return image_dir / row_id / f"{digest}.jpg"


def fetch_html(session: requests.Session, url: str) -> str:
    response = session.get(url, timeout=30)
    response.raise_for_status()
    return response.text


def download_image(session: requests.Session, image_url: str, output_path: Path) -> bool:
    if output_path.exists() and output_path.stat().st_size > 0:
        return True
    try:
        response = session.get(image_url, headers=IMAGE_HEADERS, timeout=30)
        response.raise_for_status()
        image = Image.open(BytesIO(response.content)).convert("RGB")
        if image.width < 160 or image.height < 120:
            return False
        output_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(output_path, format="JPEG", quality=90)
        return True
    except Exception as error:
        log(f"  image skipped: {image_url} ({error})")
        return False


def collect_images_for_row(
    session: requests.Session,
    row: dict[str, str],
    image_dir: Path,
    max_images: int,
    sleep_seconds: float,
) -> list[Path]:
    row_dir = image_dir / (re.sub(r"\D", "", row.get("№", "")) or "unknown")
    cached = sorted(row_dir.glob("*.jpg"))
    if cached:
        return cached[:max_images]

    url = row.get("url", "")
    if not url:
        return []
    try:
        html = fetch_html(session, url)
    except requests.RequestException as error:
        log(f"  page skipped #{row.get('№')}: {error}")
        return []

    image_urls = extract_image_urls(html, url, max_images=max_images)
    paths = []
    for image_url in image_urls:
        path = cache_path(image_dir, row, image_url)
        if download_image(session, image_url, path):
            paths.append(path)
    time.sleep(sleep_seconds)
    return paths[:max_images]


def image_features(path: Path) -> list[float]:
    image = Image.open(path).convert("RGB").resize((224, 224))
    arr = np.asarray(image, dtype=np.float32) / 255.0
    rgb_mean = arr.mean(axis=(0, 1))
    rgb_std = arr.std(axis=(0, 1))

    bgr = cv2.cvtColor((arr * 255).astype(np.uint8), cv2.COLOR_RGB2BGR)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV).astype(np.float32) / 255.0
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 80, 160)
    laplacian = cv2.Laplacian(gray, cv2.CV_64F)

    rg = np.abs(arr[:, :, 0] - arr[:, :, 1])
    yb = np.abs(0.5 * (arr[:, :, 0] + arr[:, :, 1]) - arr[:, :, 2])
    colorfulness = float(math.sqrt(rg.var() + yb.var()) + 0.3 * math.sqrt(rg.mean() ** 2 + yb.mean() ** 2))

    hist_features = []
    for channel in range(3):
        hist, _ = np.histogram(arr[:, :, channel], bins=8, range=(0, 1), density=True)
        hist_features.extend((hist / max(hist.sum(), 1e-9)).tolist())

    values = [
        *rgb_mean.tolist(),
        *rgb_std.tolist(),
        float(hsv[:, :, 1].mean()),
        float(hsv[:, :, 1].std()),
        float(hsv[:, :, 2].mean()),
        float(hsv[:, :, 2].std()),
        float(gray.mean() / 255.0),
        float(gray.std() / 255.0),
        float((edges > 0).mean()),
        float(laplacian.var() / 10000.0),
        float((gray < 45).mean()),
        float((gray > 215).mean()),
        float((hsv[:, :, 1] < 0.12).mean()),
        colorfulness,
        float(Image.fromarray(gray).entropy() / 8.0),
        *hist_features,
    ]
    return values


def train_model(args: argparse.Namespace) -> dict[str, Any]:
    rows, _fieldnames = read_rows(Path(args.csv))
    if args.max_rows:
        rows = rows[: args.max_rows]
    manual_labels = load_manual_labels(Path(args.labels_csv))

    session = requests.Session()
    session.headers.update(HEADERS)
    image_dir = Path(args.image_dir)

    x_values: list[list[float]] = []
    y_values: list[str] = []
    labeled_rows = 0
    image_count = 0

    for row in rows:
        label = row_to_training_label(row, manual_labels)
        if not label:
            continue
        labeled_rows += 1
        log(f"training images #{row.get('№')} label={label}")
        paths = collect_images_for_row(session, row, image_dir, args.max_images, args.sleep)
        for path in paths:
            try:
                x_values.append(image_features(path))
                y_values.append(label)
                image_count += 1
            except Exception as error:
                log(f"  feature skipped: {path} ({error})")

    labels = sorted(set(y_values))
    if len(y_values) < args.min_samples or len(labels) < 2:
        raise SystemExit(
            f"Not enough labeled photo samples: samples={len(y_values)}, classes={labels}. "
            "Fill more `ремонт` labels manually or lower --min-samples."
        )

    x = np.asarray(x_values, dtype=np.float64)
    scaler = StandardScaler()
    x_scaled = scaler.fit_transform(x)
    model = LogisticRegression(max_iter=1000, class_weight="balanced", random_state=42)
    model.fit(x_scaled, y_values)
    accuracy = float(model.score(x_scaled, y_values))

    payload = {
        "kind": "repair_photo_model",
        "version": 1,
        "classes": model.classes_.tolist(),
        "feature_names": FEATURE_NAMES,
        "scaler_mean": scaler.mean_.tolist(),
        "scaler_scale": scaler.scale_.tolist(),
        "coef": model.coef_.tolist(),
        "intercept": model.intercept_.tolist(),
        "training": {
            "labeled_rows": labeled_rows,
            "manual_labeled_rows": len(manual_labels),
            "photo_samples": image_count,
            "training_accuracy": accuracy,
            "image_dir": str(image_dir),
            "labels_csv": str(Path(args.labels_csv)),
        },
    }

    model_path = Path(args.model)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    model_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload["training"] | {"model": str(model_path), "classes": payload["classes"]}


def softmax(values: np.ndarray) -> np.ndarray:
    values = values - np.max(values)
    exp = np.exp(values)
    return exp / exp.sum()


def load_model(model_path: Path) -> dict[str, Any]:
    payload = json.loads(model_path.read_text(encoding="utf-8"))
    if payload.get("kind") != "repair_photo_model":
        raise SystemExit(f"Invalid repair photo model: {model_path}")
    return payload


def predict_one(features: list[float], model: dict[str, Any]) -> dict[str, float]:
    x = np.asarray(features, dtype=np.float64)
    mean = np.asarray(model["scaler_mean"], dtype=np.float64)
    scale = np.asarray(model["scaler_scale"], dtype=np.float64)
    x_scaled = (x - mean) / np.where(scale == 0, 1.0, scale)
    coef = np.asarray(model["coef"], dtype=np.float64)
    intercept = np.asarray(model["intercept"], dtype=np.float64)
    classes = model["classes"]

    if len(classes) == 2 and coef.shape[0] == 1:
        score = float(np.dot(coef[0], x_scaled) + intercept[0])
        prob_positive = 1.0 / (1.0 + math.exp(-score))
        probabilities = np.asarray([1.0 - prob_positive, prob_positive])
    else:
        scores = coef.dot(x_scaled) + intercept
        probabilities = softmax(scores)
    return {label: float(prob) for label, prob in zip(classes, probabilities)}


def apply_model(args: argparse.Namespace) -> dict[str, Any]:
    rows, fieldnames = read_rows(Path(args.csv))
    model = load_model(Path(args.model))
    session = requests.Session()
    session.headers.update(HEADERS)
    image_dir = Path(args.image_dir)

    processed = 0
    updated = 0
    no_images = 0
    low_confidence = 0
    updated_rows: list[dict[str, str]] = []

    for row in rows:
        if args.max_rows and processed >= args.max_rows:
            break
        if not args.overwrite and clean_text(row.get("ремонт", "")):
            continue
        processed += 1
        log(f"predict repair #{row.get('№')} {row.get('source')}")
        paths = collect_images_for_row(session, row, image_dir, args.max_images, args.sleep)
        if not paths:
            no_images += 1
            continue

        probabilities: list[dict[str, float]] = []
        for path in paths:
            try:
                probabilities.append(predict_one(image_features(path), model))
            except Exception as error:
                log(f"  prediction skipped: {path} ({error})")
        if not probabilities:
            no_images += 1
            continue

        averaged = {
            label: sum(item.get(label, 0.0) for item in probabilities) / len(probabilities)
            for label in model["classes"]
        }
        label, confidence = max(averaged.items(), key=lambda item: item[1])
        log(f"  prediction={label} confidence={confidence:.2f} images={len(probabilities)}")
        if confidence < args.min_confidence:
            low_confidence += 1
            continue

        row["ремонт"] = label
        updated += 1
        updated_rows.append({"№": row.get("№", ""), "ремонт": label, "confidence": f"{confidence:.3f}"})

    write_rows(Path(args.output), rows, fieldnames)
    return {
        "processed": processed,
        "updated": updated,
        "no_images": no_images,
        "low_confidence": low_confidence,
        "updated_rows": updated_rows,
        "output": args.output,
    }


def main() -> None:
    args = parse_args()
    if args.command == "train":
        result = train_model(args)
    else:
        result = apply_model(args)
    log(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
