"""测试 SnapshotStore：快照目录结构、文件内容、meta.json 字段。"""

import json
import tempfile
from pathlib import Path

from seu_monitor.core.models import Detail, Notice, SavedAttachment
from seu_monitor.core.snapshot import SnapshotStore


def _make_notice(**kwargs) -> Notice:
    defaults = dict(
        site_id="jwc",
        column_id="jwxx",
        id="test_id_123456",
        title="测试公告",
        url="https://jwc.seu.edu.cn/jwxx/2024/test.shtml",
        date="2024-03-15",
    )
    defaults.update(kwargs)
    return Notice(**defaults)


def _make_detail(text: str = "测试正文内容", html: str = None) -> Detail:
    if html is None:
        html = f"<html><body><p>{text}</p></body></html>"
    return Detail(html=html, text=text)


class TestSnapshotStore:
    def test_save_creates_directory_structure(self):
        """保存快照后应生成正确的目录结构。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = SnapshotStore(snapshot_root=tmp)
            notice = _make_notice(
                site_id="jwc",
                column_id="zxdt",
                id="abc12345678",
                date="2024-03-15",
            )
            detail = _make_detail("各位同学：这是一条测试公告。")

            snap_path = store.save(notice, detail)
            path = Path(snap_path)

            # 目录: snapshots/jwc/zxdt/2024/03/20240315_000000_abc12345678/
            assert path.exists()
            assert path.is_dir()
            assert path.parent.name == "最新动态"
            assert path.parent.parent.name == "教务处"
            assert path.name.startswith("测试公告--")

    def test_save_creates_required_files(self):
        """只保存正文和元数据，不保留原始网页。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = SnapshotStore(snapshot_root=tmp)
            notice = _make_notice(
                id="abc12345678",
                date="2024-03-15",
                title="测试公告标题",
                url="https://example.com/test",
            )
            detail = _make_detail("正文内容")

            snap_path = store.save(notice, detail)
            path = Path(snap_path)

            assert not (path / "raw.html").exists()
            assert (path / "text.md").exists()
            assert (path / "meta.json").exists()

            (path / "raw.html").write_text("legacy HTML")
            store.save(notice, detail)
            assert not (path / "raw.html").exists()

            # text.md 应包含标题和正文
            md = (path / "text.md").read_text(encoding="utf-8")
            assert "# 测试公告标题" in md
            assert "正文内容" in md

    def test_meta_json_fields(self):
        """meta.json 应包含所有必需字段。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = SnapshotStore(snapshot_root=tmp)
            notice = _make_notice(
                site_id="jwc",
                column_id="jwxx",
                id="abc12345678",
                title="测试公告标题",
                url="https://example.com/test",
                date="2024-03-15",
            )
            detail = _make_detail("正文内容")

            saved_attachments = [
                SavedAttachment(
                    url="https://example.com/file.pdf",
                    filename="file.pdf",
                    sha256="a" * 64,
                    size=1234,
                    content_type="application/pdf",
                    error=None,
                ),
                SavedAttachment(
                    url="https://example.com/broken.zip",
                    filename="broken.zip",
                    sha256=None,
                    size=None,
                    content_type=None,
                    error="Connection timeout",
                ),
            ]

            snap_path = store.save(notice, detail, saved_attachments)
            meta = json.loads(
                (Path(snap_path) / "meta.json").read_text(encoding="utf-8")
            )

            assert meta["site_id"] == "jwc"
            assert meta["column_id"] == "jwxx"
            assert meta["notice_id"] == "abc12345678"
            assert meta["title"] == "测试公告标题"
            assert meta["url"] == "https://example.com/test"
            assert meta["date"] == "2024-03-15"
            assert "fetched_at" in meta
            assert "html_sha256" in meta
            assert "text_sha256" in meta
            assert "snapshot_path" in meta

            assert len(meta["attachments"]) == 2
            assert meta["attachments"][0]["url"] == "https://example.com/file.pdf"
            assert meta["attachments"][0]["filename"] == "file.pdf"
            assert meta["attachments"][0]["sha256"] == "a" * 64
            assert meta["attachments"][0]["size"] == 1234
            assert meta["attachments"][0]["error"] is None

            assert meta["attachments"][1]["error"] == "Connection timeout"

    def test_save_without_attachments(self):
        """无附件时 attachments 应为空列表。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = SnapshotStore(snapshot_root=tmp)
            notice = _make_notice(id="abc12345678")
            detail = _make_detail()

            snap_path = store.save(notice, detail)
            meta = json.loads(
                (Path(snap_path) / "meta.json").read_text(encoding="utf-8")
            )
            assert meta["attachments"] == []

    def test_text_md_no_emoji(self):
        """text.md 不应包含 emoji，保持简洁。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = SnapshotStore(snapshot_root=tmp)
            notice = _make_notice(id="abc12345678", title="测试")
            detail = _make_detail()

            snap_path = store.save(notice, detail)
            md = (Path(snap_path) / "text.md").read_text(encoding="utf-8")
            # 没有 emoji
            assert "\U0001f514" not in md
            assert "\U0001f517" not in md


def test_image_markdown_uses_local_escaped_path_and_keeps_failed_link(tmp_path):
    store = SnapshotStore(str(tmp_path))
    detail = Detail("", "文字", markdown='前文\n![图](<https://example.com/img>)\n后文\n![失败](<https://example.com/fail>)')
    saved = [SavedAttachment("https://example.com/img", "安排 (1).png"),
             SavedAttachment("https://example.com/fail", "", error="timeout")]
    directory = Path(store.save(_make_notice(), detail, saved))
    markdown = (directory / "text.md").read_text()
    assert '![图](<attachments/%E5%AE%89%E6%8E%92%20%281%29.png>)' in markdown
    assert '![失败](<https://example.com/fail>)' in markdown
    assert markdown.index("前文") < markdown.index("![图]") < markdown.index("后文")
