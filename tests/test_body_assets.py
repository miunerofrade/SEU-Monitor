from bs4 import BeautifulSoup
from seu_monitor.core.body_assets import extract_body_assets


def test_viewer_relative_pdf_and_lazy_images_use_their_correct_base():
    body = BeautifulSoup('''<article>
      <iframe class="wp_pdf_player" src="/viewer/index.html?file=../files/course.pdf"></iframe>
      <img src="placeholder.jpg" data-src="/image?id=2" alt="示意图">
      <a href="/image?id=2">下载图片</a>
      <span class="wp_pdf_player" pdfsrc="javascript:alert(1)"></span>
    </article>''', 'html.parser')
    candidates, markdown = extract_body_assets(body, 'https://example.com/news/notice.htm')
    assert {c.url for c in candidates} == {
        'https://example.com/files/course.pdf', 'https://example.com/image?id=2'}
    image = next(c for c in candidates if c.url.endswith('id=2'))
    assert image.source == 'inline_image'
    assert '![示意图](<https://example.com/image?id=2>)' in markdown
    assert 'placeholder' not in markdown
