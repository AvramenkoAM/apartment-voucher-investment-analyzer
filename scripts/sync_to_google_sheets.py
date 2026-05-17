#!/usr/bin/env python3
"""Sync local apartment CSV to a Google Sheets spreadsheet."""

from __future__ import annotations

import argparse
import csv
import os
import re
from pathlib import Path

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CSV = PROJECT_ROOT / "data/apartments_multi_source.csv"
DEFAULT_SPREADSHEET_URL = os.getenv("GOOGLE_SPREADSHEET_ID", "")
DEFAULT_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "Аркуш1")
DEFAULT_SERVICE_ACCOUNT = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync apartment CSV to Google Sheets.")
    parser.add_argument("--csv", default=str(DEFAULT_CSV), help=f"CSV file to sync. Default: {DEFAULT_CSV}")
    parser.add_argument("--spreadsheet", default=DEFAULT_SPREADSHEET_URL, help="Google Sheets URL or spreadsheet ID.")
    parser.add_argument("--sheet", default=DEFAULT_SHEET_NAME, help=f"Target sheet tab. Default: {DEFAULT_SHEET_NAME}")
    parser.add_argument(
        "--service-account",
        default=DEFAULT_SERVICE_ACCOUNT,
        help="Service account JSON key. Can also be set via GOOGLE_SERVICE_ACCOUNT_JSON.",
    )
    return parser.parse_args()


def spreadsheet_id(value: str) -> str:
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", value)
    return match.group(1) if match else value


def read_csv(path: Path) -> list[list[str]]:
    with path.open(encoding="utf-8", newline="") as file:
        return list(csv.reader(file))


def get_sheet_id(service, spreadsheet_id_value: str, sheet_name: str) -> int:
    metadata = service.spreadsheets().get(spreadsheetId=spreadsheet_id_value).execute()
    for sheet in metadata.get("sheets", []):
        properties = sheet.get("properties", {})
        if properties.get("title") == sheet_name:
            return properties["sheetId"]
    available = ", ".join(sheet["properties"]["title"] for sheet in metadata.get("sheets", []))
    raise ValueError(f"Sheet tab '{sheet_name}' not found. Available tabs: {available}")


def sync_values(service, spreadsheet_id_value: str, sheet_name: str, values: list[list[str]]) -> None:
    service.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id_value,
        range=sheet_name,
        body={},
    ).execute()
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id_value,
        range=f"{sheet_name}!A1",
        valueInputOption="USER_ENTERED",
        body={"values": values},
    ).execute()


def format_sheet(service, spreadsheet_id_value: str, sheet_id: int, row_count: int, column_count: int) -> None:
    requests = [
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": column_count,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {"red": 0.88, "green": 0.94, "blue": 1.0},
                        "textFormat": {"bold": True},
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,textFormat)",
            }
        },
        {
            "updateSheetProperties": {
                "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}},
                "fields": "gridProperties.frozenRowCount",
            }
        },
        {
            "setBasicFilter": {
                "filter": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 0,
                        "endRowIndex": row_count,
                        "startColumnIndex": 0,
                        "endColumnIndex": column_count,
                    }
                }
            }
        },
        {
            "autoResizeDimensions": {
                "dimensions": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": 0,
                    "endIndex": column_count,
                }
            }
        },
    ]
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id_value,
        body={"requests": requests},
    ).execute()


def main() -> None:
    args = parse_args()
    if not args.spreadsheet:
        raise SystemExit("Provide --spreadsheet or GOOGLE_SPREADSHEET_ID.")
    if not args.service_account:
        raise SystemExit("Provide --service-account or GOOGLE_SERVICE_ACCOUNT_JSON.")
    csv_path = Path(args.csv)
    key_path = Path(args.service_account)
    spreadsheet_id_value = spreadsheet_id(args.spreadsheet)
    values = read_csv(csv_path)

    credentials = Credentials.from_service_account_file(str(key_path), scopes=SCOPES)
    service = build("sheets", "v4", credentials=credentials, cache_discovery=False)

    try:
        sheet_id = get_sheet_id(service, spreadsheet_id_value, args.sheet)
        sync_values(service, spreadsheet_id_value, args.sheet, values)
        format_sheet(service, spreadsheet_id_value, sheet_id, len(values), len(values[0]))
    except HttpError as error:
        client_email = getattr(credentials, "service_account_email", "")
        print(f"Google Sheets API error: {error}")
        if error.resp.status in {403, 404}:
            print(f"Share the spreadsheet with this service account as editor: {client_email}")
        raise SystemExit(1) from error

    print(
        f"Synced {len(values) - 1} data rows and {len(values[0])} columns "
        f"to {args.sheet}: {args.spreadsheet}"
    )


if __name__ == "__main__":
    main()
