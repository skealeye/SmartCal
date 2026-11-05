from urllib.parse import parse_qs, urlsplit

import pytest

from smartcal.maps import directions_url


def _destination(url):
    parts = urlsplit(url)
    assert (parts.scheme, parts.netloc, parts.path) == ("https", "www.google.com", "/maps/dir/")
    q = parse_qs(parts.query)
    assert q["api"] == ["1"] and set(q) == {"api", "destination"}
    return q["destination"][0]


def test_directions_url_is_url_encoded():
    url = directions_url("Polished Nail Salon, 12 Main St")
    assert url == ("https://www.google.com/maps/dir/?api=1&destination="
                   "Polished%20Nail%20Salon%2C%2012%20Main%20St")
    assert _destination(url) == "Polished Nail Salon, 12 Main St"


def test_directions_url_escapes_ampersand_hash_and_spaces():
    url = directions_url("Tom & Jerry's #2 Bar")
    assert " " not in url and "#" not in url and url.count("&") == 1
    assert _destination(url) == "Tom & Jerry's #2 Bar"


def test_directions_url_unicode():
    url = directions_url("Café Zürich, Straße 5")
    assert url.isascii()
    assert _destination(url) == "Café Zürich, Straße 5"


def test_directions_url_strips_and_rejects_empty():
    assert _destination(directions_url("  Main St\n")) == "Main St"
    for bad in ("", "   ", "\n\t"):
        with pytest.raises(ValueError):
            directions_url(bad)
