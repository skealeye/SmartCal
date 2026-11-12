# SmartCal privacy policy

SmartCal is a free, open-source Windows tray calendar. It runs on your PC. There is no SmartCal server, and the author receives none of your data.

## What SmartCal keeps on your PC
In `%APPDATA%\SmartCal`: your Google sign-in tokens, a local copy of your calendar events, your reminders, your settings, and (if you save one) your Claude API key. Nothing here is uploaded to the author. Delete the folder, or remove the account in Settings, to clear it.

## Google
When you sign in, SmartCal asks Google for access to your calendars so it can show your meetings (and change them, where you ask it to). That data goes only between Google and your PC. You can revoke access any time at https://myaccount.google.com/permissions.
SmartCal's use of Google data follows the Google API Services User Data Policy, including the Limited Use requirements.

## Ask Claude (optional)
Only if you add a Claude API key and use "Ask Claude": your question, and the calendar details needed to answer it, are sent to Anthropic using your own key, under Anthropic's terms and privacy policy. Nothing is sent if you do not use it.

## Update check
Once a day SmartCal asks GitHub for the latest release number. This sends no calendar data. Turn it off in the tray menu ("Check for updates daily").

## Talking to Claude Code / Claude Desktop (optional)
`SmartCal-mcp.exe` runs only when you connect it to Claude. It reads your reminders and agenda from your PC and passes them to the Claude app you connected.

## Contact
Questions: https://github.com/skealeye/SmartCal/issues
