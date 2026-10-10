"""附件下载模块。

从 Detail.attachments 中下载附件到本地，并在 meta.json 中记录结果。
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from urllib.parse import unquote, urlsplit
from pathlib import Path
from typing import List, Optional

import requests

from seu_monitor.core.http import new_session
from seu_monitor.core.models import AttachmentCandidate, SavedAttachment
from .attachment_rules import file_suffix, is_attachment_candidate, is_html_response

logger = logging.getLogger(__name__)

_is_attachment_candidate = is_attachment_candidate


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
        from email.message import Message

        header = Message()
        header["Content-Disposition"] = cd
        fname = header.get_filename() or ""
        if fname:
            return _sanitize_filename(fname)

    # 策略 2：URL path
    url_path = unquote(urlsplit(candidate.url).path)
    url_filename = url_path.rstrip("/").split("/")[-1]
    if url_filename and "." in url_filename:
        return _sanitize_filename(url_filename)

    # 策略 3：链接文本
    if candidate.text and candidate.text != url_filename:
        return _sanitize_filename(candidate.text)

    # 策略 4：fallback
    ext = file_suffix(candidate.url)
    return f"attachment_{index}{ext}"


def _is_html_content(response: requests.Response) -> bool:
    """判断响应是否明显是 HTML。"""
    return is_html_response(response.headers.get("Content-Type", "") or "")


def download_attachment(
    session: requests.Session,
    candidate: AttachmentCandidate,
    target_dir: Path,
    index: int,
) -> SavedAttachment:
    """下载单个附件，返回 SavedAttachment（不会抛出网络异常）。"""
    url = candidate.url
    result = SavedAttachment(url=url, filename="")

    resp = None
    temporary = None
    try:
        resp = session.get(url, timeout=15, stream=True)
        resp.raise_for_status()

        # Reject login/error HTML even when the URL ends in .pdf.
        if _is_html_content(resp):
            result.error = "跳过：响应为 text/html，不是附件原文件"
            logger.debug("跳过 HTML 响应: %s", url)
            return result

        # 确定文件名
        filename = _resolve_filename(resp, candidate, index)
        # Distinct URLs with equal names must not overwrite each other.
        stem, ext = os.path.splitext(filename)
        filename = f"{stem}--{hashlib.sha256(url.encode()).hexdigest()[:10]}{ext}"
        filepath = target_dir / filename

        # 下载并计算 SHA-256
        sha256 = hashlib.sha256()
        size = 0
        temporary = filepath.with_name(filepath.name + ".part")
        with open(temporary, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
                    sha256.update(chunk)
                    size += len(chunk)

        temporary.replace(filepath)
        result.filename = filename
        result.sha256 = sha256.hexdigest()
        result.size = size
        result.content_type = resp.headers.get("Content-Type", "")
        logger.info("附件下载成功: %s (%d bytes)", filename, size)

    except Exception as e:
        error_msg = str(e)
        result.error = error_msg
        logger.debug("附件下载失败 (%s): %s", url, error_msg)

    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        if resp is not None:
            resp.close()

    return result


def download_attachments(
    candidates: List[AttachmentCandidate],
    target_dir: Path,
    session: Optional[requests.Session] = None,
    previous: Optional[dict[str, SavedAttachment]] = None,
) -> List[SavedAttachment]:
    """下载所有候选附件，返回结果列表。

    单个下载失败不会中断整体流程。
    """
    if not candidates:
        return []

    target_dir.mkdir(parents=True, exist_ok=True)
    owned_session = session is None
    session = session or new_session()
    previous = previous or {}
    try:
        results: List[SavedAttachment] = []
        unique = {candidate.url: candidate for candidate in candidates}
        for i, candidate in enumerate(unique.values()):
            if not is_attachment_candidate(candidate):
                logger.debug("跳过非附件链接: %s", candidate.url)
                continue
            result = previous.get(candidate.url)
            if result is None or result.error:
                result = download_attachment(session, candidate, target_dir, i + 1)
            results.append(result)
        return results
    finally:
        if owned_session:
            session.close()
