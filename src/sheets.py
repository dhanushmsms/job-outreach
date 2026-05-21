"""Google Sheets CRM — one shared sheet, one tab per user."""

import logging
import time
from datetime import datetime
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials


def _sheets_retry(fn, retries=4, base_delay=15):
    """Retry a gspread call on 429 rate-limit errors with exponential backoff."""
    for attempt in range(retries):
        try:
            return fn()
        except gspread.exceptions.APIError as e:
            if "429" in str(e) and attempt < retries - 1:
                wait = base_delay * (2 ** attempt)
                logging.getLogger(__name__).warning(
                    "Sheets rate limit hit — retrying in %ss (attempt %d/%d)",
                    wait, attempt + 1, retries,
                )
                time.sleep(wait)
            else:
                raise

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

COLUMNS = [
    "Name", "Email", "Company", "Title", "Country",
    "LinkedIn URL", "Source", "Role Type", "Status",
    "Date Emailed", "Reply Date", "Notes", "Message ID", "Follow Up Sent",
    "Job Title",
]

STATUS_FOLLOW_UP = "follow_up_sent"

STATUS_SCRAPED = "scraped"
STATUS_EMAILED = "emailed"
STATUS_REPLIED = "replied"
STATUS_RESPONDED = "responded"
STATUS_NOT_INTERESTED = "not_interested"
STATUS_BOUNCED = "bounced"


