# SmartCal

SmartCal is a Windows tray calendar app. It merges your personal and work Google calendars into one agenda popup, shows week and month views, sends toast reminders before meetings, and has a chat assistant (Claude) that can add, move and delete events, set reminders and find free time.

## How it works

- **Synced range:** SmartCal keeps your events from 7 days ago (or from the start of this month's grid, if that is earlier, so the current month view is always complete) to 60 days ahead, refreshed every 5 minutes. The week and month windows show **Not synced for these dates** past that range. The assistant only trusts the dates every account has actually synced: if an account needs you to sign in again, was offline, or has not finished its first sync, it says it cannot see those dates (or that the account hasn't finished syncing yet) instead of calling them free. If any account's last successful sync is more than 2 hours old, it will not answer availability questions and says how long ago that account last synced; its answers always say how old the calendar data is.
- **Join buttons and plain-text descriptions:** meetings with a Zoom, Google Meet or Teams link show a prominent "Join Zoom meeting" (or Meet/Teams) button in the event details and a compact "Join" button on the agenda popup row. Event descriptions from Google are shown as plain text (HTML tags are converted to line breaks, never rendered).
- **Clickable links in meeting notes:** `http://` and `https://` addresses in a description are clickable in the event details and in the popup's meeting details pop-out, and open in your browser. Nothing else becomes a link (no `javascript:`, `file:`, `mailto:` or bare `www.`), and hovering a link shows its full address.
- **Meeting details in the popup:** click a meeting in the tray popup (or Tab to it and press Enter or Space) and its details slide out from behind the popup in a pop-out on its left (on the right when the popup sits at the left edge of the screen). The pop-out is as wide as the popup, lines up with its top, and is only as tall as its content (at most the popup's height; longer notes then scroll): a thin bar in the meeting's color, title, time, calendar (left out when the calendar is just named after your email address, as an account's main calendar is; named calendars such as Holidays or Out of Office still show), location, notes and the Join button. Hide and Color are on the meeting's right-click menu, not in the details. The popup list stays compact and the selected meeting is highlighted. Clicking another meeting shows its details in the same pop-out, without sliding again (the content cross-fades and the pop-out resizes smoothly). There is no close button: click the selected meeting again (or press Esc) and the pop-out slides back behind the popup; it also closes whenever the popup closes. Clicking in the pop-out keeps the popup open; clicking anywhere else closes both. The small "Join" button on the row joins without opening the details. Keyboard: Tab in the popup moves between meetings and their compact Join buttons; Enter or Space opens the pop-out and moves focus into it (onto Join, when there is one); Esc returns to the meeting.
- **Animations:** see **Appearance** below.
- **Meeting reminders (toasts):** a Windows notification pops up before each meeting, 10 minutes before by default (change it in **Settings > Calendars**, per calendar too; **At start** is 0 minutes). It shows the title, "Starts in N min" and the start time, the calendar's name (unless it is just your email address) and a **Join** button when the meeting has a Zoom, Meet or Teams link. Each meeting pops up once, also across restarts. No toast for meetings that are hidden in SmartCal, declined, cancelled, all-day, on a hidden calendar or account, or on a muted calendar. Toasts come on time to the second (SmartCal also checks every 30 seconds). If the PC was asleep or SmartCal was not running at the reminder time, a meeting still in progress gets one "Started 9:05 AM" toast (if it began less than a day ago); one that is already over gets none. A new lead time or mute applies to upcoming meetings as soon as you click OK.
- **Reminders (from Claude):** ask Claude in the popup, for example "remind me to call Sam in 20 minutes" or "remind me at 3 pm to leave", and it sets a reminder on this PC (from 1 minute to a year ahead, at most 5 per message and 500 waiting). When it is due, a Windows notification titled **Reminder** shows its text. Right-click the tray icon > **Reminders…** lists the upcoming ones (Today / Tomorrow / the date, and the time) with a **Cancel** button each; the list updates while it is open (a reminder Claude just set, one that fired). Reminders are kept in `%APPDATA%\SmartCal\reminders.json`, so they survive a restart; times are stored as exact moments, so a daylight-saving change or a time-zone change does not move them. A reminder that came due while the PC was asleep or SmartCal was not running pops up as soon as SmartCal runs again, with "(was due 3:45 PM)" (or the day, if it was not today) in front of its text; if more than three are due at once, the first two get their own notification and the rest one summary ("6 more were due: …"). Fired reminders are forgotten after two days. Clicking a reminder notification does nothing else.
- **Agenda rows:** each popup row shows the meeting's color (a side stripe, or the whole tile: see **Appearance**), the time, the title and (with a meeting link) **Join**. There is no calendar or email line under the title; open the details to see the calendar.
- **Right-click a meeting:** right-click a meeting in the tray popup or in the Day/Week/Month window for **Hide this meeting**, **Hide all in this series** (recurring meetings only), **Color ▸** (the 10 colors and **Reset to calendar color**) and **Color style ▸** (**Side stripe** / **Whole tile**). This menu is the only place for Hide and Color: the details views no longer have Hide…/Color… buttons. Hiding happens at once, with no confirmation. Right-clicking never selects the meeting or closes the popup, and works while the details pop-out is open.
- **Hide a meeting:** the right-click menu offers **Hide this meeting** and, for a recurring meeting, **Hide all in this series**. A hidden meeting disappears from the popup, the next-up line, the Day/Week/Month window and reminder toasts. Hiding is local to SmartCal: nothing changes in Google, and the assistant still sees the meeting (it still counts as busy for free-time and availability answers). The hidden row shrinks away, then the popup says **Hidden. Restore from the tray icon.** with an **Undo** button for about 8 seconds.
- **Restore hidden meetings:** right-click the tray icon > **Restore hidden ▸** lists every hidden meeting (with its date) and hidden series (marked "(series)"); click one to bring it back at once, or **Restore all hidden**. With nothing hidden the entry is grayed out and reads **Nothing hidden**. **Settings > Meetings** still lists hidden and colored meetings too.
- **Show or hide an account:** right-click the tray icon > **Accounts ▸** has a check mark per connected account; uncheck one (for example your personal account, to see only work meetings) and its meetings disappear from the popup, the next-up line and tray tooltip, the Day/Week/Month window and reminder toasts right away. The same choice is the **Show in SmartCal** box per account in **Settings > Accounts** (applied on OK). Only what reaches SmartCal solely through hidden accounts disappears: a calendar a shown account also has (a team or holidays calendar, or the hidden account's own calendar subscribed in the shown account) stays visible. When nothing is left today, the popup says how many accounts are hidden. Removing an account also forgets its hidden mark. A hidden account keeps syncing, so showing it again is instant (meetings already in progress don't trigger a late toast), and its meetings are still real commitments: the assistant still sees them and free-time search still counts them as busy. If every account is hidden, the popup says **All accounts hidden — right-click the tray icon > Accounts**.
- **Color a meeting:** right-click > **Color ▸** picks one of 10 colors (or **Reset to calendar color**). A recurring meeting's color applies to the whole series, so every occurrence keeps the same color; a one-off meeting is colored on its own. The color shows in the popup row (stripe or whole tile), the window, the pop-out's color bar and the event details. Local to SmartCal only; Google is not changed.
- **One copy running:** starting SmartCal again (for example from the Start Menu while it already started at login) just opens the running copy's popup. If the running copy does not answer within about 1.5 seconds (it hung, or a stale lock was left behind), the new copy starts anyway and says so in a tray message, so you are never left with no SmartCal.
- **No email addresses as calendar names:** a calendar named after an account's email address is not shown in the popup rows, the details or reminder toasts (it says nothing you don't know); other calendar names are.
- **Declined meetings** are left out of the agenda, reminders and free-time search; the week and month windows show them dimmed and struck through.
- **Shared calendars** (for example your personal calendar also subscribed in the work account) count once: events and reminders are not doubled, and in Settings the calendar is set up under the account that owns it.
- **Sign-in:** while a Google sign-in waits for the browser, the tray menu shows **Cancel Google sign-in** (and Settings > Accounts shows **Signing in… Cancel**), so closing the browser window never blocks a retry.

## Appearance

- **Rounded cards:** the tray popup and the details pop-out are rounded cards with a thin indigo edge (on Windows 11 the corners and the edge are drawn by Windows itself, so they are smooth; on Windows 10 SmartCal draws them). The edge color is one constant, `ACCENT` in `smartcal/ui/theme.py`.
- **Meeting color: Side stripe or Whole tile.** Right-click any meeting > **Color style ▸** (one click), or **Settings > General > Meeting color**. **Side stripe** (the default) is a colored bar on the left of each popup row. **Whole tile** fills the row with the meeting's exact color, with black or white text, whichever contrasts more (at least 4.5:1, so any of the 10 colors or a calendar color stays readable). The compact Join button turns into the text color with the fill color as its label. The selected meeting gets a ring and the next-up meeting a thin outline. The Day/Week/Month window follows the same choice: Side stripe tiles are a dark tint of the meeting's color with a colored bar (month: a colored dot), Whole tile ones are filled.
- **Day / Week / Month window:** each meeting tile shows its title first (semibold, up to three lines), then the time and the location in a softer tone, as far as the tile's height allows; anything cut off ends in "…" and the whole text is in the tooltip. Even a 15-minute meeting is tall enough for one readable line (title, plus its start time when there is room). Tiles keep 3 px between back-to-back meetings and 4 px between meetings at the same time, and have 7 px of inner padding. Today's column is tinted with the day number in an accent pill, weekends are a shade darker, and a red current-time line (above the tiles) glides to its new place every minute. Month days are rounded cards that show as many meetings as fit and a **+N more** chip that opens that day; an empty day in the Day view says **No meetings**. The window opens with its toolbar, day headers and meetings building in like the popup's cards (meetings in order of start time); switching Day / Week / Month or clicking ‹ / › / Today slides the old view out sideways (Day → Week → Month moves left, back moves right) while the new meetings come in from the other side. Tiles and month days light up softly on hover, and a tile shrinks a touch while pressed. Rapid clicks always end in a clean, final view, and the once-a-minute refresh or a sync changes nothing on screen unless a meeting changed (no flicker, scroll position kept). The window itself opens and closes the normal Windows way (it fades in once its first frame is drawn, so it never flashes white); on Windows 11 its edge has the same indigo accent as the popup.
- **Settings and Reminders dialogs:** open next to the tray icon, fully inside the screen's work area (never under the taskbar), in the popup's dark colors with rounded corners and the indigo edge on Windows 11. They appear only once drawn (no white flash, no jump into place): their content is complete from the first frame they show (never an empty dark area); they fade in quickly while the tab bar, rows and buttons settle the last few pixels into place (within about 0.2 s), or appear at once with animations off. Their buttons shrink a touch while pressed. SmartCal draws all its controls and title bars dark even when Windows is set to Light mode.
- **Animations:** the popup rises a little from the taskbar side and fades in, then its meetings build in like a stack of cards: each one springs up into place from slightly below and slightly smaller, one after another (about half a second in all; rows are clickable right away, and a refresh while the popup is open never replays it). The details pop-out slides out from behind the popup and back, and its lines (title, time, Join, notes) rise into place one after another, also when you switch to another meeting. A meeting row or a Join button shrinks a touch while pressed and springs back. Movements and size changes ease out with a little weight: they pass their end by a hair and settle back (the pop-out's slide settles without passing its spot, so no gap opens beside the popup); fades simply ease out. Smaller touches: the popup grows and shrinks smoothly (keeping its edge next to the tray in place) when meetings appear or disappear, the notice or sync-status line appears, or the chat drawer needs more room; rows fade their background on hover, selection and press; a hidden row shrinks away and a restored one fades in; the notice slides in and out; the pop-out cross-fades its content and resizes when you pick another meeting; buttons fade on hover. Nothing animates when a refresh changed nothing: the 30-second tick and background syncs leave the popup exactly as it is (same rows, scroll position, keyboard focus and selection); when one meeting changed, only its row is redrawn. **Closing** (clicking elsewhere, clicking the tray icon again, Esc, Join) plays the opening in reverse, in under a fifth of a second: the meetings fade and drop a little, bottom one first, then the header, while the popup sinks toward the taskbar and fades out (an open details pop-out first slides back under it). Clicking the tray icon again while it is still closing brings it straight back. Quitting, locking or sleeping Windows closes it at once.
- **Ask Claude drawer:** **Ask Claude…** at the bottom of the popup slides the chat down from the popup's top edge, over the agenda (a short popup first grows smoothly to make room). Its header, last messages and input row build in like the meetings do; new messages slide up into place, three dots pulse while Claude is thinking, and Send / New chat shrink a touch when pressed. **Hide chat**, the ✕ in its header or Esc slides it back up (Esc closes the chat first, the popup second). The chat and the details pop-out are never open together: opening one closes the other. With animations off it opens and closes at once; with `SMARTCAL_ANIMATIONS=basic` it only slides.
- **Animations setting:** **Settings > General > Animations** (**On** / **Follow Windows setting** / **Off**), or right-click the tray icon > **Animations ▸** for a one-click switch. Both take effect at once. The default is **On**: SmartCal animates even when Windows **Animation effects** (Settings > Accessibility > Visual effects) are off, so turning those off for Windows does not turn SmartCal's animations off. **Follow Windows setting** animates only when Windows Animation effects are on; **Off** never animates. The environment variable `SMARTCAL_NO_ANIMATIONS=1` still forces animations off whatever the setting. The log (`smartcal.log`) says at startup which mode is in effect and why.
- **Turning animations off:** choose **Off** above, or set `SMARTCAL_NO_ANIMATIONS=1`; everything then appears instantly, in the same final state. To keep only the popup slide-in and the pop-out slide (and the Day/Week/Month window's view slide) and turn off the smaller touches, set `SMARTCAL_ANIMATIONS=basic` (no card build, no press effect; closing only fades and sinks the popup). The pop-out slides out from under the popup (it waits for its first frame to be painted, so it never shows a white strip). If it ever flickers on your display, set `SMARTCAL_SLIDE_MODE=fade`: the pop-out then stays beside the popup and only fades in and out (`SMARTCAL_SLIDE_MODE=slide` picks the older masked slide).

## One-time setup

### Google sign-in

**Using a release build:** nothing to set up. Open **Settings**, click sign in, and approve in the browser. Google shows "this app isn't verified" (SmartCal is a free hobby app that has not paid for Google's review): click **Advanced > Go to SmartCal (unsafe)**. It only talks to your own calendars.

**Maintainer: publishing a build with a bundled client.** Do the Google Cloud steps below once, then save the downloaded client JSON as `bundled_client_secret.json` in the repo root (gitignored, never commit it) and run `scripts\build.ps1`; it is added to the app. Set the consent screen to **In production** (Audience > Publish app) so any Google user can sign in and tokens do not expire weekly. Unverified apps are limited to 100 users in total.

### Google Cloud project (your own client, or the maintainer's one-time setup)

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

### Claude API key and talking to SmartCal from Claude

**Ask Claude (inside SmartCal)** needs an Anthropic API key from <https://console.anthropic.com/> (billed to your API account; a Claude.ai subscription login cannot be used). Paste it in **Settings > General > Claude API key** and click **Save key**; it is stored in `%APPDATA%\SmartCal\.env` and used by the next chat. An `ANTHROPIC_API_KEY` environment variable takes priority.

**Claude Code / Claude Desktop to SmartCal (MCP).** The release folder has `SmartCal-mcp.exe`, a small MCP server. Connect Claude Code once: **Settings > General > Claude Code > Copy the Claude Code connect command**, paste it in a terminal (it looks like `claude mcp add smartcal -- "C:\...\SmartCal-mcp.exe"`). Then, in any Claude Code session, say things like *"tell SmartCal to remind me to check in on the Acme deal tomorrow at 10am"*, *"what is on my calendar Thursday?"*, *"list my SmartCal reminders"* or *"cancel reminder 12"*. Tools: `smartcal_now`, `smartcal_add_reminder`, `smartcal_list_reminders`, `smartcal_cancel_reminder`, `smartcal_agenda`. Reminders are handed to the running SmartCal through `%APPDATA%\SmartCal\inbox` (it checks every 5 seconds; if SmartCal is not running they are added at its next start), and the popup card and chime appear at the time you set. The server only touches reminders and reads your agenda; it cannot create or change Google events. From source, use `claude mcp add smartcal -- python smartcal_mcp.py` in the repo folder.

### Updates

The release build asks GitHub once a day (and shortly after start-up) whether a newer release of `skealeye/SmartCal` exists. If so, the tray menu shows **Update available: vX.Y.Z (download)**, which opens the release page, and one tray balloon announces it. SmartCal never downloads or installs anything itself: unzip the new release over the old folder (your settings and sign-ins live in `%APPDATA%\SmartCal` and are kept). Turn the check off with the tray menu's **Check for updates daily**, or set `SMARTCAL_NO_UPDATE_CHECK=1`. Running from source never checks.

**Maintainer:** to publish an update, bump `__version__`, run `scripts\build.ps1`, zip the `SmartCal` output folder, and create a GitHub Release whose tag is the version (e.g. `v0.13.1`) with the zip attached.

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

Open from the tray icon menu (**Settings…**). Esc or **Cancel** closes it without saving; **OK** (or Enter) saves everything at once.

**Calendars** tab: **Remind me before meetings** is the default lead time for meeting toasts (10 min unless you change it; 0 to 1440 minutes, 0 reads **At start**). Below it, per calendar: a box to show it in SmartCal, **Mute** (no toasts for its meetings) and its own lead time. Calendars that use the default follow it as you change it; one you set yourself keeps its own time. Mute and the lead time are grayed out for a calendar that is not shown (no toasts then anyway), and the lead time for a muted one.

**General** tab: **Start SmartCal when I sign in to Windows**, the **Claude model** (a dropdown: Fable 5.1, Opus 5.5, Sonnet 5.5 (the default) or Haiku 5.5; hover an entry to see its model id; a model id saved by an older version that is not in the list is kept and shown as **Custom: <id>**; the list is `CLAUDE_MODELS` in `smartcal/settings.py`), **Meeting color** (Side stripe / Whole tile) and **Animations** (On / Follow Windows setting / Off; see **Appearance**). Stored in `settings.json` as `tile_style` and `animations`.

**Meetings** tab: lists hidden meetings (title, "this meeting" or "whole series", date such as "Mon, Nov 9") with an **Unhide** button each, and colored meetings with a **Reset** button each. Changes apply when you click **OK**.

**Accounts** tab: your Google accounts with a **Show in SmartCal** box each, **Add…** and **Remove…** (select an account first). Adding or removing takes effect at once.

Hidden meetings and meeting colors are stored in `%APPDATA%\SmartCal\settings.json` (keys `hidden_events`, `event_colors`, `event_color_titles`; at most 500 of each). They are never sent to Google. When Google splits a series in two ("this and following" edits), the new series starts with a fresh hide/color state.

## Troubleshooting

- Log: `%APPDATA%\SmartCal\smartcal.log` (also Qt's own warnings). If SmartCal ever crashes outright, `%APPDATA%\SmartCal\crash.log` holds the Python stack at the moment of the crash.
- The popup flickers: start SmartCal with `SMARTCAL_DEBUG_UI=1`; the log then records, with millisecond timestamps, every rebuild, relayout, height change, effect, frame setup, drawer slide and activation change of the popup (no meeting titles), which shows what changed on screen while nobody touched it.
- Calendar stops syncing after about a week: sign in again (see 7-day expiry above).
- Chat says no key: check `%APPDATA%\SmartCal\.env`.
- Test without touching your real data: set `SMARTCAL_NO_SYSTEM_WRITES=1` (no Run key or toast app-id registry writes) and `SMARTCAL_DATA_DIR=<folder>` (use that folder instead of `%APPDATA%\SmartCal`).

## Known limits

- Approvals in chat understand English only.
- Google Calendar only.
- The week and month windows are read-only (apart from the SmartCal-local Hide and Color on an event's right-click menu).

## Spike script

`python scripts/spike_google.py personal` (and `work`) signs in and prints today's events; useful to check an account can connect.

## Tests

```
pip install -r requirements-dev.txt
pytest
```

Tests run offscreen by default (`tests/conftest.py` forces `QT_QPA_PLATFORM=offscreen`, even if your shell sets another platform), so no windows appear on your screen. To run them on the real display on purpose, set `SMARTCAL_TESTS_REAL_DISPLAY=1`.
