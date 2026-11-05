# SmartCal

Windows tray calendar app. This README currently covers only the **Google connection spike** (Task 0), which checks that both of your Google accounts (personal and work) can sign in and read today's events.

## One-time Google Cloud setup

1. Go to <https://console.cloud.google.com/> and sign in with your personal account.
2. Create a project: top bar project picker > **New Project** > name it `SmartCal` > **Create**. Make sure it is selected.
3. Enable the API: **APIs & Services > Library**, search for **Google Calendar API**, click **Enable**.
4. Configure the consent screen: **APIs & Services > OAuth consent screen** (or **Google Auth Platform**).
   - User type: **External**. Fill in app name (`SmartCal`) and your email for support/contact.
   - Leave publishing status as **Testing**.
   - Under **Test users**, click **Add users** and add **both** account emails (personal and work).
5. Create the OAuth client: **APIs & Services > Credentials > Create credentials > OAuth client ID**, application type **Desktop app**, name `SmartCal`, **Create**.
6. Click **Download JSON** on the new client. Save it as:

   `%APPDATA%\SmartCal\client_secret.json`

   (create the `SmartCal` folder if needed; paste `%APPDATA%` into File Explorer's address bar to find it). Never commit or share this file.

## Run the spike

```
pip install -r requirements.txt
python scripts/spike_google.py personal
python scripts/spike_google.py work
```

Each run opens your browser for consent. Choose the matching account. Google will warn "this app isn't verified": click **Advanced > Go to SmartCal (unsafe)**. That is expected for your own app in Testing mode.

The script prints the signed-in email and today's events (start time, title, calendar name) across all of that account's calendars. Tokens are stored under `%APPDATA%\SmartCal\spike_tokens\<label>.json`.

If the work account is blocked by your Workspace admin ("access blocked" / "app not allowed"), stop and report it.

## Testing mode caveat

While the consent screen is in **Testing**, refresh tokens expire after **7 days**, so you will have to sign in again weekly. Publishing the app to **In production** removes that limit (it will still show the unverified warning). Decide later whether to publish.

## Claude API key (assistant chat)

Create `%APPDATA%\SmartCal\.env` (see `.env.example`) containing:

```
ANTHROPIC_API_KEY=your-key-here
```

An `ANTHROPIC_API_KEY` environment variable takes precedence. SmartCal does not read a `.env` from the current folder. Never commit or share the key.

## Tests

```
pip install pytest
pytest
```
