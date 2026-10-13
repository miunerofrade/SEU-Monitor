from bs4 import BeautifulSoup
from seu_monitor.adapters.wp_news import WpNewsAdapter
from seu_monitor.core.html_text import extract_text, text_snippet
from seu_monitor.core.notify import FeishuNotifier
from unittest.mock import Mock


def test_inline_word_fragments_are_joined_but_paragraphs_preserved():
    html = '<div class="wp_articlecontent"><p><span>根据</span><b>学科竞赛</b><span>管理系统</span>，<span>202</span><span>5</span>-<span>202</span><span>6</span>学年。</p><p><span>电</span><span>话：</span><span>123</span></p></div>'
    detail = WpNewsAdapter.parse_detail_html(html, 'https://example.com/a.htm')
    assert detail.text == '根据学科竞赛管理系统，2025-2026学年。\n\n电话：123'
    assert detail.markdown == detail.text


def test_explicit_breaks_english_spaces_and_table_cells():
    html = '<div><p>Hello <b>world</b><br>下一行</p><table><tr><td>姓名</td><td>奖项</td></tr><tr><td>张三</td><td>一等奖</td></tr></table></div>'
    text = extract_text(BeautifulSoup(html, 'html.parser'))
    assert 'Hello world\n下一行' in text
    assert '姓名\t奖项\n张三\t一等奖' in text


def test_summary_is_normalized_before_truncation():
    assert text_snippet(' 第一段\n\n第二段 ') == '第一段 第二段'
    assert text_snippet('ab\ncd', 3) == 'ab ...'
    notifier = FeishuNotifier('configured')
    notifier._post = Mock(return_value=True)
    notifier.send('实践教学', '竞赛名单', '2026-10-13', 'https://example.com/a', '正文\n\n联系人')
    payload = notifier._post.call_args.args[0]
    assert payload['content']['post']['zh_cn']['content'][1][0]['text'] == '正文 联系人'
