"""WpNewsAdapter — 适配学校常见的 wp_news 站群系统。

当前优先兼容教务处 jwc.seu.edu.cn 的栏目列表结构与详情页面。
"""

from __future__ import annotations

import logging
import re
from typing import List, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from seu_monitor.adapters.base import SiteAdapter
from seu_monitor.core.http import do_get, new_session
from seu_monitor.core.models import AttachmentCandidate, Detail, Notice

logger = logging.getLogger(__name__)


def _extract_id(relative_link: str) -> str:
    """从相对 URL 中提取稳定 ID。

    与原始 edulog.py 逻辑完全一致：
    parts = relative_link.split('/')
    n_id = parts[-2] if len(parts) > 2 else relative_link
    """
    parts = relative_link.split("/")
    return parts[-2] if len(parts) > 2 else relative_link


class WpNewsAdapter(SiteAdapter):
    """适配 wp_news 站群的通用适配器。"""

    def __init__(self, site_config: dict, session=None):
        """
        Args:
            site_config: 来自 sites.yaml 的站点配置 dict。
            session: 可选的 requests.Session，不传则自动创建。
        """
        self.config = site_config
        self.session = session or new_session()

    # ---- SiteAdapter 接口 ----

    def fetch_list(self, list_url: str) -> List[Notice]:
        """抓取列表页并解析为 Notice 列表。"""
        resp = do_get(self.session, list_url)
        return self.parse_list_html(resp.text, list_url, self.config["id"])

    def fetch_detail(self, notice: Notice) -> Detail:
        """抓取详情页并提取正文。"""
        resp = do_get(self.session, notice.url)
        detail = self.parse_detail_html(resp.text, notice.url)
        detail.raw_html = resp.text  # 保存完整原始 HTML
        return detail

    # ---- 列表页解析 ----

    @staticmethod
    def parse_list_html(
        html: str,
        base_url: str,
        site_id: str = "jwc",
    ) -> List[Notice]:
        """解析 wp_news 列表页 HTML，返回 Notice 列表。

        可被测试代码直接调用，不依赖真实网络。
        """
        soup = BeautifulSoup(html, "html.parser")

        # 查找 wp_news 容器 (id 以 wp_news_w 开头)
        container = soup.find(id=lambda x: x and x.startswith("wp_news_w"))
        if not container:
            # 降级：查找 class 含 news 的容器
            container = soup.find(class_=lambda x: x and "news" in x.lower())
        if not container:
            return []

        notices: List[Notice] = []
        rows = container.find_all("tr")

        for row in rows:
            main_td = row.find("td", class_="main")
            if not main_td:
                continue

            link_tag = main_td.find("a")
            if not link_tag:
                continue

            title = link_tag.get("title") or link_tag.get_text().strip()
            if not title:
                continue

            relative_link = link_tag.get("href", "")
            full_link = urljoin(base_url, relative_link)

            date_tds = row.find_all("td")
            date_text = date_tds[-1].get_text().strip() if len(date_tds) > 1 else "未知"

            # 与原 edulog.py 完全一致的 ID 提取
            n_id = _extract_id(relative_link)

            # 与原逻辑一致：只保留长度 > 5 的 ID
            if n_id and len(n_id) > 5:
                notices.append(
                    Notice(
                        site_id=site_id,
                        column_id="",
                        id=n_id,
                        title=title,
                        url=full_link,
                        date=date_text,
                    )
                )

        return notices

    # ---- 详情页解析 ----

    @staticmethod
    def parse_detail_html(html: str, base_url: str = "") -> Detail:
        """解析 wp_news 详情页 HTML，返回 Detail。

        提取策略（按优先级）：
        1. 清理 <script>/<style>/导航元素
        2. 按 CSS class/id 查找正文容器
        3. 查找 Article_Title 并提取其所在父容器的文本
        4. 降级至 <body> 并去除导航噪音
        """
        soup = BeautifulSoup(html, "html.parser")

        # ---- 清理 ----
        for tag in soup(["script", "style", "meta", "link", "nav", "footer",
                         "header", "aside", "noscript"]):
            tag.decompose()

        # 移除已知导航/侧栏元素（jwc 站群）
        for nav_class in ["wp_nav", "fontmain2", "nav-item", "copyright",
                          "footer", "sidebar", "side"]:
            for el in soup.find_all(class_=lambda c: c and nav_class in (c or "").lower()):
                el.decompose()

        body = soup.find("body")

        # ---- 策略 1：按 CSS class/id 查找正文容器 ----
        content = None
        for selector in ["content", "article", "main", "text", "bodytext",
                         "vsbcontent", "con_content", "article-content",
                         "ArticleContent", "news-content", "NewsContent"]:
            content = (
                soup.find(class_=lambda c: c and selector in (c or "").lower())
                or soup.find(id=lambda i: i and selector in (i or "").lower())
            )
            if content:
                break

        # ---- 策略 2：按 Article_Title 定位正文 ----
        if not content:
            title_span = soup.find("span", class_="Article_Title")
            if title_span:
                # 向上找到包含正文的 <td> 或 <div>
                parent_td = title_span.find_parent("td")
                if parent_td:
                    content = parent_td
                else:
                    content = title_span.parent

        # ---- 策略 3：降级至 <body> ----
        if not content and body:
            content = body
        if not content:
            content = soup

        html_content = str(content)

        # ---- 提取纯文本 ----
        text = content.get_text(separator="\n", strip=True)
        text = re.sub(r"\n{3,}", "\n\n", text)

        # ---- 附件候选提取 ----
        attachments: List[AttachmentCandidate] = []

        # 从 <a> 标签提取
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            link_text = a_tag.get_text().strip()
            full_url = urljoin(base_url, href) if base_url else href
            url_lower = full_url.lower()

            is_ext_match = any(
                url_lower.endswith(ext)
                for ext in [".pdf", ".doc", ".docx", ".xls", ".xlsx",
                            ".ppt", ".pptx", ".zip", ".rar", ".7z", ".txt"]
            )
            is_keyword_match = any(kw in link_text for kw in ["附件", "下载", "PDF", "DOC", "XLS", "PPT", "ZIP"])

            if is_ext_match or is_keyword_match:
                attachments.append(AttachmentCandidate(
                    url=full_url,
                    text=link_text or href.split("/")[-1],
                    source="detail_link",
                ))

        # 从 wp_pdf_player 元素提取 PDF 附件
        # jwc 站群有两种写法：
        #   <iframe class="wp_pdf_player" src="viewer.html?file=...">
        #   <span  class="wp_pdf_player" pdfsrc="...">
        from urllib.parse import parse_qs, urlparse

        # 写法 1：<span class="wp_pdf_player" pdfsrc="...">
        for el in soup.find_all(class_="wp_pdf_player"):
            pdfsrc = el.get("pdfsrc") or el.get("file") or ""
            if pdfsrc:
                pdf_url = urljoin(base_url, pdfsrc)
                fname = pdfsrc.split("/")[-1] or "附件.pdf"
                attachments.append(AttachmentCandidate(
                    url=pdf_url, text=fname, source="pdf_player",
                ))
                continue

            # 写法 2：src 里带 ?file= 参数
            src = el.get("src", "")
            if src:
                qs = parse_qs(urlparse(src).query)
                pdf_path = qs.get("file", [None])[0]
                if pdf_path:
                    pdf_url = urljoin(base_url, pdf_path)
                    fname = pdf_path.split("/")[-1] or "附件.pdf"
                    attachments.append(AttachmentCandidate(
                        url=pdf_url, text=fname, source="pdf_player",
                    ))

        return Detail(html=html_content, text=text, attachments=attachments)
