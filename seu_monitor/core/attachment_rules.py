"""网页解析与下载共用的附件候选规则。"""

from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

from .models import AttachmentCandidate

ATTACHMENT_EXTENSIONS = {
    '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
    '.zip', '.rar', '.7z', '.txt', '.jpg', '.jpeg', '.png',
}


def is_attachment_candidate(candidate: AttachmentCandidate) -> bool:
    url = urlsplit(candidate.url)
    if url.scheme not in {'http', 'https'}:
        return False
    if candidate.source in {'inline_image', 'pdf_player'}:
        return True
    path = unquote(url.path).lower()
    suffix = PurePosixPath(path).suffix
    if suffix in ATTACHMENT_EXTENSIONS:
        return True
    # 栏目和普通页面不能仅因标题中有“下载”而变成附件。
    if suffix in {'.htm', '.html'} or path.endswith('/'):
        return False
    text = candidate.text.lower().strip()
    if text in {'下载专区', '下载中心', '附件下载专区'}:
        return False
    return any(word in text for word in ('附件', '下载', 'pdf', 'doc', 'xls', 'ppt', 'zip'))
