"""Update check: ask GitHub for the latest release; SmartCal never installs anything itself."""
from __future__ import annotations

import json
import logging
import re
import urllib.request

from . import __version__

log = logging.getLogger(__name__)

REPO = "skealeye/SmartCal"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_URL = f"https://github.com/{REPO}/releases/latest"
CHECK_EVERY_MS = 24 * 60 * 60 * 1000
TIMEOUT = 10


def parse_version(text) -> tuple[int, ...] | None:
    """'v0.12.3' / '0.12.3' -> (0, 12, 3); None if it is not a plain dotted number."""
    m = re.fullmatch(r"v?(\d+(?:\.\d+){0,3})", str(text or "").strip())
    return tuple(int(p) for p in m.group(1).split(".")) if m else None


def is_newer(latest, current=__version__) -> bool:
    a, b = parse_version(latest), parse_version(current)
    return a is not None and b is not None and a > b


def fetch_latest(url: str = LATEST_URL) -> str | None:
    """The newest release's version text (e.g. '0.13.0'), or None (offline, no release,
    rate limit, odd reply). Never raises."""
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                                   "User-Agent": "SmartCal-update-check"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 - fixed https URL
            tag = json.loads(resp.read(200_000)).get("tag_name")
    except Exception as exc:  # noqa: BLE001 - an update check must never hurt the app
        log.info("update check failed: %s", exc)
        return None
    return str(tag).lstrip("vV") if parse_version(tag) else None


def check(fetch=fetch_latest, current=__version__) -> str | None:
    """The newer version's text if one exists, else None."""
    latest = fetch()
    return latest if latest and is_newer(latest, current) else None
