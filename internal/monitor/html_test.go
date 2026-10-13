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
