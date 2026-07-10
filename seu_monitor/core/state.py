"""已发送公告 ID 状态管理。

兼容原有 store/{栏目名}/sent_ids.txt 的格式：
- 每行一个 ID
- 只保存长度 > 5 的 ID（与原有逻辑一致）
"""

from __future__ import annotations

import os
from typing import Set


class StateStore:
    """管理已发送公告的去重状态。"""

    def __init__(self, store_root: str = "store"):
        self.store_root = store_root

    def _column_dir(self, column_name: str) -> str:
        """返回栏目对应的存储目录。"""
        return os.path.join(self.store_root, column_name)

    def load(self, column_name: str) -> Set[str]:
        """读取某栏目已发送的 ID 集合。"""
        sent_ids: Set[str] = set()
        col_dir = self._column_dir(column_name)
        log_file = os.path.join(col_dir, "sent_ids.txt")
        if os.path.exists(log_file):
            with open(log_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if len(line) > 5:
                        sent_ids.add(line)
        return sent_ids

    def mark_seen(self, column_name: str, notice_id: str) -> None:
        """将一条公告 ID 标记为已发送。"""
        if len(notice_id) <= 5:
            return
        col_dir = self._column_dir(column_name)
        os.makedirs(col_dir, exist_ok=True)
        log_file = os.path.join(col_dir, "sent_ids.txt")
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(notice_id + "\n")
