# SmartCal (working title) — Design

Date: 2026-11-04. Status: draft, awaiting builder check and owner approval.
Marked **[call]** = decided by the writer, not the owner. Everything else was said or agreed by the owner.

## Intent and why

The owner has a personal Google calendar and a work Google calendar. Checking "what are my meetings today" is an everyday chore that currently means keeping a Google Calendar web page open. SmartCal is a small Windows tray app that answers that question instantly, from both calendars at once, plus an assistant chat that can act on the calendar.

**The one thing that matters: it must be faster than opening Google Calendar.** If the app is slow or clunky, the owner will go back to the web page and the project has failed.

## Goals

1. **Tray popup** (lower-right Windows tray). Click the icon, a small compact window opens above it showing today's events from all connected calendars in one merged list ("Agenda list" layout):
   - Each event: color stripe per calendar, start time, title, calendar name.
   - The next upcoming event is highlighted, with a line such as "Next: Standup in 12 min".
   - Closes when clicking away. **[call]**
   - Day / Week / Month links at the bottom open a larger window with those views. **[call: larger window, since week and month do not fit the popup]**
2. **Multiple calendars.** Google first, with several accounts connected side by side (initially the owner's personal and work accounts). Design so other providers can be added later; do not build them now.
3. **Read and edit.** Events can be created, moved, and changed from the app (needed by the chat).
4. **Notifications.** Windows toast before each meeting. Default 10 minutes; the lead time is configurable. **[call: configurable per calendar, so work and personal can differ.]** Toast shows title, time, and a button to open the meeting link when the event has one. **[call]** Individual calendars can be muted for notifications. **[call]**
5. **Assistant chat** (small chat box in the app), powered by the Claude API using an API key stored on the owner's PC:
   - General assistant with calendar context, not only a scheduler.
   - **Applies changes immediately**, with no confirm step. **[call: after each change, show a one-line result such as "Done: moved Dentist to Thu 6:00" with an Undo button. Deleting an event is the one action it asks about first.]**
   - Examples from the owner: "Move my appt today to tomorrow"; "I have to take a meeting with XXX, set something up for the best time tomorrow"; "Remind me to XXXX in 20 minutes"; "Set an appointment at XXX nail salon, include directions."
   - Reminders ("in 20 minutes") fire the same kind of Windows notification as meetings.
   - For appointments with a place, put the address in the event location and add a Google Maps directions link to the event description. **[call]**
   - The chat is part of version 1, not a later add-on.

## Non-goals

- Other calendar providers (Outlook, iCloud) in v1. Only keep the design open to them.
- Mac, Linux, or mobile.
- Auto-scheduling or task planning like Motion/Reclaim.
- Sharing, invites management, or team features beyond what Google events already carry. **[call]**

## Anti-goals

- Slow startup, laggy popup, or clunky interaction. Popup should appear essentially instantly on tray click, from locally cached events, with sync happening in the background. **[call: cache]**
- The app being heavier than a web page tab.

## Constraints

- Windows 11 desktop.
- Claude API key stored locally and never committed (use `.env`, gitignored, plus `.env.example`).
- Google access through the owner's own Google Cloud project (OAuth). One-time setup the builder should walk the owner through.
- Use US spelling in UI and docs.
- Show the app version number; increment it on each update.

## How it gets made (writer's call, not agreed by owner)

- Python with PyQt6, matching the owner's other apps (e.g. PokemonInvestments). Builder may choose a different stack only if it makes the tray popup measurably faster.
- Google Calendar API via OAuth; local cache (SQLite) of events for instant display.
- Claude API with tool use for calendar actions (list, create, move, update, delete events; set reminders).

## Owner decisions after builder review

- **Work account access is unknown.** The first step of the plan must be a small throwaway connection test: sign in to both Google accounts with the owner's own Google Cloud project and read today's events. Nothing else is built until this works. If the work account blocks it, stop and report back to the owner.
- **Which calendar for new events:** if more than one account is active and the request doesn't say which, the assistant asks. With only one account active, it uses that one.

## Calls added after builder review (writer's calls, owner to check)

- **Ambiguity:** if a chat request could match more than one event or calendar, the assistant asks one short question instead of guessing. Clear requests apply immediately.
- **Undo:** keeps the previous state of the last 20 changes, saved on disk so it survives restarts. Undo covers moves, edits, creates, and deletes. If the event was changed elsewhere since, Undo warns instead of overwriting.
- **Reminders:** chat reminders ("in 20 minutes") are local timers saved on disk, not calendar events. They survive restarts.
- **Always running:** the app starts with Windows and stays in the tray. After sleep or restart, missed meeting toasts for events still in progress are shown once; older ones are dropped.
- **Claude model and context:** default `claude-sonnet-5-5`, configurable. Each chat turn sends today plus the next 7 days of events from all calendars. Chat history lasts for the session only.
- **Directions link:** a plain Google Maps URL built from the place text (`maps/dir/?api=1&destination=...`). No origin is set, so Maps uses the current location. No extra API key.
- **Toasts:** native Windows toasts with a registered app ID. Meeting link comes from the Google Meet link, or a Zoom/Teams URL found in the location or description.
- **Week/Month window:** read-only in v1, with click-for-details. Editing happens through the chat. (Owner: say if you want drag-and-drop editing.)
- **Speed target:** popup visible within 200 ms of the tray click. Build the window ahead of time and keep it hidden.

## Left to the builder

- Exact visuals (dark theme like the mockup is a sample, not a requirement).
- Sync frequency and how to handle sync conflicts.
- Window size, settings screen layout, and how the week/month window looks.
- Time zone handling and recurring events.
- How "best time tomorrow" is computed (suggestion: free gaps across all connected calendars, honoring working hours).
- Test strategy.

## Acceptance checks

- Clicking the tray icon shows today's merged agenda from two Google accounts with no visible delay.
- A meeting 10 minutes away triggers a Windows toast; changing the lead time changes when it fires.
- "Move my dentist appt to tomorrow" changes the event in Google, shows a Done line with Undo, and Undo restores it.
- "Remind me to call Sam in 20 minutes" produces a toast 20 minutes later.
- "Set an appointment at [salon] tomorrow at 3, include directions" creates the event with location and a Maps link.
- Both accounts' events are color-coded and distinguishable.
