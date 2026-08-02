from __future__ import annotations

from agent.tools.builtin.web_fetch.html import MAX_LINKS, extract_static_html


def test_static_html_returns_main_body_and_links_from_same_document() -> None:
    html = """
    <!doctype html>
    <html><body>
      <nav>navigation boilerplate</nav>
      <main><h1>开战斗机的虾米</h1><p>这是一段足够明确的创作者介绍正文。</p></main>
      <aside class="infobox">
        <a href="https://www.bilibili.com/video/BV1oDdKYtEyr">代表作一</a>
        <a href="/relative/path" aria-label="站内补充资料"></a>
      </aside>
    </body></html>
    """

    result = extract_static_html(
        html,
        requested_url="https://zh.moegirl.org.cn/example",
        final_url="https://zh.moegirl.org.cn/example",
    )

    assert "开战斗机的虾米" in result.text
    assert [(link.url, link.label) for link in result.links] == [
        ("https://www.bilibili.com/video/BV1oDdKYtEyr", "代表作一"),
        ("https://zh.moegirl.org.cn/relative/path", "站内补充资料"),
    ]
    assert result.link_total == 2
    assert result.links_truncated is False


def test_static_html_deduplicates_and_bounds_links() -> None:
    links = "".join(
        f'<a href="https://example.com/item/{index}">item {index}</a>'
        for index in range(MAX_LINKS + 5)
    )
    result = extract_static_html(
        f"<html><body><main><p>正文内容</p></main>{links}</body></html>",
        requested_url="https://example.com/source",
        final_url="https://example.com/source",
    )

    assert len(result.links) == MAX_LINKS
    assert result.link_total == MAX_LINKS + 5
    assert result.links_truncated is True
