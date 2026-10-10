"""跨文章 URL 的内容去重；只索引已成功处理的归档。"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from pathlib import Path

from .state import StateStore
from .attachment_rules import is_image_file
from seu_monitor.sources.jwc import COLUMNS

logger = logging.getLogger(__name__)


def normalized(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))


def body_text(metadata: dict, directory: Path, archived_text: str | None = None) -> str:
    if "content_text" in metadata:
        return metadata["content_text"]
    text = archived_text if archived_text is not None else (directory / "text.md").read_text(encoding="utf-8")
    # 旧版快照的正文从抓取时间后的空行开始，不把 URL、抓取时间算入内容。
    parts = re.split(r"抓取时间：[^\n]*\n\n", text, maxsplit=1)
    if len(parts) != 2:
        raise ValueError("旧快照缺少正文边界")
    return re.sub(r"!\[[^\]]*\]\([^\n]*?\)", "", parts[1])


def signature(metadata: dict, text: str):
    files, images = set(), set()
    for item in metadata.get("attachments", []):
        if item.get("error") or not item.get("sha256"):
            raise ValueError("附件不完整，不能作为去重依据")
        is_image = is_image_file(item.get("filename", ""), item.get("content_type") or "")
        (images if is_image else files).add(item["sha256"])
    body = normalized(text)
    if not body and not files and not images:
        raise ValueError("空通知不能作为去重依据")
    key = hashlib.sha256(json.dumps([
        metadata.get("site_id"), normalized(metadata.get("title", "")),
        metadata.get("date"), body, sorted(files),
    ], ensure_ascii=False).encode()).hexdigest()
    return key, frozenset(images)


def verify_files(metadata: dict, directory: Path):
    for item in metadata.get("attachments", []):
        path = directory / "attachments" / item.get("filename", "")
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != item.get("sha256"):
            raise ValueError("归档附件缺失或哈希不符")


class ContentIndex:
    def __init__(self):
        self.entries: dict[str, list[tuple[dict, Path, frozenset]]] = {}

    @classmethod
    def load(cls, root: str, state: StateStore):
        from .snapshot import SnapshotStore
        index = cls()
        seen = {column: state.load(name) for column, name in COLUMNS.items()}
        records = []
        for path in Path(root).glob("教务处/*/*/meta.json"):
            try:
                metadata = json.loads(path.read_text(encoding="utf-8"))
                if metadata.get("notice_id") not in seen.get(metadata.get("column_id"), set()):
                    continue
                records.append((metadata, path.parent))
            except (OSError, ValueError):
                logger.warning("忽略损坏的归档元数据：%s", path)
        for metadata, directory in sorted(records, key=lambda item: item[0].get("fetched_at", "")):
            try:
                verify_files(metadata, directory)
                index.add(metadata, directory, body_text(metadata, directory))
            except (OSError, ValueError):
                logger.warning("归档无法用于内容去重：%s", directory)
        for record, reference in SnapshotStore(root).packed_records():
            metadata = record['metadata']
            if not record['verified'] or metadata.get('notice_id') not in seen.get(metadata.get('column_id'), set()):
                continue
            try:
                index.add(metadata, reference, body_text(metadata, reference, record['text']))
            except ValueError:
                logger.warning("压缩归档无法用于内容去重：%s", reference)
        return index

    def find(self, metadata: dict, text: str):
        key, images = signature(metadata, text)
        for old, directory, old_images in self.entries.get(key, []):
            # 旧版不抓正文图片：只有存在相同的非图片附件时才兼容缺失的图片。
            legacy = not old.get("content_version") and not old_images and any(
                a.get("sha256") for a in old.get("attachments", [])
            )
            if images == old_images or legacy:
                return directory
        return None

    def add(self, metadata: dict, directory: Path, text: str):
        key, images = signature(metadata, text)
        self.entries.setdefault(key, []).append((metadata, directory, images))
