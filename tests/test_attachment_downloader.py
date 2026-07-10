"""测试附件下载模块：文件保存、SHA-256、Content-Type、错误处理。"""

import hashlib
import tempfile
from pathlib import Path
from unittest.mock import Mock

from seu_monitor.core.attachments import (
    _is_attachment_candidate,
    _resolve_filename,
    download_attachment,
    download_attachments,
    _sanitize_filename,
)
from seu_monitor.core.models import AttachmentCandidate


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

class TestSanitizeFilename:
    def test_removes_invalid_chars(self):
        assert "/" not in _sanitize_filename("a/b/c.pdf")
        assert ":" not in _sanitize_filename("a:b.txt")
        assert "*" not in _sanitize_filename("a*b.txt")

    def test_empty_fallback(self):
        assert _sanitize_filename("") == "unnamed"


class TestIsAttachmentCandidate:
    def test_pdf_url(self):
        c = AttachmentCandidate(url="https://example.com/file.pdf", text="文件")
        assert _is_attachment_candidate(c) is True

    def test_docx_url(self):
        c = AttachmentCandidate(url="https://example.com/file.docx", text="文档")
        assert _is_attachment_candidate(c) is True

    def test_keyword_text(self):
        c = AttachmentCandidate(
            url="https://example.com/download.php?id=1",
            text="点击下载附件",
        )
        assert _is_attachment_candidate(c) is True

    def test_normal_link(self):
        c = AttachmentCandidate(
            url="https://example.com/page.htm",
            text="查看更多信息",
        )
        assert _is_attachment_candidate(c) is False


class TestResolveFilename:
    def test_url_path(self):
        c = AttachmentCandidate(
            url="https://example.com/files/report.pdf",
            text="报告",
        )
        response = Mock(headers={})
        fname = _resolve_filename(response, c, 1)
        assert fname == "report.pdf"

    def test_fallback_uses_link_text(self):
        """当 URL 无扩展名时使用链接文本作为文件名。"""
        c = AttachmentCandidate(
            url="https://example.com/download",
            text="下载文件",
        )
        response = Mock(headers={})
        fname = _resolve_filename(response, c, 1)
        assert fname == "下载文件"


# ---------------------------------------------------------------------------
# 下载逻辑
# ---------------------------------------------------------------------------

class TestDownloadAttachment:
    def _make_mock_get(self, content=b"content", headers=None):
        """创建 mock 的 session.get 方法。"""
        if headers is None:
            headers = {"Content-Type": "application/pdf", "Content-Disposition": ""}

        def mock_get(self, url, **kwargs):
            resp = Mock(status_code=200)
            resp.headers = dict(headers)
            resp.iter_content.return_value = [content]
            resp.raise_for_status = lambda: None
            return resp

        return mock_get

    def test_successful_download(self, monkeypatch):
        """正常下载应保存文件并计算 SHA-256。"""
        content = b"fake pdf content"
        expected_sha256 = hashlib.sha256(content).hexdigest()

        monkeypatch.setattr(
            "requests.Session.get",
            self._make_mock_get(content=content),
        )

        from seu_monitor.core.http import new_session
        session = new_session()

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            candidate = AttachmentCandidate(
                url="https://example.com/test.pdf",
                text="测试文件",
            )
            result = download_attachment(session, candidate, target, 1)

            assert result.filename == "test.pdf"
            assert result.sha256 == expected_sha256
            assert result.size == len(content)
            assert result.content_type == "application/pdf"
            assert result.error is None
            assert (target / "test.pdf").exists()
            assert (target / "test.pdf").read_bytes() == content

    def test_download_failure_records_error(self, monkeypatch):
        """下载失败应记录 error 且不崩溃。"""
        def mock_get(self, url, **kwargs):
            raise Exception("Connection refused")

        monkeypatch.setattr("requests.Session.get", mock_get)

        from seu_monitor.core.http import new_session
        session = new_session()

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            candidate = AttachmentCandidate(
                url="https://example.com/broken.pdf",
                text="损坏文件",
            )
            result = download_attachment(session, candidate, target, 1)

            assert result.error is not None
            assert "Connection refused" in result.error

    def test_html_response_skipped(self, monkeypatch):
        """Content-Type 为 text/html 且 URL 非附件后缀时应跳过。"""
        content = b"<html><body>not a file</body></html>"

        def mock_get(self, url, **kwargs):
            resp = Mock(status_code=200)
            resp.headers = {
                "Content-Type": "text/html; charset=utf-8",
                "Content-Disposition": "",
            }
            resp.iter_content.return_value = [content]
            resp.raise_for_status = lambda: None
            return resp

        monkeypatch.setattr("requests.Session.get", mock_get)

        from seu_monitor.core.http import new_session
        session = new_session()

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            candidate = AttachmentCandidate(
                url="https://example.com/notice.jsp",
                text="普通通知",
            )
            result = download_attachment(session, candidate, target, 1)
            assert result.error is not None
            assert "跳过" in result.error

    def test_pdf_url_with_html_content_type_not_skipped(self, monkeypatch):
        """URL 是 .pdf 后缀即使 Content-Type 是 text/html 也要下载。"""
        content = b"%PDF-1.4 fake"

        def mock_get(self, url, **kwargs):
            resp = Mock(status_code=200)
            resp.headers = {
                "Content-Type": "text/html",
                "Content-Disposition": "",
            }
            resp.iter_content.return_value = [content]
            resp.raise_for_status = lambda: None
            return resp

        monkeypatch.setattr("requests.Session.get", mock_get)

        from seu_monitor.core.http import new_session
        session = new_session()

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            candidate = AttachmentCandidate(
                url="https://example.com/file.pdf",
                text="PDF文件",
            )
            result = download_attachment(session, candidate, target, 1)
            assert result.error is None
            assert result.filename == "file.pdf"


