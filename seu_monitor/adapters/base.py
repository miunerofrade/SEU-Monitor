"""网站适配器抽象基类。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from seu_monitor.core.models import Notice, Detail


class SiteAdapter(ABC):
    """统一接口：抓取列表页 & 详情页。"""

    @abstractmethod
    def fetch_list(self, list_url: str) -> List[Notice]:
        """从公告列表页解析出 Notice 列表。

        Args:
            list_url: 栏目列表页完整 URL。

        Returns:
            List[Notice] 按时间倒序（最新在前）。
        """
        ...

    @abstractmethod
    def fetch_detail(self, notice: Notice) -> Detail:
        """抓取一条公告的详情页并抽取正文。

        Args:
            notice: 需要抓取详情的公告。

        Returns:
            包含 HTML、纯文本、附件列表的 Detail 对象。
        """
        ...
