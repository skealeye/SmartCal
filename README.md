# SmartCal

SmartCal is a Windows tray calendar app. It merges your personal and work Google calendars into one agenda popup, shows week and month views, sends toast reminders before meetings, and has a chat assistant (Claude) that can add, move and delete events, set reminders and find free time.

## How it works

- **Synced range:** SmartCal keeps your events from 7 days ago (or from the start of this month's grid, if that is earlier, so the current month view is always complete) to 60 days ahead, refreshed every 5 minutes. The week and month windows show **Not synced for these dates** past that range. The assistant only trusts the dates every account has actually synced: if an account needs you to sign in again, was offline, or has not finished its first sync, it says it cannot see those dates (or that the account hasn't finished syncing yet) instead of calling them free. If any account's last successful sync is more than 2 hours old, it will not answer availability questions and says how long ago that account last synced; its answers always say how old the calendar data is.
- **Join buttons and plain-text descriptions:** meetings with a Zoom, Google Meet or Teams link show a prominent "Join Zoom meeting" (or Meet/Teams) button in the event details and a compact "Join" button on the agenda popup row. Event descriptions from Google are shown as plain text (HTML tags are converted to line breaks, never rendered).
- **Clickable links in meeting notes:** `http://` and `https://` addresses in a description are clickable in the event details and in the popup's meeting details pop-out, and open in your browser. Nothing else becomes a link (no `javascript:`, `file:`, `mailto:` or bare `www.`), and hovering a link shows its full address.
- **Meeting details in the popup:** click a meeting in the tray popup (or Tab to it and press Enter or Space) and its details slide out from behind the popup in a pop-out on its left (on the right when the popup sits at the left edge of the screen). The pop-out is the same size as the popup and lines up with it: title, time, calendar (left out when the calendar is just named after your email address, as an account's main calendar is; named calendars such as Holidays or Out of Office still show), location, notes (they fill the remaining height and scroll beyond it), the Join button, and **Hide…** / **Color…**. The popup list stays compact and the selected meeting is highlighted. Clicking another meeting shows its details in the same pop-out, without sliding again. There is no close button: click the selected meeting again (or press Esc) and the pop-out slides back behind the popup; it also closes whenever the popup closes. Clicking in the pop-out keeps the popup open; clicking anywhere else closes both. The small "Join" button on the row joins without opening the details. Keyboard: Tab in the popup moves between meetings and their compact Join buttons; Enter or Space opens the pop-out and moves focus into it, where Tab goes Join → Hide… → Color…; Esc returns to the meeting.
- **Animations:** the tray popup slides in from the taskbar side and fades in when it opens, and the details pop-out slides out and back. To turn them off, switch off Windows **Animation effects** (Settings > Accessibility > Visual effects), or set the environment variable `SMARTCAL_NO_ANIMATIONS=1`; everything then appears instantly. If the sliding pop-out flickers on your display, set `SMARTCAL_SLIDE_MODE=fade`: the pop-out then stays beside the popup and only fades in and out.
- **Agenda rows:** each popup row shows the colored stripe, the time, the title and (with a meeting link) **Join**. There is no calendar or email line under the title; open the details to see the calendar.
- **Right-click a meeting:** right-click a meeting in the tray popup or in the Day/Week/Month window for **Hide this meeting**, **Hide all in this series** (recurring meetings only) and **Color ▸** (the 10 colors and **Reset to calendar color**). Hiding happens at once, with no confirmation. Right-clicking never selects the meeting or closes the popup, and works while the details pop-out is open.
- **Hide a meeting:** **Hide…** (popup details or event details) and the right-click menu offer **Hide this meeting** and, for a recurring meeting, **Hide all in this series**. A hidden meeting disappears from the popup, the next-up line, the Day/Week/Month window and reminder toasts. Hiding is local to SmartCal: nothing changes in Google, and the assistant still sees the meeting (it still counts as busy for free-time and availability answers). After hiding, the popup says **Hidden. Restore from the tray icon.** with an **Undo** button for about 8 seconds.
- **Restore hidden meetings:** right-click the tray icon > **Restore hidden ▸** lists every hidden meeting (with its date) and hidden series (marked "(series)"); click one to bring it back at once, or **Restore all hidden**. With nothing hidden the entry is grayed out and reads **Nothing hidden**. **Settings > Meetings** still lists hidden and colored meetings too.
- **Show or hide an account:** right-click the tray icon > **Accounts ▸** has a check mark per connected account; uncheck one (for example your personal account, to see only work meetings) and its meetings disappear from the popup, the next-up line and tray tooltip, the Day/Week/Month window and reminder toasts right away. The same choice is the **Show in SmartCal** box per account in **Settings > Accounts** (applied on OK). A hidden account keeps syncing, so showing it again is instant, and its meetings are still real commitments: the assistant still sees them and free-time search still counts them as busy. If every account is hidden, the popup says **All accounts hidden — right-click the tray icon > Accounts**.
- **Color a meeting:** **Color…** picks one of 10 colors (or **Reset to calendar color**). A recurring meeting's color applies to the whole series, so every occurrence keeps the same color; a one-off meeting is colored on its own. The color shows in the popup stripe, the window and the event details. Local to SmartCal only; Google is not changed.
- **One copy running:** starting SmartCal again (for example from the Start Menu while it already started at login) just opens the running copy's popup. If the running copy does not answer within about 1.5 seconds (it hung, or a stale lock was left behind), the new copy starts anyway and says so in a tray message, so you are never left with no SmartCal.
- **No email addresses as calendar names:** a calendar named after an account's email address is not shown in the popup rows, the details or reminder toasts (it says nothing you don't know); other calendar names are.
- **Declined meetings** are left out of the agenda, reminders and free-time search; the week and month windows show them dimmed and struck through.
- **Shared calendars** (for example your personal calendar also subscribed in the work account) count once: events and reminders are not doubled, and in Settings the calendar is set up under the account that owns it.
- **Sign-in:** while a Google sign-in waits for the browser, the tray menu shows **Cancel Google sign-in** (and Settings > Accounts shows **Signing in… Cancel**), so closing the browser window never blocks a retry.

