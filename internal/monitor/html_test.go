package monitor

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

func TestPythonDetailParity(t *testing.T) {
	b, e := os.ReadFile("testdata/detail_golden.json")
	if e != nil {
		t.Fatal(e)
	}
	var cases []struct {
		Name, HTML, Base, Text, Markdown string
		Attachments                      []Candidate
	}
	if e = json.Unmarshal(b, &cases); e != nil {
		t.Fatal(e)
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			d, e := ParseDetail(c.HTML, c.Base)
			if e != nil {
				t.Fatal(e)
			}
			if d.Text != c.Text {
				t.Errorf("text mismatch\nGo: %q\nPython: %q", d.Text, c.Text)
			}
			if d.Markdown != c.Markdown {
				t.Errorf("markdown mismatch\nGo: %q\nPython: %q", d.Markdown, c.Markdown)
			}
			if !reflect.DeepEqual(d.Attachments, c.Attachments) {
				t.Errorf("attachments mismatch: %#v / %#v", d.Attachments, c.Attachments)
			}
		})
	}
}
func TestPythonListParity(t *testing.T) {
	b, e := os.ReadFile("testdata/list_golden.json")
	if e != nil {
		t.Fatal(e)
	}
	var c struct {
		HTML, Base string
		Notices    []Notice
	}
	json.Unmarshal(b, &c)
	got, e := ParseList(c.HTML, c.Base)
	if e != nil {
		t.Fatal(e)
	}
	if !reflect.DeepEqual(got, c.Notices) {
		t.Fatalf("list mismatch: %#v / %#v", got, c.Notices)
	}
}
func TestCandidateAndSummary(t *testing.T) {
	for _, c := range []Candidate{{"https://example.com/a.pdf?v=1", "报名表", ""}, {"https://example.com/viewer?file=x", "PDF", "pdf_player"}, {"https://example.com/img?id=1", "图", "inline_image"}} {
		if !IsCandidate(c) {
			t.Fatal(c)
		}
	}
	for _, c := range []Candidate{{"https://example.com/list.htm", "下载专区", ""}, {"javascript:alert(1)", "文件", "pdf_player"}} {
		if IsCandidate(c) {
			t.Fatal(c)
		}
	}
	if Snippet("正文\n\n联系人") != "正文 联系人" {
		t.Fatal("summary fragmented")
	}
}

func TestInvalidNestedSchoolLinks(t *testing.T) {
	source := `<div id="wp_news_w8"><table><tr><td class="main"><a href="/2026/1013/c12345a123456/page.htm"><a href="/2026/1013/c12345a123456/page.htm" title="通知完整标题"><font>通知标题</font></a></a></td><td>2026-10-13</td></tr></table></div>`
	list, e := ParseList(source, "https://example.com/list.htm")
	if e != nil || len(list) != 1 || list[0].Title != "通知标题" {
		t.Fatal(list, e)
	}
}

func TestEditorPlaceholderCandidate(t *testing.T) {
	for _, source := range []string{"inline_image", "detail_link"} {
		for _, u := range []string{"https://example.com/2026/article/ueditor/themes/default/images/spacer.gif?x=1", "https://example.com/ueditor/lang/zh-cn/images/localimage.png"} {
			if IsCandidate(Candidate{u, "图片", source}) {
				t.Fatalf("editor placeholder accepted: %s", u)
			}
		}
		for _, u := range []string{"https://example.com/_upload/spacer.gif", "https://example.com/_upload/localimage.png", "https://example.com/_upload/photo.gif"} {
			if !IsCandidate(Candidate{u, "图片", source}) {
				t.Fatalf("real image rejected: %s", u)
			}
		}
	}
}

func TestRedesignedNoticeList(t *testing.T) {
	source := `<nav class="news-nav"><a href="/unrelated">导航</a></nav><div class="news-list-wrap"><div frag="窗口25">
 <div class="news-list-item"><div class="news-item-title"><a href="/_t3454/2026/1014/c21678a586035/page.htm" title="完整标题">缩略标题</a></div><div class="news-item-date-ym">2026-10-15</div></div>
 <div class="news-list-item"><div class="news-item-title"><a href="https://futuretech.seu.edu.cn/2026/1013/c64035a585923/page.htm">转发通知</a></div><div class="news-item-date-ym">2026-10-14</div></div>
 <div class="news-list-item"><div class="news-item-title"><a href="https://mp.weixin.qq.com/s/example123456">公众号转载</a></div><div class="news-item-date-ym">2026-10-14</div></div>
 </div></div>`
	expected := []Notice{
		{SiteID: "jwc", ID: "c21678a586035", Title: "完整标题", URL: "https://jwc.seu.edu.cn/_t3454/2026/1014/c21678a586035/page.htm", Date: "2026-10-15"},
		{SiteID: "jwc", ID: "c64035a585923", Title: "转发通知", URL: "https://futuretech.seu.edu.cn/2026/1013/c64035a585923/page.htm", Date: "2026-10-14"},
	}
	got, err := ParseList(source, "https://jwc.seu.edu.cn/jwxx/list.htm")
	if err != nil || !reflect.DeepEqual(got, expected) {
		t.Fatal(got, err)
	}
}

func TestNoticeListStructureErrors(t *testing.T) {
	for _, source := range []string{
		`<html><body>网站维护</body></html>`,
		`<div class="news-navigation"><a href="/">首页</a></div>`,
		`<div class="news-list-wrap"><div class="news-list-item"><div class="news-item-title"><a href="/2026/c12345a123456/page.htm">通知</a></div></div></div>`,
		`<div class="news-list-wrap"><div class="news-list-item"><div class="news-item-title"><a href="javascript:void(0)">通知</a></div><div class="news-item-date-ym">2026-10-23</div></div></div>`,
	} {
		if got, err := ParseList(source, "https://example.com/list.htm"); err == nil {
			t.Fatalf("unrecognized page treated as empty: %v", got)
		}
	}
	for _, source := range []string{`<div class="news-list-wrap"></div>`, `<div id="wp_news_w1"><table></table></div>`} {
		if got, err := ParseList(source, "https://example.com/list.htm"); err != nil || len(got) != 0 {
			t.Fatal(got, err)
		}
	}
}

func TestNoticeLinksTrimSurroundingWhitespace(t *testing.T) {
	source := `<div class="news-list-wrap"><div class="news-list-item"><div class="news-item-title"><a href=" https://power.seu.edu.cn/2026/1008/c9503a585147/page.htm ">转载通知</a></div><div class="news-item-date-ym">2026-10-23</div></div></div>`
	got, err := ParseList(source, "https://jwc.seu.edu.cn/gjjl/list.htm")
	if err != nil || len(got) != 1 || got[0].URL != "https://power.seu.edu.cn/2026/1008/c9503a585147/page.htm" {
		t.Fatal(got, err)
	}
	if got := absolute("https://example.com/list.htm", " /files/my%20file.pdf "); got != "https://example.com/files/my%20file.pdf" {
		t.Fatal(got)
	}
}
