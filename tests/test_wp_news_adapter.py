"""测试 WpNewsAdapter 的列表页和详情页解析。"""

import json
import os
from pathlib import Path

from seu_monitor.adapters.wp_news import WpNewsAdapter, _extract_id

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> str:
    path = FIXTURES_DIR / name
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# ID 提取（与原始 edulog.py 一致）
# ---------------------------------------------------------------------------

class TestExtractId:
    def test_trailing_slash_id(self):
        """../../c21676a517810/  →  'c21676a517810'"""
        assert _extract_id("../../c21676a517810/") == "c21676a517810"

    def test_single_component(self):
        """只有 1 段 → 返回原值"""
        assert _extract_id("c21676a517810.shtml") == "c21676a517810.shtml"

    def test_two_components(self):
        """2 段 → 返回原值"""
        assert _extract_id("2024/c21676a517810.shtml") == "2024/c21676a517810.shtml"


# ---------------------------------------------------------------------------
# 列表页解析
# ---------------------------------------------------------------------------

class TestParseList:
    def test_parse_list_returns_notices(self):
        html = _load_fixture("wp_news_list.html")
        base_url = "https://jwc.seu.edu.cn/zxdt/list.htm"
        notices = WpNewsAdapter.parse_list_html(html, base_url, site_id="jwc")

        assert len(notices) == 3

        # 第一条
        n0 = notices[0]
        assert n0.site_id == "jwc"
        assert n0.id == "c21676a517810"
        assert "选课" in n0.title
        assert n0.date == "2024-03-01"
        assert "c21676a517810" in n0.url

        # 第二条
        n1 = notices[1]
        assert n1.id == "c21676a529996"

        # 第三条
        n2 = notices[2]
        assert n2.id == "c21676a534566"

    def test_parse_empty_html(self):
        """空 HTML 不应崩溃。"""
        notices = WpNewsAdapter.parse_list_html(
            "<html></html>",
            "https://jwc.seu.edu.cn/zxdt/list.htm",
        )
        assert notices == []

    def test_parse_no_wp_news_container(self):
        """没有 wp_news_w 容器的页面应返回空列表。"""
        html = "<html><body><div>no news here</div></body></html>"
        notices = WpNewsAdapter.parse_list_html(html, "https://example.com")
        assert notices == []


# ---------------------------------------------------------------------------
# 详情页解析
# ---------------------------------------------------------------------------

class TestParseDetail:
    def test_parse_detail_text(self):
        html = _load_fixture("wp_news_detail.html")
        detail = WpNewsAdapter.parse_detail_html(html, "https://jwc.seu.edu.cn")

        assert "选课" in detail.text
        assert "2024-2025" in detail.text
        assert "秋季学期" in detail.text
        assert "各位同学" in detail.text

        # script/style 应被移除
        assert "<script>" not in detail.html

    def test_parse_detail_attachments(self):
        html = _load_fixture("wp_news_detail.html")
        detail = WpNewsAdapter.parse_detail_html(html, "https://jwc.seu.edu.cn")

        assert len(detail.attachments) == 2
        names = [a.text for a in detail.attachments]
        urls = [a.url for a in detail.attachments]

        assert "选课指南.pdf" in names
        assert "课程清单.docx" in names
        assert any("file1.pdf" in u for u in urls)
        assert any("file2.docx" in u for u in urls)

    def test_parse_empty_detail(self):
        """空详情页不应崩溃。"""
        detail = WpNewsAdapter.parse_detail_html(
            "<html><body></body></html>",
            "https://example.com",
        )
        assert detail.text == ""
        assert detail.attachments == []

    def test_parse_detail_with_scripts(self):
        """带 script/style 的页面应正确清理。"""
        html = """
        <html>
        <head><script>alert('x')</script><style>.cls{}</style></head>
        <body><div class="content"><p>正文内容</p></div></body>
        </html>
        """
        detail = WpNewsAdapter.parse_detail_html(html, "https://example.com")
        assert "正文内容" in detail.text
        assert "alert" not in detail.html
