"""按 HTML 的段落边界提取文本，行内标签不制造换行。"""
import re
from bs4 import Comment, NavigableString

_BLOCKS = {'p', 'div', 'article', 'section', 'blockquote', 'li', 'ul', 'ol',
           'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'header', 'footer', 'table'}


def extract_text(element):
    parts = []

    def visit(node):
        if isinstance(node, Comment):
            return
        if isinstance(node, NavigableString):
            parts.append(re.sub(r'\s+', ' ', str(node)))
            return
        name = getattr(node, 'name', '')
        if name in {'script', 'style', 'noscript'}:
            return
        if name == 'br':
            parts.append('\n')
            return
        if name == 'pre':
            parts.extend(['\n\n', node.get_text(), '\n\n'])
            return
        if name in _BLOCKS:
            parts.append('\n\n')
        for child in node.children:
            visit(child)
        if name in _BLOCKS:
            parts.append('\n\n')
        elif name == 'tr':
            parts.append('\n')
        elif name in {'td', 'th'}:
            parts.append('\t')

    visit(element)
    text = ''.join(parts)
    text = re.sub(r'[ \t]+\n', '\n', text)
    text = re.sub(r'\n +', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def text_snippet(text, limit=100):
    """推送摘要连续显示，长度按清理后的文字计算。"""
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:limit] + ('...' if len(text) > limit else '')