class TestDownloadAttachments:
    def test_multiple_attachments(self, monkeypatch):
        """多个附件应全部下载。"""
        contents = [b"content1", b"content2"]
        call_count = 0

        def mock_get(self, url, **kwargs):
            nonlocal call_count
            resp = Mock(status_code=200)
            resp.headers = {"Content-Type": "application/pdf", "Content-Disposition": ""}
            resp.iter_content.return_value = [contents[call_count]]
            resp.raise_for_status = lambda: None
            call_count += 1
            return resp

        monkeypatch.setattr("requests.Session.get", mock_get)

        from seu_monitor.core.http import new_session
        session = new_session()

        with tempfile.TemporaryDirectory() as tmp:
            candidates = [
                AttachmentCandidate(url="https://ex.com/1.pdf", text="文件1"),
                AttachmentCandidate(url="https://ex.com/2.pdf", text="文件2"),
            ]
            results = download_attachments(candidates, Path(tmp), session)

            assert len(results) == 2
            assert results[0].error is None
            assert results[1].error is None

    def test_one_fails_others_continue(self, monkeypatch):
        """一个附件失败不应影响其他附件。"""
        call_count = 0

        def mock_get(self, url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("网络错误")
            resp = Mock(status_code=200)
            resp.headers = {"Content-Type": "application/pdf", "Content-Disposition": ""}
            resp.iter_content.return_value = [b"ok"]
            resp.raise_for_status = lambda: None
            return resp

        monkeypatch.setattr("requests.Session.get", mock_get)

        from seu_monitor.core.http import new_session
        session = new_session()

        with tempfile.TemporaryDirectory() as tmp:
            candidates = [
                AttachmentCandidate(url="https://ex.com/broken.pdf", text="损坏"),
                AttachmentCandidate(url="https://ex.com/good.pdf", text="正常"),
            ]
            results = download_attachments(candidates, Path(tmp), session)

            assert len(results) == 2
            assert results[0].error is not None
            assert results[1].error is None
            assert results[1].filename == "good.pdf"

    def test_empty_candidates(self):
        """空候选列表应返回空。"""
        results = download_attachments([], Path("/tmp"))
        assert results == []

    def test_non_attachment_skipped(self):
        """非附件候选应被跳过。"""
        candidates = [
            AttachmentCandidate(
                url="https://ex.com/normal.htm",
                text="普通链接",
            ),
        ]
        results = download_attachments(candidates, Path("/tmp"))
        assert len(results) == 0
