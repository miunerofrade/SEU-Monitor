"""按教务处 / 栏目 / 通知归档正文、附件和元数据。"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote

from seu_monitor.core.models import Detail, Notice, SavedAttachment

logger = logging.getLogger(__name__)

# 不允许出现在目录名中的字符
_INVALID_FS_CHARS = re.compile(r'[/:*?"<>|]')


def _sanitize(name: str) -> str:
    """替换文件系统不允许的字符为 '_'。"""
    return _INVALID_FS_CHARS.sub("_", name)


def _write_text(path: Path, content: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


class SnapshotStore:
    """将公告快照保存到本地目录。"""

    def __init__(self, snapshot_root: str = "snapshots"):
        self.snapshot_root = snapshot_root

    # ---- 路径计算 ----

    def _snapshot_dir(self, notice: Notice) -> Path:
        from seu_monitor.sources.jwc import COLUMNS

        digest = hashlib.sha256(notice.url.encode()).hexdigest()[:16]
        column = (
            Path(self.snapshot_root)
            / "教务处"
            / _sanitize(COLUMNS.get(notice.column_id, notice.column_id))
        )
        existing = sorted(column.glob(f"*--{digest}")) if column.exists() else []
        return (
            existing[0]
            if existing
            else column / f"{_sanitize(notice.title)[:60]}--{digest}"
        )

    def attachment_records(self, notice: Notice) -> dict[str, SavedAttachment]:
        directory = self._snapshot_dir(notice)
        metadata = directory / "meta.json"
        if not metadata.exists():
            return {}
        records = {}
        for item in json.loads(metadata.read_text(encoding="utf-8")).get(
            "attachments", []
        ):
            path = directory / "attachments" / item["filename"]
            if (
                not item.get("error")
                and path.is_file()
                and hashlib.sha256(path.read_bytes()).hexdigest() == item.get("sha256")
            ):
                records[item["url"]] = SavedAttachment(**item)
        return records

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

        now_iso = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime(
            "%Y-%m-%dT%H:%M:%S+08:00"
        )

        # HTML 仅用于计算摘要，不落盘；清理此前留下的原网页。
        raw_content = detail.raw_html or detail.html
        for name in ("raw.html", "raw.html.tmp"):
            (snap_dir / name).unlink(missing_ok=True)
        html_sha256 = hashlib.sha256(raw_content.encode("utf-8")).hexdigest()

        # ---- text.md ----
        text_md = self._format_text_md(notice, detail, now_iso, saved_attachments)
        md_path = snap_dir / "text.md"
        _write_text(md_path, text_md)
        text_sha256 = hashlib.sha256(text_md.encode("utf-8")).hexdigest()

        # ---- meta.json ----
        attachments_info: list = []
        for att in saved_attachments or []:
            attachments_info.append(
                {
                    "url": att.url,
                    "filename": att.filename,
                    "sha256": att.sha256,
                    "size": att.size,
                    "content_type": att.content_type,
                    "error": att.error,
                }
            )

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
            "content_version": 1,
            "content_text": detail.text,
            "snapshot_path": str(snap_dir),
            "attachments": attachments_info,
        }
        meta_path = snap_dir / "meta.json"
        _write_text(meta_path, json.dumps(meta, ensure_ascii=False, indent=2))

        logger.info("快照已保存: %s", snap_dir)
        return str(snap_dir)

    @staticmethod
    def _format_text_md(notice: Notice, detail: Detail, fetched_at: str,
                        saved_attachments=None) -> str:
        """生成 text.md 的 Markdown 内容。"""
        body = detail.markdown or detail.text
        for attachment in saved_attachments or []:
            if not attachment.error and attachment.filename:
                body = body.replace(
                    f"(<{attachment.url}>)",
                    f"(<attachments/{quote(attachment.filename, safe='')}>)",
                )
        lines = [
            f"# {notice.title}",
            "",
            f"来源：{notice.url}",
            f"站点：{notice.site_id}",
            f"栏目：{notice.column_id}",
            f"发布时间：{notice.date}",
            f"抓取时间：{fetched_at}",
            "",
            body,
        ]
        return "\n".join(lines) + "\n"
