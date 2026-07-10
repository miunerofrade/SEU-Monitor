"""统一数据模型 — Notice、Detail、AttachmentCandidate、SavedAttachment。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Notice:
    """列表页上的一条公告摘要。"""
    site_id: str       # 站点标识，如 "jwc"
    column_id: str     # 栏目标识，如 "zxdt"
    id: str            # 稳定 ID（从 URL 提取，用于去重）
    title: str         # 公告标题
    url: str           # 完整详情页 URL
    date: str          # 发布日期（字符串，保持原样）


@dataclass
class AttachmentCandidate:
    """详情页中提取的附件候选链接（尚未下载）。"""
    url: str           # 附件下载链接（已转为绝对 URL）
    text: str          # 链接文本（如 "选课指南.pdf"）
    source: str = ""   # 来源说明，如 "detail_link"


@dataclass
class SavedAttachment:
    """已下载的附件记录（可序列化为 JSON）。"""
    url: str                           # 原始下载链接
    filename: str                      # 实际保存的文件名
    sha256: Optional[str] = None       # 文件 SHA-256 哈希
    size: Optional[int] = None         # 文件大小（字节）
    content_type: Optional[str] = None # Content-Type
    error: Optional[str] = None        # 下载失败时的错误信息


@dataclass
class Detail:
    """公告详情页内容。"""
    html: str                                          # 提取后的正文区域 HTML
    text: str                                          # 纯文本
    raw_html: str = ""                                 # 原始完整页面 HTML（用于快照）
    attachments: List[AttachmentCandidate] = field(default_factory=list)
