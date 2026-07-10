"""附件下载模块。

从 Detail.attachments 中下载附件到本地，并在 meta.json 中记录结果。
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from pathlib import Path
from typing import List, Optional

import requests

from seu_monitor.core.http import new_session
from seu_monitor.core.models import AttachmentCandidate, SavedAttachment

logger = logging.getLogger(__name__)

# 常见附件扩展名（小写）
_ATTACHMENT_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".zip", ".rar", ".7z", ".txt", ".jpg", ".jpeg", ".png",
}

# 可接受的 Content-Type 前缀
_ACCEPTABLE_TYPES = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats",
    "application/vnd.ms-",
    "application/vnd.ms-excel",
    "application/zip",
    "application/x-rar",
    "application/x-7z",
    "image/",
    "text/plain",
}

# 附件链接文本关键词
_ATTACHMENT_KEYWORDS = ["附件", "下载", "pdf", "doc", "xls", "ppt", "zip"]


def _is_attachment_candidate(candidate: AttachmentCandidate) -> bool:
    """判断是否是值得尝试下载的附件候选。"""
    url_lower = candidate.url.lower()

    # 规则 1：URL 后缀匹配附件扩展名
    for ext in _ATTACHMENT_EXTENSIONS:
        if url_lower.endswith(ext):
            return True

    # 规则 2：链接文本包含附件关键词
    text_lower = candidate.text.lower()
    for kw in _ATTACHMENT_KEYWORDS:
        if kw in text_lower:
            return True

    return False


def _sanitize_filename(filename: str) -> str:
    """清理文件名中的非法字符。"""
    sanitized = re.sub(r'[/:*?"<>|\\]', "_", filename)
    # 限制长度
    if len(sanitized) > 200:
        name, ext = os.path.splitext(sanitized)
        sanitized = name[:196] + ext
    return sanitized.strip() or "unnamed"


def _resolve_filename(
    response: requests.Response,
    candidate: AttachmentCandidate,
    index: int,
) -> str:
    """按优先级确定文件名。

    1. Content-Disposition filename
    2. URL path 中的文件名
    3. 链接文本
    4. attachment_<index>
    """
    # 策略 1：Content-Disposition
    cd = response.headers.get("Content-Disposition", "")
    if cd:
        import cgi
        _, params = cgi.parse_header(cd)
        fname = params.get("filename", "")
        if fname:
            return _sanitize_filename(fname)

    # 策略 2：URL path
    url_path = candidate.url.split("?")[0]
    url_filename = url_path.rstrip("/").split("/")[-1]
    if url_filename and "." in url_filename:
        return _sanitize_filename(url_filename)

    # 策略 3：链接文本
    if candidate.text and candidate.text != url_filename:
        return _sanitize_filename(candidate.text)

    # 策略 4：fallback
    ext = ""
    for known_ext in _ATTACHMENT_EXTENSIONS:
        if candidate.url.lower().endswith(known_ext):
            ext = known_ext
            break
    return f"attachment_{index}{ext}"


def _is_html_content(response: requests.Response) -> bool:
    """判断响应是否明显是 HTML。"""
    ct = (response.headers.get("Content-Type", "") or "").lower()
    if "text/html" in ct:
        return True
    return False


def download_attachment(
    session: requests.Session,
    candidate: AttachmentCandidate,
    target_dir: Path,
    index: int,
) -> SavedAttachment:
    """下载单个附件，返回 SavedAttachment（不会抛出网络异常）。"""
    url = candidate.url
    result = SavedAttachment(url=url, filename="")

    try:
        resp = session.get(url, timeout=15, stream=True)
        resp.raise_for_status()

        # 如果返回了 HTML 且 URL 不像附件扩展名，跳过
        if _is_html_content(resp) and not candidate.url.lower().endswith(
            tuple(_ATTACHMENT_EXTENSIONS)
        ):
            result.error = "跳过：响应为 text/html，URL 后缀不匹配附件格式"
            logger.debug("跳过 HTML 响应: %s", url)
            return result

        # 确定文件名
        filename = _resolve_filename(resp, candidate, index)
        filepath = target_dir / filename

        # 下载并计算 SHA-256
        sha256 = hashlib.sha256()
        size = 0
        with open(filepath, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
                    sha256.update(chunk)
                    size += len(chunk)

        result.filename = filename
        result.sha256 = sha256.hexdigest()
        result.size = size
        result.content_type = resp.headers.get("Content-Type", "")
        logger.info("附件下载成功: %s (%d bytes)", filename, size)

    except Exception as e:
        error_msg = str(e)
        result.error = error_msg
        logger.debug("附件下载失败 (%s): %s", url, error_msg)

    return result


def download_attachments(
    candidates: List[AttachmentCandidate],
    target_dir: Path,
    session: Optional[requests.Session] = None,
) -> List[SavedAttachment]:
    """下载所有候选附件，返回结果列表。

    单个下载失败不会中断整体流程。
    """
    if not candidates:
        return []

    target_dir.mkdir(parents=True, exist_ok=True)
    session = session or new_session()

    results: List[SavedAttachment] = []
    for i, candidate in enumerate(candidates):
        if not _is_attachment_candidate(candidate):
            logger.debug("跳过非附件链接: %s", candidate.url)
            continue
        result = download_attachment(session, candidate, target_dir, i + 1)
        results.append(result)

    return results
