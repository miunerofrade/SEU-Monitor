"""正文资源提取：附件、内嵌 PDF、图片引用及候选去重。"""

from urllib.parse import parse_qs, urljoin, urlsplit
from bs4 import BeautifulSoup
from .attachment_rules import is_attachment_candidate
from .models import AttachmentCandidate


def extract_body_assets(content, base_url: str):
    attachments = []
    for link in content.find_all("a", href=True):
        href = link["href"]
        candidate = AttachmentCandidate(urljoin(base_url, href),
                                        link.get_text(strip=True) or href, "detail_link")
        if is_attachment_candidate(candidate):
            attachments.append(candidate)

    for player in content.find_all(class_="wp_pdf_player"):
        path = player.get("pdfsrc") or player.get("file")
        origin = base_url
        if not path and player.get("src"):
            origin = urljoin(base_url, player["src"])
            path = parse_qs(urlsplit(origin).query).get("file", [None])[0]
        if path:
            candidate = AttachmentCandidate(urljoin(origin, path), "附件.pdf", "pdf_player")
            if is_attachment_candidate(candidate):
                attachments.append(candidate)

    markdown_body = BeautifulSoup(str(content), "html.parser")
    for image in markdown_body.find_all("img"):
        src = image.get("data-src") or image.get("src") or ""
        candidate = AttachmentCandidate(urljoin(base_url, src),
                                        image.get("alt") or "正文图片", "inline_image")
        if not src or not is_attachment_candidate(candidate):
            image.decompose()
            continue
        alt = candidate.text.replace("\n", " ")
        alt = alt.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
        attachments.append(candidate)
        image.replace_with(f"\n![{alt}](<{candidate.url}>)\n")
    # 图片覆盖同 URL 的普通链接，保证无扩展名的图片也能下载。
    unique = {candidate.url: candidate for candidate in attachments}
    return list(unique.values()), markdown_body.get_text(separator="\n", strip=True)
