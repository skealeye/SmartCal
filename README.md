# SmartCal

SmartCal is a Windows tray calendar app. It merges your personal and work Google calendars into one agenda popup, shows week and month views, sends toast reminders before meetings, and has a chat assistant (Claude) that can add, move and delete events, set reminders and find free time.

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
powershell -File scripts\build.ps1          # builds dist\SmartCal\SmartCal.exe
powershell -File scripts\install-local.ps1  # copies it to %LOCALAPPDATA%\Programs\SmartCal + Start Menu shortcut
```

On first run SmartCal registers itself to start at login (turn this off in Settings). The packaged exe is registered by its own path.

To update an installed copy, quit SmartCal first (tray icon > Quit) and run the install script again; to clear old files from earlier versions, delete `%LOCALAPPDATA%\Programs\SmartCal` first.

Autostart from source runs `pythonw.exe run.py`. If the repo is on the Z: network share, that can run before the share is mounted at login and silently fail. For autostart, use the packaged build copied to a local folder (the install script does this).

## What the assistant does

- Asks before deleting or changing meetings that have attendees, and asks one confirmation at a time.
- Every change can be reversed with **Undo** (the last 20 changes are kept).
- Never emails attendees.
- Handles reminders ("remind me to call Sam in 20 minutes") and adds a Maps link when you ask for directions.

## Settings

Open from the tray icon menu: accounts (add/remove), reminder lead time, start with Windows, and the other options shown in the dialog.

## Troubleshooting

- Log: `%APPDATA%\SmartCal\smartcal.log`.
- Calendar stops syncing after about a week: sign in again (see 7-day expiry above).
- Chat says no key: check `%APPDATA%\SmartCal\.env`.
- Test without touching your real data: set `SMARTCAL_NO_SYSTEM_WRITES=1` (no Run key or toast app-id registry writes) and `SMARTCAL_DATA_DIR=<folder>` (use that folder instead of `%APPDATA%\SmartCal`).

## Known limits

- Approvals in chat understand English only.
- Google Calendar only.
- The week and month windows are read-only.
- Declined events still count as busy in free-time search.

## Spike script

`python scripts/spike_google.py personal` (and `work`) signs in and prints today's events; useful to check an account can connect.

## Tests

```
pip install -r requirements-dev.txt
pytest
```
