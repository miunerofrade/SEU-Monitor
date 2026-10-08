"""One-time non-destructive import of this checkout's old snapshots/state."""

from __future__ import annotations
import json
import shutil
from pathlib import Path
from .core.models import Notice
from .core.snapshot import SnapshotStore


def migrate(directory: Path, legacy: Path):
    marker = directory / "migration.json"
    if marker.exists():
        return
    snapshots = SnapshotStore(str(directory / "web"))
    imported = 0
    for metadata in (legacy / "snapshots").glob("**/meta.json"):
        item = json.loads(metadata.read_text(encoding="utf-8"))
        if item.get("site_id") != "jwc":
            continue
        notice = Notice(
            site_id="jwc",
            column_id=item["column_id"],
            id=item["notice_id"],
            title=item["title"],
            url=item["url"],
            date=item.get("date", ""),
        )
        destination = snapshots._snapshot_dir(notice)
        if not destination.exists():
            shutil.copytree(metadata.parent, destination)
            item["snapshot_path"] = str(destination)
            (destination / "meta.json").write_text(
                json.dumps(item, ensure_ascii=False, indent=2)
            )
            imported += 1
    for source in (legacy / "store").glob("*/sent_ids.txt"):
        destination = directory / "state" / source.parent.name / source.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        old = (
            set(destination.read_text().splitlines()) if destination.exists() else set()
        )
        values = old | set(source.read_text().splitlines())
        destination.write_text("\n".join(sorted(values)) + "\n")
    marker.write_text(json.dumps({"source": str(legacy), "snapshots": imported}))
