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
    directory = store.reference(notice)
    assert store.metadata(notice)["notice_id"] == notice.id
    assert list(Path(settings.snapshot_root).glob("教务处/*.zip"))
    old = directory
    notice.title = "更新标题"
    assert store.reference(notice) == old


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
        previous = kwargs.get("previous", {})
        items = [item for item in items if item.url not in previous]
        count.append(len(items))
        target.mkdir(parents=True, exist_ok=True)
        if not items:
            return list(previous.values())
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


def test_vpn_failure_scans_directly(tmp_path, monkeypatch):
    _, _, settings = setup_scan(tmp_path, monkeypatch)
    settings.vpn_enabled = True
    settings.https_proxy = "http://127.0.0.1:8888"
    monkeypatch.setattr(runner, "run_check_vpn", lambda settings: False)
    original_session = runner.new_session
    create = Mock(side_effect=original_session)
    monkeypatch.setattr(runner, "new_session", create)
    assert runner.run_all(settings) == 1
    create.assert_called_once_with(settings.request_timeout, proxy_override={})
    assert settings.https_proxy == "http://127.0.0.1:8888"


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
    (source / "raw.html").write_text("old HTML")
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
    assert (source / "raw.html").exists()
    assert not (destination / "raw.html").exists()
    assert (target / "state/教务信息/sent_ids.txt").read_text() == "notice123456\n"
    migrate(target, old)
    assert len(list((target / "web").glob("**/meta.json"))) == 1


def test_inline_image_without_extension_is_a_download_candidate():
    from seu_monitor.core.attachments import _is_attachment_candidate
    assert _is_attachment_candidate(AttachmentCandidate(
        'https://jwc.seu.edu.cn/image?id=123', '正文图片', source='inline_image'))


def test_failed_column_and_notice_do_not_block_remaining_scan(tmp_path, monkeypatch):
    notice, detail, settings = setup_scan(tmp_path, monkeypatch)
    settings.vpn_enabled = True
    monkeypatch.setattr(runner, 'run_check_vpn', lambda settings: False)
    monkeypatch.setattr(runner, 'site_config', lambda: {
        'id':'jwc', 'columns':[
            {'id':'zxdt','name':'最新动态','list_url':'unavailable'},
            {'id':'jwxx','name':'教务信息','list_url':'available'}]})
    broken = Notice('jwc','jwxx','broken123456','访问失败','https://jwc.seu.edu.cn/broken','2026-10-08')
    def listing(self, url):
        if url == 'unavailable': raise ConnectionError('offline')
        return [notice, broken]
    def details(self, item):
        if item.id == broken.id: raise ConnectionError('offline')
        return detail
    monkeypatch.setattr(runner.WpNewsAdapter, 'fetch_list', listing)
    monkeypatch.setattr(runner.WpNewsAdapter, 'fetch_detail', details)
    assert runner.run_all(settings) == 1
    assert (tmp_path/'state/教务信息/sent_ids.txt').read_text().splitlines() == [notice.id]


def test_changed_article_id_same_content_is_not_sent_or_retained(tmp_path, monkeypatch):
    notice, detail, settings = setup_scan(tmp_path, monkeypatch, webhook='configured')
    send = Mock(return_value=True)
    monkeypatch.setattr(runner.FeishuNotifier, 'send', send)
    assert runner.run_all(settings) == 1
    notice.id = 'replacement123456'
    notice.url = 'https://jwc.seu.edu.cn/replacement/page.htm'
    detail.text = ' 正\n文 '
    assert runner.run_all(settings) == 0
    assert send.call_count == 1
    assert len(list(SnapshotStore(settings.snapshot_root).packed_records())) == 1
    assert 'replacement123456' in runner.StateStore(settings.store_root).load('教务信息')
    assert runner.run_all(settings) == 0


@pytest.mark.parametrize('change', ['body', 'date', 'title'])
def test_changed_content_is_still_sent(tmp_path, monkeypatch, change):
    notice, detail, settings = setup_scan(tmp_path, monkeypatch)
    assert runner.run_all(settings) == 1
    notice.id = 'replacement123456'
    notice.url += '?new'
    if change == 'body': detail.text += '新增条款'
    elif change == 'date': notice.date = '2026-10-09'
    else: notice.title += '更正'
    assert runner.run_all(settings) == 1


def test_saved_failed_push_does_not_suppress_replacement(tmp_path, monkeypatch):
    notice, _, settings = setup_scan(tmp_path, monkeypatch, webhook='configured')
    send = Mock(side_effect=[False, True])
    monkeypatch.setattr(runner.FeishuNotifier, 'send', send)
    assert runner.run_all(settings) == 0
    notice.id = 'replacement123456'
    notice.url += '?new'
    assert runner.run_all(settings) == 1
    assert send.call_count == 2
