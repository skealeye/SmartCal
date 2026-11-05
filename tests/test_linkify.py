import re
import time
from html.parser import HTMLParser

from smartcal.textutil import linkify_to_html


class _Parts(HTMLParser):
    """Collects every tag and attribute the HTML actually contains."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags, self.hrefs, self.text = [], [], []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, attrs))
        if tag == "a":
            self.hrefs.append(dict(attrs)["href"])

    def handle_data(self, data):
        self.text.append(data)


def parts(html):
    p = _Parts()
    p.feed(html)
    p.close()
    return p


def links(plain):
    return parts(linkify_to_html(plain)).hrefs


def test_http_and_https_become_links():
    assert links("see https://zoom.us/j/1 and http://example.com/x") == [
        "https://zoom.us/j/1", "http://example.com/x"]


def test_text_is_kept_verbatim():
    plain = "Agenda:\n1. a < b & c > d\n  indented"
    p = parts(linkify_to_html(plain))
    assert "".join(p.text) == plain
    assert [t for t, _ in p.tags] == ["div"]


def test_trailing_punctuation_is_not_part_of_the_link():
    for tail in (".", ",", ";", ":", "!", "?", "...", "?!"):
        assert links(f"Go to https://example.com/a{tail}") == ["https://example.com/a"]
    p = parts(linkify_to_html("Go to https://example.com/a."))
    assert "".join(p.text) == "Go to https://example.com/a."


def test_parens_balanced_kept_unbalanced_dropped():
    assert links("(see https://example.com/a)") == ["https://example.com/a"]
    assert links("https://en.wikipedia.org/wiki/Foo_(bar)") == [
        "https://en.wikipedia.org/wiki/Foo_(bar)"]
    assert links("(https://en.wikipedia.org/wiki/Foo_(bar)).") == [
        "https://en.wikipedia.org/wiki/Foo_(bar)"]


def test_quotes_and_angle_brackets_cannot_inject_markup():
    evil = ('https://x.com/a"onmouseover="alert(1) '
            "https://x.com/b'><script>alert(1)</script> "
            "https://x.com/c<img src=x onerror=alert(1)>")
    html = linkify_to_html(evil)
    p = parts(html)
    assert {t for t, _ in p.tags} <= {"div", "a"}
    for tag, attrs in p.tags:
        assert [k for k, _ in attrs] == (["href"] if tag == "a" else ["style"])
    assert p.hrefs == ["https://x.com/a", "https://x.com/b", "https://x.com/c"]
    assert "".join(p.text) == evil          # shown as text, never run
    assert "<script" not in html and "<img" not in html


def test_other_schemes_and_bare_www_are_not_linked():
    for s in ("javascript:alert(1)", "file:///C:/Windows/notepad.exe", "mailto:a@b.com",
              "www.example.com", "ftp://example.com", "JAVASCRIPT:alert('https://x.com')x"):
        hrefs = links(s)
        assert all(h.lower().startswith(("http://", "https://")) for h in hrefs), s
    assert links("javascript:alert(1)") == []
    assert links("www.example.com") == []
    assert links("xhttps://example.com") == []


def test_urls_failing_safe_http_url_are_text():
    assert links("https://user:pw@evil.com/ https:// http://") == []


def test_uppercase_scheme_is_linked():
    assert links("HTTPS://EXAMPLE.COM/A") == ["HTTPS://EXAMPLE.COM/A"]


def test_ampersand_in_url_is_escaped_in_html_and_decoded_in_href():
    html = linkify_to_html("https://zoom.us/j/1?pwd=a&x=<1>")
    assert "&amp;" in html
    assert parts(html).hrefs == ["https://zoom.us/j/1?pwd=a&x="]


def test_multiple_links_on_many_lines():
    plain = "\n".join(f"line {i} https://e{i}.com/p" for i in range(50))
    assert links(plain) == [f"https://e{i}.com/p" for i in range(50)]


def test_very_long_text_is_fast_and_complete():
    plain = ("word " * 40_000) + "https://example.com/" + "a" * 10_000 + " end"
    t0 = time.perf_counter()
    html = linkify_to_html(plain)
    assert time.perf_counter() - t0 < 1.0
    assert links(plain) == ["https://example.com/" + "a" * 10_000]
    assert html.endswith(" end</div>")


def test_empty():
    assert re.sub(r"<[^>]*>", "", linkify_to_html("")) == ""
