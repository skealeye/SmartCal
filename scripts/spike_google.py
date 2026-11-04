"""Throwaway spike: sign in to a Google account and print today's events.

Usage: python scripts/spike_google.py <label>     (e.g. personal, work)
"""
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


def data_dir() -> Path:
    return Path(os.environ.get("APPDATA", str(Path.home()))) / "SmartCal"


def format_event(event: dict) -> str:
    """Format a Google Calendar event dict as 'HH:MM  Title  [Calendar]'.

    Expects optional keys 'calendar_name' (added by the caller).
    All-day events (start has 'date', not 'dateTime') show 'All day'.
    """
    start = event.get("start", {})
    if "dateTime" in start:
        when = datetime.fromisoformat(start["dateTime"]).strftime("%H:%M")
    else:
        when = "All day"
    title = event.get("summary") or "(no title)"
    calendar = event.get("calendar_name") or "?"
    return f"{when:<8} {title}  [{calendar}]"


def get_credentials(label: str):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    base = data_dir()
    secret = base / "client_secret.json"
    token_path = base / "spike_tokens" / f"{label}.json"
    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        if not secret.exists():
            sys.exit(f"Missing {secret}. See README.md for setup steps.")
        flow = InstalledAppFlow.from_client_secrets_file(str(secret), SCOPES)
        creds = flow.run_local_server(port=0)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("Usage: python scripts/spike_google.py <label>   (e.g. personal)")
    from googleapiclient.discovery import build

    label = sys.argv[1]
    service = build("calendar", "v3", credentials=get_credentials(label))

    primary = service.calendars().get(calendarId="primary").execute()
    print(f"Signed in as: {primary.get('id')}  (label: {label})")

    now = datetime.now().astimezone()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)

    events = []
    page = None
    while True:
        resp = service.calendarList().list(pageToken=page).execute()
        for cal in resp.get("items", []):
            name = cal.get("summaryOverride") or cal.get("summary")
            ev_page = None
            while True:
                r = service.events().list(
                    calendarId=cal["id"], timeMin=start.isoformat(),
                    timeMax=end.isoformat(), singleEvents=True,
                    orderBy="startTime", pageToken=ev_page).execute()
                for ev in r.get("items", []):
                    ev["calendar_name"] = name
                    events.append(ev)
                ev_page = r.get("nextPageToken")
                if not ev_page:
                    break
        page = resp.get("nextPageToken")
        if not page:
            break

    def sort_key(e):
        s = e["start"]
        return s.get("dateTime") or s.get("date") + "T00:00:00"

    events.sort(key=sort_key)
    print(f"Today's events ({start.date()}): {len(events)}")
    for ev in events:
        print("  " + format_event(ev))


if __name__ == "__main__":
    main()