## One-time setup

### Google Cloud project

1. Go to <https://console.cloud.google.com/> and sign in with your personal account.
2. Create a project: top bar project picker > **New Project** > name it `SmartCal` > **Create**. Make sure it is selected.
3. Enable the API: **APIs & Services > Library**, search for **Google Calendar API**, click **Enable**.
4. Configure the consent screen: **APIs & Services > OAuth consent screen** (or **Google Auth Platform**).
   - User type: **External**. Fill in app name (`SmartCal`) and your email for support/contact.
   - Leave publishing status as **Testing** (see the 7-day caveat below).
   - Under **Test users**, click **Add users** and add **both** account emails (personal and work).
5. Create the OAuth client: **APIs & Services > Credentials > Create credentials > OAuth client ID**, application type **Desktop app**, name `SmartCal`, **Create**.
6. Click **Download JSON** on the new client. Save it as:

   `%APPDATA%\SmartCal\client_secret.json`

   (create the `SmartCal` folder if needed; paste `%APPDATA%` into File Explorer's address bar to find it). Never commit or share this file.

### Sign in (again)

SmartCal now asks for **full calendar access** (it can edit events, not just read them). If you used an earlier version, open **Settings** and sign in once more for **each** account. Google shows "this app isn't verified": click **Advanced > Go to SmartCal (unsafe)**; that is expected for your own app.

**Work account:** a Workspace admin may block third-party apps ("access blocked" / "app not allowed"). If so, ask the admin to allow the OAuth client, or use the app with the personal account only.

**7-day token expiry:** while the consent screen is in **Testing**, Google expires refresh tokens after 7 days and you must sign in again weekly. Fix: **Google Auth Platform > Audience > Publish app** (move to production). It still shows the unverified warning, but tokens stop expiring.

### Anthropic key (assistant chat)

Create `%APPDATA%\SmartCal\.env` (see `.env.example`) containing:

```
ANTHROPIC_API_KEY=your-key-here
```

An `ANTHROPIC_API_KEY` environment variable takes precedence. SmartCal does not read a `.env` from the current folder. Never commit or share the key.

## Run from source

```
pip install -r requirements.txt
python run.py
```

## Build and install

```
pip install -r requirements-dev.txt
powershell -File scripts\build.ps1          # builds %LOCALAPPDATA%\SmartCal-build\dist\SmartCal\SmartCal.exe
powershell -File scripts\install-local.ps1  # copies it to %LOCALAPPDATA%\Programs\SmartCal + Start Menu shortcut
```

The build goes to the local disk (`%LOCALAPPDATA%\SmartCal-build\dist` and `\work`), not the repo's `dist\`: building on the Z: network share fails now and then. Only the `.spec` file is written to the repo's `build\` folder. The install script falls back to the repo's `dist\SmartCal` if no local build exists.

On first run SmartCal registers itself to start at login (turn this off in Settings). The packaged exe is registered by its own path.

To update an installed copy, quit SmartCal first (tray icon > Quit) and run the install script again; to clear old files from earlier versions, delete `%LOCALAPPDATA%\Programs\SmartCal` first.

Autostart from source runs `pythonw.exe run.py`. If the repo is on the Z: network share, that can run before the share is mounted at login and silently fail. For autostart, use the packaged build copied to a local folder (the install script does this).

## What the assistant does

- Asks before deleting or changing meetings that have attendees, and asks one confirmation at a time.
- Every change can be reversed with **Undo** (the last 20 changes are kept). If the event was changed in Google since (for example a guest replied), Undo stops and offers **Undo anyway**, which overwrites those changes after you confirm.
- Never emails attendees.
- Handles reminders ("remind me to call Sam in 20 minutes") and adds a Maps link when you ask for directions.

## Settings

Open from the tray icon menu: accounts (add/remove), reminder lead time, start with Windows, and the other options shown in the dialog.

**Meetings** tab: lists hidden meetings (title, "this meeting" or "whole series", date) with an **Unhide** button each, and colored meetings with a **Reset** button each. Changes apply when you click **OK**.

Hidden meetings and meeting colors are stored in `%APPDATA%\SmartCal\settings.json` (keys `hidden_events`, `event_colors`, `event_color_titles`; at most 500 of each). They are never sent to Google. When Google splits a series in two ("this and following" edits), the new series starts with a fresh hide/color state.

## Troubleshooting

- Log: `%APPDATA%\SmartCal\smartcal.log`.
- Calendar stops syncing after about a week: sign in again (see 7-day expiry above).
- Chat says no key: check `%APPDATA%\SmartCal\.env`.
- Test without touching your real data: set `SMARTCAL_NO_SYSTEM_WRITES=1` (no Run key or toast app-id registry writes) and `SMARTCAL_DATA_DIR=<folder>` (use that folder instead of `%APPDATA%\SmartCal`).

## Known limits

- Approvals in chat understand English only.
- Google Calendar only.
- The week and month windows are read-only (apart from SmartCal-local Hide… and Color…).

## Spike script

`python scripts/spike_google.py personal` (and `work`) signs in and prints today's events; useful to check an account can connect.

## Tests

```
pip install -r requirements-dev.txt
pytest
```

Tests run offscreen by default (`tests/conftest.py` forces `QT_QPA_PLATFORM=offscreen`, even if your shell sets another platform), so no windows appear on your screen. To run them on the real display on purpose, set `SMARTCAL_TESTS_REAL_DISPLAY=1`.
