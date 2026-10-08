import hashlib
import json
from pathlib import Path
from unittest.mock import Mock
import pytest
from seu_monitor.core import runner
from seu_monitor.core.models import Notice, Detail, AttachmentCandidate, SavedAttachment
from seu_monitor.core.settings import Settings
from seu_monitor.core.snapshot import SnapshotStore


def setup_scan(tmp_path, monkeypatch, attachments=None, webhook=""):
    notice = Notice(
        "jwc",
        "jwxx",
        "notice123456",
        "测试通知",
        "https://jwc.seu.edu.cn/notice/page.htm",
        "2026-10-08",
    )
    detail = Detail("<p>正文</p>", "正文", attachments=attachments or [])
    monkeypatch.setattr(
        runner,
        "site_config",
        lambda: {
            "id": "jwc",
            "columns": [{"id": "jwxx", "name": "教务信息", "list_url": "list"}],
        },
    )
    monkeypatch.setattr(runner.WpNewsAdapter, "fetch_list", lambda *a: [notice])
    monkeypatch.setattr(runner.WpNewsAdapter, "fetch_detail", lambda *a: detail)
    settings = Settings(
        store_root=str(tmp_path / "state"),
        snapshot_root=str(tmp_path / "web"),
        feishu_webhook=webhook,
    )
    return notice, detail, settings


def test_without_webhook_archives_and_deduplicates(tmp_path, monkeypatch):
    notice, _, settings = setup_scan(tmp_path, monkeypatch)
    assert runner.run_all(settings) == 1
    assert runner.run_all(settings) == 0
    store = SnapshotStore(settings.snapshot_root)
    directory = store._snapshot_dir(notice)
    assert (directory / "text.md").is_file()
    old = directory
    notice.title = "更新标题"
    assert store._snapshot_dir(notice) == old


def test_snapshot_failure_does_not_deliver_or_mark_seen(tmp_path, monkeypatch):
    _, _, settings = setup_scan(tmp_path, monkeypatch, webhook="configured")
    monkeypatch.setattr(SnapshotStore, "save", Mock(side_effect=OSError("disk full")))
    send = Mock()
    monkeypatch.setattr(runner.FeishuNotifier, "send", send)
    assert runner.run_all(settings) == 0
    send.assert_not_called()
    assert not list((tmp_path / "state").glob("**/sent_ids.txt"))


def test_failed_delivery_reuses_verified_attachment(tmp_path, monkeypatch):
    attachment = AttachmentCandidate("https://jwc.seu.edu.cn/a.pdf", "a.pdf")
    notice, _, settings = setup_scan(
        tmp_path, monkeypatch, [attachment], webhook="configured"
    )
    count = []

    def download(items, target, **kwargs):
        count.append(len(items))
        target.mkdir(parents=True, exist_ok=True)
        if not items:
            return []
        (target / "a.pdf").write_bytes(b"pdf")
        return [
            SavedAttachment(
                attachment.url,
                "a.pdf",
                hashlib.sha256(b"pdf").hexdigest(),
                3,
                "application/pdf",
            )
        ]

    monkeypatch.setattr(runner, "download_attachments", download)
    monkeypatch.setattr(runner.FeishuNotifier, "send", Mock(side_effect=[False, True]))
    assert runner.run_all(settings) == 0
    assert runner.run_all(settings) == 1
    assert count == [1, 0]


def test_failed_attachment_prevents_mark_seen(tmp_path, monkeypatch):
    attachment = AttachmentCandidate("https://jwc.seu.edu.cn/a.pdf", "a.pdf")
    _, _, settings = setup_scan(tmp_path, monkeypatch, [attachment])
    monkeypatch.setattr(
        runner,
        "download_attachments",
        lambda *a, **kw: [SavedAttachment(attachment.url, "", error="timeout")],
    )
    assert runner.run_all(settings) == 0
    assert not list((tmp_path / "state").glob("**/sent_ids.txt"))


def test_vpn_failure_does_not_scan(tmp_path, monkeypatch):
    _, _, settings = setup_scan(tmp_path, monkeypatch)
    settings.vpn_enabled = True
    monkeypatch.setattr(runner, "run_check_vpn", lambda settings: False)
    scan = Mock()
    monkeypatch.setattr(runner.WpNewsAdapter, "fetch_list", scan)
    with pytest.raises(RuntimeError):
        runner.run_all(settings)
    scan.assert_not_called()


def test_migration_keeps_old_and_imports_attachments_and_state(tmp_path):
    from seu_monitor.migration import migrate

    old = tmp_path / "old"
    source = old / "snapshots/jwc/jwxx/2026/10/notice"
    source.mkdir(parents=True)
    metadata = dict(
        site_id="jwc",
        column_id="jwxx",
        notice_id="notice123456",
        title="旧通知",
        url="https://jwc.seu.edu.cn/a",
        date="2026-10-08",
    )
    (source / "meta.json").write_text(json.dumps(metadata))
    (source / "attachments").mkdir()
    (source / "attachments/a.pdf").write_bytes(b"pdf")
    state = old / "store/教务信息/sent_ids.txt"
    state.parent.mkdir(parents=True)
    state.write_text("notice123456\n")
    target = tmp_path / "new"
    target.mkdir()
    migrate(target, old)
    destination = list((target / "web").glob("**/meta.json"))[0].parent
    assert (destination / "attachments/a.pdf").read_bytes() == b"pdf"
    assert source.exists()
    assert (target / "state/教务信息/sent_ids.txt").read_text() == "notice123456\n"
    migrate(target, old)
    assert len(list((target / "web").glob("**/meta.json"))) == 1
