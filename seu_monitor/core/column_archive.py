"""每栏目一个 ZIP：原子替换、可重建索引与旧目录迁移。"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile


def digest_stream(stream):
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(65536), b''):
        digest.update(chunk)
    return digest.hexdigest()


@contextmanager
def archive_lock(root: str):
    parent = Path(root).parent
    parent.mkdir(parents=True, exist_ok=True)
    with (parent / '.archive.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


class ColumnArchive:
    def __init__(self, column: Path):
        self.column = column
        self.path = column.with_suffix('.zip')
        self.index = column.with_suffix('.index.json')

    def records(self):
        if not self.path.exists():
            return {}
        stat = self.path.stat()
        stamp = [stat.st_ino, stat.st_size, stat.st_mtime_ns]
        if self.index.exists():
            try:
                data = json.loads(self.index.read_text())
                if data['stamp'] == stamp:
                    return data['records']
            except (ValueError, KeyError, TypeError):
                pass
        records = {}
        with ZipFile(self.path) as archive:
            for name in archive.namelist():
                if name.count('/') != 1 or not name.endswith('/meta.json'):
                    continue
                prefix = name.rsplit('/', 1)[0]
                metadata = json.loads(archive.read(name))
                text = archive.read(prefix + '/text.md').decode('utf-8')
                valid = True
                for item in metadata.get('attachments', []):
                    member = prefix + '/attachments/' + item.get('filename', '')
                    if item.get('error') or not item.get('sha256'):
                        valid = False
                        continue
                    try:
                        with archive.open(member) as stream:
                            valid &= digest_stream(stream) == item['sha256']
                    except KeyError:
                        valid = False
                records[prefix] = {'metadata': metadata, 'text': text, 'verified': valid}
        self.index.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.index.with_suffix('.index.json.tmp')
        try:
            temporary.write_text(json.dumps({'stamp': stamp, 'records': records}, ensure_ascii=False))
            temporary.replace(self.index)
        finally:
            temporary.unlink(missing_ok=True)
        return records

    def restore(self, prefix: str, target: Path, urls: set[str]):
        record = self.records().get(prefix)
        previous = {}
        if not record:
            return previous
        from .models import SavedAttachment
        with ZipFile(self.path) as archive:
            for item in record['metadata'].get('attachments', []):
                if item['url'] not in urls or item.get('error') or not item.get('sha256'):
                    continue
                name = item.get('filename', '')
                if not name or Path(name).name != name:
                    raise ValueError('不安全的归档附件名称')
                destination = target / 'attachments' / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with archive.open(prefix + '/attachments/' + name) as source, destination.open('wb') as out:
                        shutil.copyfileobj(source, out)
                    with destination.open('rb') as stream:
                        valid = digest_stream(stream) == item['sha256']
                    if valid:
                        previous[item['url']] = SavedAttachment(**item)
                    else:
                        destination.unlink()
                except KeyError:
                    destination.unlink(missing_ok=True)
        return previous

    def update(self, folders: dict[str, Path]):
        """写新 ZIP 并核对 CRC 和输入文件哈希，旧 ZIP 到最后才被替换。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, filename = tempfile.mkstemp(prefix=self.path.name + '.', suffix='.tmp', dir=self.path.parent)
        import os
        os.close(fd)
        temporary = Path(filename)
        expected = {}
        try:
            with ZipFile(temporary, 'w', compression=ZIP_DEFLATED, compresslevel=6) as out:
                if self.path.exists():
                    with ZipFile(self.path) as old:
                        names = old.namelist()
                        if len(names) != len(set(names)):
                            raise ValueError('ZIP 中有重复成员，停止更新')
                        for info in old.infolist():
                            if info.filename.split('/')[0] in folders:
                                continue
                            with old.open(info) as source, out.open(info, 'w') as destination:
                                shutil.copyfileobj(source, destination)
                for prefix, directory in folders.items():
                    for path in sorted(directory.rglob('*')):
                        if not path.is_file() or path.name in {'raw.html', 'raw.html.tmp'}:
                            continue
                        member = prefix + '/' + path.relative_to(directory).as_posix()
                        with path.open('rb') as stream:
                            expected[member] = digest_stream(stream)
                        out.write(path, member)
            with ZipFile(temporary) as check:
                bad = check.testzip()
                if bad:
                    raise ValueError(f'ZIP 校验失败：{bad}')
                for name, digest in expected.items():
                    with check.open(name) as stream:
                        if digest_stream(stream) != digest:
                            raise ValueError(f'归档内容校验失败：{name}')
            temporary.replace(self.path)
            # ZIP 是权威数据，索引失效或中途退出时，下次自动从 ZIP 重建。
            self.index.unlink(missing_ok=True)
        finally:
            temporary.unlink(missing_ok=True)

    def migrate(self):
        if not self.column.is_dir():
            return 0
        folders = {p.name: p for p in self.column.iterdir() if p.is_dir() and (p / 'meta.json').is_file()}
        if not folders:
            return 0
        # 重启恢复时，已存在的归档必须与待迁移目录逐文件相同。
        if self.path.exists():
            with ZipFile(self.path) as old:
                for prefix, directory in folders.items():
                    for path in directory.rglob('*'):
                        if not path.is_file() or path.name in {'raw.html', 'raw.html.tmp'}:
                            continue
                        member = prefix + '/' + path.relative_to(directory).as_posix()
                        if member in old.namelist():
                            with old.open(member) as source, path.open('rb') as original:
                                if digest_stream(source) != digest_stream(original):
                                    raise ValueError(f'旧目录与 ZIP 冲突：{member}')
        self.update(folders)
        self.records()  # 确认元数据与正文可读取，才删除原目录。
        for directory in folders.values():
            shutil.rmtree(directory)
        if not any(self.column.iterdir()):
            self.column.rmdir()
        return len(folders)
