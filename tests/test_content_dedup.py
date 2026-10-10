from pathlib import Path
import json
import hashlib
from seu_monitor.core.content_dedup import ContentIndex
from seu_monitor.core.models import Notice, Detail, SavedAttachment
from seu_monitor.core.snapshot import SnapshotStore
from seu_monitor.core.state import StateStore


def record(images=(), pdf='pdf', version=1):
    return dict(site_id='jwc', title='测试', date='2026-10-10', content_version=version,
                attachments=[dict(filename='a.pdf', sha256=pdf, content_type='application/pdf')] +
                [dict(filename='a.jpg', sha256=i, content_type='image/jpeg') for i in images])


def test_attachment_changes_and_image_changes_are_not_suppressed():
    index = ContentIndex()
    index.add(record(['image']), Path('old'), '正文')
    assert index.find(record(['image']), '正\n文') == Path('old')
    assert index.find(record(['changed']), '正文') is None
    assert index.find(record(['image'], pdf='changed'), '正文') is None


def test_legacy_archive_loading_and_image_compatibility(tmp_path):
    notice = Notice('jwc', 'jwxx', 'old123456', '测试', 'https://example/old', '2026-10-10')
    store = SnapshotStore(str(tmp_path / 'web'))
    directory = Path(store.save(notice, Detail('正文', '正文'),
                               [SavedAttachment('https://example/a.pdf', 'a.pdf', hashlib.sha256(b'pdf').hexdigest(), 3, 'application/pdf')]))
    (directory / 'attachments').mkdir()
    (directory / 'attachments/a.pdf').write_bytes(b'pdf')
    meta_path = directory / 'meta.json'
    metadata = json.loads(meta_path.read_text())
    del metadata['content_text']
    del metadata['content_version']
    meta_path.write_text(json.dumps(metadata))
    state = StateStore(str(tmp_path / 'state'))
    assert not ContentIndex.load(store.snapshot_root, state).entries
    state.mark_seen('教务信息', notice.id)
    index = ContentIndex.load(store.snapshot_root, state)
    assert index.find(record(['newly-supported-image'], pdf=hashlib.sha256(b'pdf').hexdigest()), '正文') == directory
    assert index.find(record(['image'], pdf='changed'), '正文') is None


def test_image_only_notice_requires_identical_images():
    index = ContentIndex()
    old = record(['image'])
    old['attachments'] = old['attachments'][1:]
    index.add(old, Path('old'), '')
    changed = dict(old, attachments=[dict(filename='a.jpg', sha256='other', content_type='image/jpeg')])
    assert index.find(changed, '') is None
    assert index.find(old, '') == Path('old')
