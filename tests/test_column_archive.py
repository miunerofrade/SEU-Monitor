import hashlib
import json
from pathlib import Path
from unittest.mock import Mock
from zipfile import ZipFile
import pytest
from seu_monitor.core.column_archive import ColumnArchive
from seu_monitor.core.snapshot import SnapshotStore
from seu_monitor.core.models import Detail, Notice, SavedAttachment, AttachmentCandidate
from seu_monitor.core.attachments import download_attachments


def notice():
    return Notice('jwc', 'jwxx', 'notice123456', '通知', 'https://example.com/a', '2026-10-10')


def legacy(store, item=None):
    item = item or notice()
    directory = store._snapshot_dir(item)
    (directory / 'attachments').mkdir(parents=True, exist_ok=True)
    data = b'reusable PDF contents' * 100
    (directory / 'attachments/a.pdf').write_bytes(data)
    attachment = SavedAttachment('https://example.com/a.pdf', 'a.pdf', hashlib.sha256(data).hexdigest(),
                                 len(data), 'application/pdf')
    store.save(item, Detail('', '正文'), [attachment])
    return directory, attachment, data


def test_migration_preserves_every_byte_and_reuses_without_network(tmp_path):
    store = SnapshotStore(str(tmp_path / 'web'))
    directory, attachment, data = legacy(store)
    originals = {p.relative_to(directory).as_posix(): p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    assert store.pack_legacy() == 1
    assert not directory.exists()
    archive = directory.parent.with_suffix('.zip')
    with ZipFile(archive) as z:
        for name, contents in originals.items():
            assert z.read(directory.name + '/' + name) == contents
    candidate = AttachmentCandidate(attachment.url, '附件')
    session = Mock()
    with store.staging(notice(), [candidate]) as (workspace, previous):
        assert (workspace / 'attachments/a.pdf').read_bytes() == data
        assert download_attachments([candidate], workspace / 'attachments', session, previous) == [attachment]
    session.get.assert_not_called()
    assert not workspace.exists()
    assert store.pack_legacy() == 0


def test_atomic_update_failure_keeps_original_and_cleans_temporary(tmp_path, monkeypatch):
    store = SnapshotStore(str(tmp_path / 'web'))
    directory, _, _ = legacy(store)
    store.pack_legacy()
    archive = ColumnArchive(directory.parent)
    before = archive.path.read_bytes()
    candidate = AttachmentCandidate('https://example.com/a.pdf', '附件')
    with store.staging(notice(), [candidate]) as (workspace, previous):
        store.save(notice(), Detail('', '新正文'), list(previous.values()))
        monkeypatch.setattr(ZipFile, 'testzip', lambda self: 'damaged')
        with pytest.raises(ValueError): store.commit(notice())
    assert archive.path.read_bytes() == before
    assert not list(archive.path.parent.glob('*.tmp'))


def test_update_does_not_duplicate_members_and_rebuilds_index(tmp_path):
    store = SnapshotStore(str(tmp_path / 'web'))
    directory, att, _ = legacy(store)
    store.pack_legacy()
    other = Notice('jwc', 'jwxx', 'second123456', '另一通知', 'https://example.com/b', '2026-10-10')
    for item, body in [(other, '另一个正文'), (notice(), '更新正文'), (notice(), '再次更新')]:
        candidates = [AttachmentCandidate(att.url, '附件')] if item.id == notice().id else []
        with store.staging(item, candidates) as (workspace, previous):
            store.save(item, Detail('', body), list(previous.values()))
            store.commit(item)
    archive = ColumnArchive(directory.parent)
    records = archive.records()
    assert len(records) == 2
    archive.index.write_text('corrupted')
    assert archive.records() == records
    assert store.metadata(notice())['content_text'] == '再次更新'
    with ZipFile(archive.path) as z:
        assert len(z.namelist()) == len(set(z.namelist()))
        assert z.testzip() is None


def test_corrupt_attachment_is_not_reused(tmp_path):
    store = SnapshotStore(str(tmp_path / 'web'))
    directory, att, _ = legacy(store)
    (directory / 'attachments/a.pdf').write_bytes(b'corrupted')
    store.pack_legacy()
    assert not next(store.packed_records())[0]['verified']
    with store.staging(notice(), [AttachmentCandidate(att.url, '附件')]) as (workspace, previous):
        assert previous == {}
        assert not (workspace / 'attachments/a.pdf').exists()


def test_migration_failure_never_removes_originals(tmp_path, monkeypatch):
    store = SnapshotStore(str(tmp_path / 'web'))
    directory, _, _ = legacy(store)
    monkeypatch.setattr(ZipFile, 'testzip', lambda self: 'broken')
    with pytest.raises(ValueError): store.pack_legacy()
    assert (directory / 'text.md').is_file()
    assert (directory / 'attachments/a.pdf').is_file()
    assert not directory.parent.with_suffix('.zip').exists()


def test_partial_attachment_failure_preserves_successful_files(tmp_path):
    store = SnapshotStore(str(tmp_path / 'web'))
    item = notice()
    first = AttachmentCandidate('https://example.com/a.pdf', '附件一')
    second = AttachmentCandidate('https://example.com/b.pdf', '附件二')
    with store.staging(item, [first, second]) as (workspace, previous):
        (workspace / 'attachments').mkdir()
        data = b'first PDF'
        (workspace / 'attachments/a.pdf').write_bytes(data)
        good = SavedAttachment(first.url, 'a.pdf', hashlib.sha256(data).hexdigest(),len(data),'application/pdf')
        bad = SavedAttachment(second.url, '', error='timeout')
        store.save(item, Detail('', '正文'), [good, bad])
        store.commit(item)
    with store.staging(item, [first, second]) as (workspace, previous):
        assert set(previous) == {first.url}
        assert (workspace / 'attachments/a.pdf').read_bytes() == data
