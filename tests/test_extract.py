from pagemint.extract import extract_main
from pagemint.links import extract_links, normalize_url
from pagemint.markdown import to_markdown


def test_extract_density_fallback():
    sidebar = "".join(f'<li><a href="/l{i}">Link number {i}</a></li>' for i in range(30))
    body = " ".join(["This is a long sentence of real article content."] * 30)
    html = f"<html><body><div class='wrap'><div><ul>{sidebar}</ul></div><div><p>{body}</p><p>{body}</p></div></div></body></html>"
    ex = extract_main(html, "http://h/")
    assert "real article content" in ex.main_html
    assert "Link number 5" not in ex.main_html


def test_extract_prefers_article():
    html = "<html><body><div>Teaser text</div><article><h2>Story</h2><p>" + "words " * 100 + "</p></article></body></html>"
    ex = extract_main(html, "http://h/")
    assert "Story" in ex.main_html and "Teaser" not in ex.main_html


def test_extract_title_prefers_og():
    html = '<html><head><meta property="og:title" content="OG Title"><title>Plain</title></head><body><p>x</p></body></html>'
    assert extract_main(html, "http://h/").title == "OG Title"


def test_extract_title_falls_back_to_h1():
    assert extract_main("<html><body><h1>Heading One</h1><p>x</p></body></html>", "http://h/").title == "Heading One"


def test_boilerplate_word_match_is_whole_token():
    # "content-sidebar-layout" wraps everything; it must not be removed.
    html = '<html><body><div class="content-sidebar-layout"><p>' + "Important text. " * 40 + "</p></div></body></html>"
    assert "Important text" in extract_main(html, "http://h/").main_html


def test_removes_hidden_and_scripts():
    html = '<html><body><main><p>Visible</p><p hidden>Secret1</p><p aria-hidden="true">Secret2</p><p style="display: none">Secret3</p><script>var s="Secret4"</script></main></body></html>'
    out = extract_main(html, "http://h/").main_html
    assert "Visible" in out
    for s in ("Secret1", "Secret2", "Secret3", "Secret4"):
        assert s not in out


def test_empty_or_broken_html_does_not_crash():
    assert extract_main("", "http://h/").text_len == 0
    assert extract_main("<<<>>>", "http://h/").main_html is not None


def test_markdown_absolute_links_and_images():
    md = to_markdown('<p><a href="/a">A</a> <img src="i.png" alt="pic"></p>', "http://h/dir/page")
    assert "[A](http://h/a)" in md
    assert "![pic](http://h/dir/i.png)" in md


def test_markdown_drops_empty_links_and_collapses_blank_lines():
    md = to_markdown('<p>one</p><a href="/x"></a><br><br><br><br><p>two</p>', "http://h/")
    assert "[](" not in md
    assert "\n\n\n" not in md


def test_links_base_and_fragments():
    html = '<html><head><base href="/docs/"></head><body><a href="a.html#x">a</a><a href="a.html">dup</a><a href="mailto:x@y">m</a><a href="javascript:void(0)">j</a></body></html>'
    assert extract_links(html, "http://h/start") == ["http://h/docs/a.html"]


def test_normalize_url():
    assert normalize_url("HTTP://Example.COM:80/a/../b?q=1#frag") == "http://example.com/b?q=1"
    assert normalize_url("https://example.com:443") == "https://example.com/"
    assert normalize_url("ftp://example.com/") is None
    assert normalize_url("../x", "http://h/a/b/") == "http://h/a/x"