class SheetsClient:
    def __init__(self, sheet_id: str, service_account_file: str):
        creds = Credentials.from_service_account_file(service_account_file, scopes=SCOPES)
        self._gc = gspread.authorize(creds)
        self._sheet_id = sheet_id
        self._spreadsheet = self._gc.open_by_key(sheet_id)

    def _get_or_create_tab(self, user_name: str) -> gspread.Worksheet:
        tab_name = user_name.split("@")[0].replace(" ", "_")
        try:
            ws = _sheets_retry(lambda: self._spreadsheet.worksheet(tab_name))
        except gspread.WorksheetNotFound:
            ws = self._spreadsheet.add_worksheet(title=tab_name, rows=1000, cols=len(COLUMNS))
            logger.info(f"Created new tab: {tab_name}")

        # Always verify row 1 is the correct header — update in-place or insert if missing
        first_row = _sheets_retry(lambda: ws.row_values(1))
        if first_row != COLUMNS:
            if first_row and first_row[0] == "Name":
                # Existing header — update in place (handles new columns being added)
                logger.info(f"Updating header row in tab: {tab_name}")
                _sheets_retry(lambda: ws.update("A1", [COLUMNS]))
            else:
                # No header at all — insert one
                logger.info(f"Inserting header row into tab: {tab_name}")
                ws.insert_row(COLUMNS, index=1)
            ws.format(f"A1:{chr(ord('A') + len(COLUMNS) - 1)}1",
                      {"textFormat": {"bold": True}})
        return ws

    def _all_rows(self, ws: gspread.Worksheet) -> list[dict]:
        """Return all data rows as dicts, mapped to COLUMNS regardless of sheet state."""
        all_values = _sheets_retry(ws.get_all_values)
        if not all_values:
            return []
        # Skip row 1 if it's the header
        header = all_values[0]
        data_rows = all_values[1:] if header == COLUMNS else all_values
        result = []
        for row in data_rows:
            # Pad or trim row to match COLUMNS length
            padded = (row + [""] * len(COLUMNS))[:len(COLUMNS)]
            result.append(dict(zip(COLUMNS, padded)))
        return result

    def add_contacts(self, contacts: list[dict], user_name: str) -> int:
        """Add new contacts, skip duplicates by email or company+name. Returns count added."""
        ws = self._get_or_create_tab(user_name)
        all_rows = self._all_rows(ws)
        existing_emails = {r["Email"].lower() for r in all_rows if r.get("Email")}
        existing_keys = {
            f"{r.get('Name','').lower()}|{r.get('Company','').lower()}"
            for r in all_rows
        }
        added = 0
        rows_to_append = []
        for c in contacts:
            email = (c.get("email") or "").lower().strip()
            dedup_key = f"{c.get('name','').lower()}|{c.get('company','').lower()}"
            # skip if email already seen or name+company already seen
            if (email and email in existing_emails) or dedup_key in existing_keys:
                continue
            if email:
                existing_emails.add(email)
            existing_keys.add(dedup_key)
            rows_to_append.append([
                c.get("name", ""),
                email,
                c.get("company", ""),
                c.get("title", ""),
                c.get("country", ""),
                c.get("linkedin_url", ""),
                c.get("source", ""),
                c.get("role_type", ""),
                STATUS_SCRAPED,
                "", "", "", "", "",  # Date Emailed, Reply Date, Notes, Message ID, Follow Up Sent
                c.get("job_title", ""),
            ])
            added += 1
        if rows_to_append:
            # Use explicit row position to avoid gspread append_rows column-offset bug
            current_rows = len(_sheets_retry(ws.get_all_values))
            _sheets_retry(lambda r=rows_to_append, cr=current_rows: ws.update(
                values=r, range_name=f"A{cr + 1}"
            ))
        logger.info(f"Added {added} new contacts for {user_name}")
        return added

    def get_contacts_by_status(self, status: str, user_name: str) -> list[dict]:
        ws = self._get_or_create_tab(user_name)
        return [r for r in self._all_rows(ws) if r.get("Status") == status]

    def get_all_contacts(self, user_name: str) -> list[dict]:
        ws = self._get_or_create_tab(user_name)
        return self._all_rows(ws)

    def _find_row(self, ws: gspread.Worksheet, email: str) -> Optional[int]:
        """Return 1-based row index for the given email, or None."""
        col_values = _sheets_retry(lambda: ws.col_values(2))
        email_lower = email.lower()
        for i, val in enumerate(col_values):
            if val.lower() == email_lower:
                return i + 1
        return None

    def _batch_update(self, ws: gspread.Worksheet, updates: list[tuple]):
        """Apply multiple cell updates in one API call. updates = [(row, col, value), ...]"""
        body = {
            "valueInputOption": "RAW",
            "data": [
                {"range": gspread.utils.rowcol_to_a1(r, c), "values": [[v]]}
                for r, c, v in updates
            ],
        }
        _sheets_retry(lambda: ws.spreadsheet.values_batch_update(body=body))

    def update_status(self, email: str, status: str, user_name: str, notes: str = "") -> bool:
        ws = self._get_or_create_tab(user_name)
        row = self._find_row(ws, email)
        if row is None:
            return False
        updates = [(row, COLUMNS.index("Status") + 1, status)]
        if notes:
            updates.append((row, COLUMNS.index("Notes") + 1, notes))
        self._batch_update(ws, updates)
        return True

    # ── Row highlighting ───────────────────────────────────────────────────────

    def _highlight_row(self, ws: gspread.Worksheet, row: int, rgb: tuple[float, float, float]):
        """Apply a background colour to the entire data row."""
        n_cols = len(COLUMNS)
        last_col_letter = chr(ord("A") + n_cols - 1)
        ws.format(
            f"A{row}:{last_col_letter}{row}",
            {"backgroundColor": {"red": rgb[0], "green": rgb[1], "blue": rgb[2]}},
        )

    def mark_emailed(self, email: str, user_name: str, message_id: str = "") -> bool:
        ws = self._get_or_create_tab(user_name)
        row = self._find_row(ws, email)
        if row is None:
            return False
        updates = [
            (row, COLUMNS.index("Status") + 1, STATUS_EMAILED),
            (row, COLUMNS.index("Date Emailed") + 1, datetime.now().strftime("%Y-%m-%d %H:%M")),
        ]
        if message_id:
            updates.append((row, COLUMNS.index("Message ID") + 1, message_id))
        self._batch_update(ws, updates)
        _sheets_retry(lambda: self._highlight_row(ws, row, (0.85, 0.93, 0.83)))
        return True

    def flag_mismatch(self, email: str, user_name: str, reason: str) -> bool:
        """🟠 Orange — name/email mismatch flagged, email was NOT sent."""
        ws = self._get_or_create_tab(user_name)
        row = self._find_row(ws, email)
        if row is None:
            return False
        self._batch_update(ws, [
            (row, COLUMNS.index("Notes") + 1, f"MISMATCH: {reason}"),
            (row, COLUMNS.index("Status") + 1, "mismatch_flagged"),
        ])
        _sheets_retry(lambda: self._highlight_row(ws, row, (1.0, 0.85, 0.6)))
        return True

    def mark_replied(self, email: str, user_name: str) -> bool:
        ws = self._get_or_create_tab(user_name)
        row = self._find_row(ws, email)
        if row is None:
            return False
        self._batch_update(ws, [
            (row, COLUMNS.index("Status") + 1, STATUS_REPLIED),
            (row, COLUMNS.index("Reply Date") + 1, datetime.now().strftime("%Y-%m-%d %H:%M")),
        ])
        return True

    def get_emailed_message_ids(self, user_name: str) -> dict[str, str]:
        """Return {message_id: email} for all emailed contacts."""
        ws = self._get_or_create_tab(user_name)
        rows = self._all_rows(ws)
        return {
            r["Message ID"]: r["Email"]
            for r in rows
            if r.get("Message ID") and r.get("Status") == STATUS_EMAILED
        }

    def get_followup_contacts(self, user_name: str, follow_up_days: int = 7) -> list[dict]:
        """Return contacts emailed N+ days ago with no reply and no follow-up yet."""
        ws = self._get_or_create_tab(user_name)
        rows = self._all_rows(ws)
        result = []
        for r in rows:
            if r.get("Status") != STATUS_EMAILED:
                continue
            if r.get("Follow Up Sent"):
                continue
            date_emailed = r.get("Date Emailed", "")
            if not date_emailed:
                continue
            try:
                emailed_dt = datetime.strptime(date_emailed[:16], "%Y-%m-%d %H:%M")
                days_since = (datetime.now() - emailed_dt).days
                if days_since >= follow_up_days:
                    result.append(r)
            except Exception:
                continue
        return result

    def mark_follow_up_sent(self, email: str, user_name: str) -> bool:
        ws = self._get_or_create_tab(user_name)
        row = self._find_row(ws, email)
        if row is None:
            return False
        self._batch_update(ws, [(row, COLUMNS.index("Follow Up Sent") + 1, datetime.now().strftime("%Y-%m-%d %H:%M"))])
        return True

    def get_daily_email_count(self, user_name: str) -> int:
        """Count emails sent today."""
        ws = self._get_or_create_tab(user_name)
        today = datetime.now().strftime("%Y-%m-%d")
        rows = self._all_rows(ws)
        return sum(1 for r in rows if r.get("Date Emailed", "").startswith(today))
