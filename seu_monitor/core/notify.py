"""飞书机器人推送。

支持：
- 公告通知（含摘要）
- 纯文本告警（VPN 掉线、运行异常）
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class FeishuNotifier:
    """飞书 Webhook 消息推送。"""

    def __init__(self, webhook_url: Optional[str] = None):
        """
        Args:
            webhook_url: 飞书 Webhook 地址。不传则读 FEISHU_WEBHOOK 环境变量。
        """
        self._webhook_url = webhook_url

    @property
    def webhook_url(self) -> str:
        if self._webhook_url:
            return self._webhook_url
        return os.environ.get("FEISHU_WEBHOOK", "")

    def send(
        self,
        column_name: str,
        title: str,
        date_text: str,
        notice_url: str,
        text_summary: Optional[str] = None,
    ) -> bool:
        """发送一条公告通知（含正文摘要）。"""
        if not self.webhook_url:
            logger.warning("FEISHU_WEBHOOK 未配置，跳过推送")
            return False

        content_lines = []
        content_lines.append([
            {"tag": "text", "text": f"发布时间: {date_text}"},
        ])
        if text_summary:
            snippet = text_summary[:100].strip()
            if len(text_summary) > 100:
                snippet += "..."
            content_lines.append([
                {"tag": "text", "text": snippet},
            ])
        content_lines.append([
            {"tag": "a", "text": "\U0001f517 点击查看原文", "href": notice_url},
        ])

        payload = {
            "msg_type": "post",
            "content": {
                "post": {
                    "zh_cn": {
                        "title": f"\U0001f514 [{column_name}] {title}",
                        "content": content_lines,
                    }
                }
            },
        }
        return self._post(payload)

    def send_alert(self, message: str, title: str = "⚠ SEU-Monitor 告警") -> bool:
        """发送纯文本告警消息。

        Args:
            message: 告警内容。
            title: 消息标题。

        Returns:
            True 表示发送成功，False 表示失败或未配置 webhook。
        """
        if not self.webhook_url:
            logger.warning("FEISHU_WEBHOOK 未配置，跳过告警推送")
            return False

        payload = {
            "msg_type": "post",
            "content": {
                "post": {
                    "zh_cn": {
                        "title": title,
                        "content": [
                            [{"tag": "text", "text": message}],
                        ],
                    }
                }
            },
        }
        return self._post(payload)

    def _post(self, payload: dict) -> bool:
        """底层 POST 请求。"""
        try:
            resp = requests.post(self.webhook_url, json=payload, timeout=10)
            ok = resp.json().get("code") == 0
            if not ok:
                logger.error("飞书推送返回异常: %s", resp.text)
            return ok
        except Exception as exc:
            logger.error("飞书推送请求失败: %s", exc)
            return False
