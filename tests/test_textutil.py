import time

from smartcal.textutil import html_to_text, meeting_label

OWNER = ("<b>One session only. Pick one and decline the rest.</b><br><b>Do not forward this invite "
         "or share the Zoom link. Need someone added? Have their producer request it with L7 "
         "approval.</b>")


def test_owner_example_is_two_lines():
    assert html_to_text(OWNER).split("\n") == [
        "One session only. Pick one and decline the rest.",
        "Do not forward this invite or share the Zoom link. Need someone added? Have their "
        "producer request it with L7 approval."]


def test_script_and_style_dropped_with_contents():
    assert html_to_text("<script>alert(1)</script>x") == "x"
    assert html_to_text("<style>p{color:red}</style>y<script>unclosed(") == "y"


def test_plain_text_not_corrupted():
    assert html_to_text("a < b & c") == "a < b & c"
    assert html_to_text("R&D and 5 > 3") == "R&D and 5 > 3"
    assert html_to_text("R&amp;D") == "R&D"
    assert html_to_text("line1\nline2") == "line1\nline2"


def test_entities_decoded_in_html():
    assert html_to_text("<p>a&nbsp;b &amp; &#39;c&#x27; &lt;i&gt;</p>") == "a b & 'c' <i>"


def test_links():
    assert html_to_text('<a href="https://x.com/a?b=1&amp;c=2">Click</a>') \
        == "Click (https://x.com/a?b=1&c=2)"
    assert html_to_text('<a href="https://x.com">https://x.com</a>') == "https://x.com"
    assert html_to_text('<a href="javascript:alert(1)">bad</a>') == "bad"


def test_lists_and_blocks():
    assert html_to_text("<ul><li>one</li><li>two</li></ul>") == "• one\n• two"
    assert html_to_text("<p>a</p><p>b</p><div>c</div>") == "a\nb\nc"
    assert html_to_text("<h1>T</h1>body") == "T\nbody"


def test_nested_and_unclosed_tags():
    assert html_to_text("<div><b><i>hi</i> there</b></div>") == "hi there"
    assert html_to_text("<b>bold<br>next") == "bold\nnext"
    assert html_to_text("text <a href='http://x.com'>link") == "text link (http://x.com)"
    assert html_to_text("tail <b").strip() == "tail"


def test_blank_line_collapse_and_strip():
    assert html_to_text("<br><br>a<br><br><br><br>b<br><br>") == "a\n\nb"
    assert html_to_text("a\r\nb") == "a\nb"


def test_large_input_fast_and_capped():
    for big in ("<b>x</b><br>" * 20_000, "<a " * 70_000, "x" * 200_000, "<p>" + "&amp;" * 60_000):
        t0 = time.perf_counter()
        out = html_to_text(big)
        assert time.perf_counter() - t0 < 2
        assert len(out) <= 20_001


def test_no_markup_output_from_html():
    out = html_to_text('<img src=x onerror=alert(1)><b>ok</b>')
    assert out == "ok"


def test_meeting_label_by_host():
    assert meeting_label("https://us02web.zoom.us/j/1?pwd=a") == "Join Zoom meeting"
    assert meeting_label("https://zoom.us/j/1") == "Join Zoom meeting"
    assert meeting_label("https://meet.google.com/abc-defg") == "Join Google Meet"
    assert meeting_label("https://teams.microsoft.com/l/x") == "Join Teams meeting"
    assert meeting_label("https://teams.live.com/meet/1") == "Join Teams meeting"
    for bad in ("https://evilzoom.us/j/1", "https://zoom.us.evil.com/j/1",
                "https://meet.google.com.evil.com/a", "https://evil.com/?u=zoom.us",
                "https://evil.com/zoom.us", "", None, "not a url"):
        assert meeting_label(bad) == "Join meeting"
