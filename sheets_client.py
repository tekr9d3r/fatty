import re
from datetime import datetime, timedelta
from typing import Optional

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

SPREADSHEET_ID = "1OxZdnPLmU8V3tMs7pdCHgyu0ORD4mqp1XvQx_8suZRc"
SHEET_NAME = "Fatty Log"
SETTINGS_SHEET = "Settings"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def build_service(service_account_json_path: str):
    creds = Credentials.from_service_account_file(service_account_json_path, scopes=SCOPES)
    return build("sheets", "v4", credentials=creds)


def append_row(service, row: list) -> int:
    """Append one row to the sheet. Returns the 1-based row index of the new row."""
    result = (
        service.spreadsheets()
        .values()
        .append(
            spreadsheetId=SPREADSHEET_ID,
            range="A:F",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [row]},
        )
        .execute()
    )
    # updatedRange looks like "Sheet1!A47:F47" — parse the row number
    updated_range = result["updates"]["updatedRange"]
    match = re.search(r"[A-Z]+(\d+):", updated_range)
    if match:
        return int(match.group(1))
    raise ValueError(f"Could not parse row index from updatedRange: {updated_range}")


def read_recent_days(service, n_days: int) -> list[list]:
    """Return all rows where Date >= today - n_days (client-side filter)."""
    cutoff = (datetime.now() - timedelta(days=n_days - 1)).strftime("%Y-%m-%d")
    result = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=SPREADSHEET_ID,
            range="A:F",
        )
        .execute()
    )
    rows = result.get("values", [])
    # rows[0] is the header; filter data rows by date column (col 0)
    return [r for r in rows[1:] if len(r) >= 1 and r[0] >= cutoff]


def _get_sheet_id(service, sheet_name: str) -> int:
    meta = service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    for s in meta["sheets"]:
        if s["properties"]["title"] == sheet_name:
            return s["properties"]["sheetId"]
    raise ValueError(f"Sheet '{sheet_name}' not found")


def delete_row(service, row_index: int) -> None:
    """Delete the row at 1-based row_index using batchUpdate."""
    sheet_id = _get_sheet_id(service, SHEET_NAME)
    body = {
        "requests": [{
            "deleteDimension": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "ROWS",
                    "startIndex": row_index - 1,  # 0-based inclusive
                    "endIndex": row_index,          # 0-based exclusive
                }
            }
        }]
    }
    service.spreadsheets().batchUpdate(
        spreadsheetId=SPREADSHEET_ID,
        body=body,
    ).execute()


def _ensure_settings_sheet(service) -> None:
    meta = service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    existing = [s["properties"]["title"] for s in meta["sheets"]]
    if SETTINGS_SHEET not in existing:
        service.spreadsheets().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={"requests": [{"addSheet": {"properties": {"title": SETTINGS_SHEET}}}]},
        ).execute()
        service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{SETTINGS_SHEET}!A1:B1",
            valueInputOption="RAW",
            body={"values": [["user_id", "goal"]]},
        ).execute()


def get_user_goal(service, user_id: int) -> Optional[int]:
    _ensure_settings_sheet(service)
    result = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{SETTINGS_SHEET}!A:B",
    ).execute()
    for row in result.get("values", [])[1:]:
        if len(row) >= 2 and row[0] == str(user_id):
            try:
                return int(row[1])
            except ValueError:
                return None
    return None


def set_user_goal(service, user_id: int, goal: int) -> None:
    _ensure_settings_sheet(service)
    result = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{SETTINGS_SHEET}!A:B",
    ).execute()
    rows = result.get("values", [])
    for i, row in enumerate(rows[1:], start=2):
        if len(row) >= 1 and row[0] == str(user_id):
            service.spreadsheets().values().update(
                spreadsheetId=SPREADSHEET_ID,
                range=f"{SETTINGS_SHEET}!A{i}:B{i}",
                valueInputOption="RAW",
                body={"values": [[str(user_id), goal]]},
            ).execute()
            return
    service.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{SETTINGS_SHEET}!A:B",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": [[str(user_id), goal]]},
    ).execute()
