# Apartment Voucher Investment Analyzer

Portfolio project for collecting and enriching apartment listings in Ukraine for a constrained purchase scenario: Odesa or Dnipro, 35,000-45,000 USD, with later rental use in mind.

The project turns fragmented real-estate listings into a structured CSV/Google Sheets dataset with comparable fields: price, address, area, rooms, floor, publication date, repair state, distance to city center, Google Maps URL, and optional contact data.

## What It Does

- Collects apartment listings from REM.ua, DIM.RIA, OLX, and LUN browser flows.
- Normalizes a shared CSV schema for cross-source comparison.
- Extracts addresses, rooms, floors, publication dates, phone numbers, and repair state.
- Calculates approximate distance to city center.
- Generates Google Maps search URLs from addresses.
- Syncs the final table to Google Sheets.
- Includes a local photo-based model that classifies repair state into:
  - `радянський ремонт`
  - `євроремонт`
  - `під ремонт`
  - `косметичний ремонт`

## Why It Is Useful

Real-estate marketplaces expose inconsistent data. One source may have a good address but weak floor metadata; another has phone numbers hidden behind a browser interaction; another has enough photos to infer repair quality but no structured repair field.

This project demonstrates an end-to-end data product around that problem:

1. multi-source collection;
2. schema normalization;
3. enrichment with rule-based and ML-assisted extractors;
4. browser-assisted workflows for JS-heavy pages;
5. Google Sheets delivery for non-technical decision making.

## Repository Layout

```text
.
├── api.py                         # Local JSON API for listings and source operations
├── main.py                        # Main pipeline entrypoint
├── data/
│   ├── apartments_template.csv    # Public schema template
│   ├── sample_apartments.csv      # Anonymized sample rows
│   └── source_registry.csv        # Source registry
├── docs/
│   ├── api.md
│   ├── phone_enrichment.md
│   ├── repair_photo_model.md
│   └── sources.md
├── models/                        # Lightweight local model JSON files
├── scripts/                       # Collectors and enrichment scripts
└── requirements.txt
```

Private runtime data is intentionally excluded from Git:

- full collected CSV files;
- phone numbers;
- listing photo caches;
- Playwright browser profiles;
- logs;
- Google service account credentials.

## Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install chromium
```

Run the main pipeline:

```bash
python main.py --sources REM.ua,DIM.RIA,OLX --max-pages 1
```

Start the local API:

```bash
python api.py --host 127.0.0.1 --port 8000
```

Then open:

```text
http://127.0.0.1:8000/health
```

## Google Sheets Sync

Copy `.env.example` to `.env` or pass values directly:

```bash
python scripts/sync_to_google_sheets.py \
  --csv data/apartments_multi_source.csv \
  --spreadsheet YOUR_SPREADSHEET_ID \
  --sheet Аркуш1 \
  --service-account /path/to/service_account.json
```

## Repair Photo Model

The repair model is local and lightweight. It uses cached listing photos and manual or weak labels.

```bash
python scripts/export_repair_labels.py
python scripts/repair_photo_model.py train --labels-csv data/repair_photo_labels.csv
python scripts/repair_photo_model.py apply --overwrite --min-confidence 0.45
```

See [docs/repair_photo_model.md](docs/repair_photo_model.md).

## Phone Enrichment

Phone enrichment is separate from the main pipeline because some marketplaces reveal numbers only after a browser interaction.

```bash
python scripts/enrich_olx_phones_browser.py --headless --manual-wait 0 --phone-wait 15 --sleep 5
```

See [docs/phone_enrichment.md](docs/phone_enrichment.md).

## Portfolio Notes

This repository is structured as a public portfolio version of a real data workflow. The included sample data is anonymized. The system design, scripts, models, and docs are preserved; private listing exports and contact information are not committed.

## Tech Stack

- Python
- BeautifulSoup and Requests
- Playwright for browser-based collection
- scikit-learn, OpenCV, Pillow, NumPy for local photo features and repair classification
- Google Sheets API
- CSV-first data pipeline
