"""快照保存模块 — SnapshotStore。

将公告详情页保存为结构化目录：
  snapshots/<site_id>/<column_id>/<YYYY>/<MM>/<YYYYMMDD_HHMMSS>_<notice_id>/
    meta.json
    raw.html
    text.md
    attachments/
      ...
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Optional

from seu_monitor.core.models import Detail, Notice, SavedAttachment

logger = logging.getLogger(__name__)

# 不允许出现在目录名中的字符
_INVALID_FS_CHARS = re.compile(r'[/:*?"<>|]')


def _sanitize(name: str) -> str:
    """替换文件系统不允许的字符为 '_'。"""
    return _INVALID_FS_CHARS.sub("_", name)


def _resolve_root(config_root: Optional[str], env_var: str, default: str) -> str:
    """解析路径：环境变量 > 配置值 > 默认值。"""
    env = os.environ.get(env_var)
    if env:
        return env
    if config_root:
        return config_root
    return default


class SnapshotStore:
    """将公告快照保存到本地目录。"""

    def __init__(self, snapshot_root: str = "snapshots"):
        self.snapshot_root = snapshot_root

    # ---- 路径计算 ----

    def _parse_date(self, date_str: str) -> datetime:
        """尝试解析日期字符串，失败则返回当前时间。"""
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日"):
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue
        # 回退：当前北京时间
        return datetime.now(timezone.utc) + timedelta(hours=8)

    def _dir_name(self, notice: Notice) -> str:
        """生成公告目录名：YYYYMMDD_<标题前30字>"""
        dt = self._parse_date(notice.date)
        ts = dt.strftime("%Y%m%d")
        safe_title = _sanitize(notice.title.strip())[:30].rstrip("_")
        return f"{ts}_{safe_title}" if safe_title else f"{ts}_{_sanitize(notice.id)}"

    def _snapshot_dir(self, notice: Notice) -> Path:
        """返回公告快照的完整目录路径。"""
        dt = self._parse_date(notice.date)
        return (
            Path(self.snapshot_root)
            / _sanitize(notice.site_id)
            / _sanitize(notice.column_id)
            / f"{dt.year:04d}"
            / f"{dt.month:02d}"
            / self._dir_name(notice)
        )

    # ---- 保存快照 ----

    def save(
        self,
        notice: Notice,
        detail: Detail,
        saved_attachments: Optional[List[SavedAttachment]] = None,
    ) -> str:
        """保存公告详情快照到本地目录。

        Returns:
            快照目录的路径字符串。
        """
        snap_dir = self._snapshot_dir(notice)
        snap_dir.mkdir(parents=True, exist_ok=True)

        now_iso = (
            (datetime.now(timezone.utc) + timedelta(hours=8))
            .strftime("%Y-%m-%dT%H:%M:%S+08:00")
        )

        # ---- raw.html（优先保存完整原始 HTML） ----
        raw_path = snap_dir / "raw.html"
        raw_content = detail.raw_html or detail.html
        raw_path.write_text(raw_content, encoding="utf-8")
        html_sha256 = hashlib.sha256(raw_content.encode("utf-8")).hexdigest()

        # ---- text.md ----
        text_md = self._format_text_md(notice, detail, now_iso)
        md_path = snap_dir / "text.md"
        md_path.write_text(text_md, encoding="utf-8")
        text_sha256 = hashlib.sha256(text_md.encode("utf-8")).hexdigest()

        # ---- meta.json ----
        attachments_info: list = []
        for att in (saved_attachments or []):
            attachments_info.append({
                "url": att.url,
                "filename": att.filename,
                "sha256": att.sha256,
                "size": att.size,
                "content_type": att.content_type,
                "error": att.error,
            })

        meta = {
            "site_id": notice.site_id,
            "column_id": notice.column_id,
            "notice_id": notice.id,
            "title": notice.title,
            "url": notice.url,
            "date": notice.date,
            "fetched_at": now_iso,
            "html_sha256": html_sha256,
            "text_sha256": text_sha256,
            "snapshot_path": str(snap_dir),
            "attachments": attachments_info,
        }
        meta_path = snap_dir / "meta.json"
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        logger.info("快照已保存: %s", snap_dir)
        return str(snap_dir)

    @staticmethod
    def _format_text_md(notice: Notice, detail: Detail, fetched_at: str) -> str:
        """生成 text.md 的 Markdown 内容。"""
        lines = [
            f"# {notice.title}",
            "",
            f"来源：{notice.url}",
            f"站点：{notice.site_id}",
            f"栏目：{notice.column_id}",
            f"发布时间：{notice.date}",
            f"抓取时间：{fetched_at}",
            "",
            detail.text,
        ]
        return "\n".join(lines) + "\n"
