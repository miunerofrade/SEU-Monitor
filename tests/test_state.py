"""测试 StateStore 的读写、去重、兼容性。"""

import os
import tempfile

from seu_monitor.core.state import StateStore


class TestStateStore:
    def test_empty_state(self):
        """从未使用过的栏目应返回空集合。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(store_root=tmp)
            sent = store.load("最新动态")
            assert sent == set()

    def test_mark_and_load(self):
        """标记后应能读取到。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(store_root=tmp)
            store.mark_seen("最新动态", "abc123")
            sent = store.load("最新动态")
            assert "abc123" in sent
            assert len(sent) == 1

    def test_multiple_ids(self):
        """多条 ID 应正确持久化。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(store_root=tmp)
            store.mark_seen("最新动态", "id000001")
            store.mark_seen("最新动态", "id000002")
            store.mark_seen("最新动态", "id000003")
            sent = store.load("最新动态")
            assert sent == {"id000001", "id000002", "id000003"}

    def test_short_id_filtered(self):
        """长度 <= 5 的 ID 不应保存（与原始逻辑兼容）。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(store_root=tmp)
            store.mark_seen("最新动态", "ab")  # 太短
            sent = store.load("最新动态")
            assert "ab" not in sent
            assert len(sent) == 0

    def test_isolation_between_columns(self):
        """不同栏目的 ID 相互独立。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(store_root=tmp)
            store.mark_seen("最新动态", "id00000x")
            store.mark_seen("教务信息", "id00000y")
            assert "id00000x" in store.load("最新动态")
            assert "id00000y" not in store.load("最新动态")
            assert "id00000y" in store.load("教务信息")

    def test_compatible_with_existing_format(self):
        """兼容现有 store/{栏目}/sent_ids.txt 的纯文本行格式。"""
        with tempfile.TemporaryDirectory() as tmp:
            col_dir = os.path.join(tmp, "最新动态")
            os.makedirs(col_dir)
            # 模拟已有的 sent_ids.txt
            with open(os.path.join(col_dir, "sent_ids.txt"), "w") as f:
                f.write("c21676a517810\n")
                f.write("c21676a529996\n")
                f.write("ab\n")  # 短 ID，应被过滤

            store = StateStore(store_root=tmp)
            sent = store.load("最新动态")
            assert sent == {"c21676a517810", "c21676a529996"}
