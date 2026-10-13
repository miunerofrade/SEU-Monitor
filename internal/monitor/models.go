package monitor

type Column struct{ ID, Name, URL string }

var Columns = []Column{
	{"zxdt", "最新动态", "https://jwc.seu.edu.cn/zxdt/list.htm"},
	{"jwxx", "教务信息", "https://jwc.seu.edu.cn/jwxx/list.htm"},
	{"xjgl", "学籍管理", "https://jwc.seu.edu.cn/xjgl/list.htm"},
	{"sjjx", "实践教学", "https://jwc.seu.edu.cn/sjjx/list.htm"},
	{"gjjl", "国际交流", "https://jwc.seu.edu.cn/gjjl/list.htm"},
	{"cbxx", "文化素质教育", "https://jwc.seu.edu.cn/cbxx/list.htm"},
}

func columnName(id string) string {
	for _, c := range Columns {
		if c.ID == id {
			return c.Name
		}
	}
	return id
}

type Notice struct {
	SiteID   string `json:"site_id"`
	ColumnID string `json:"column_id"`
	ID       string `json:"notice_id"`
	Title    string `json:"title"`
	URL      string `json:"url"`
	Date     string `json:"date"`
}
type Candidate struct {
	URL    string `json:"url"`
	Text   string `json:"text"`
	Source string `json:"source"`
}
type Attachment struct {
	URL         string `json:"url"`
	Filename    string `json:"filename"`
	SHA256      string `json:"sha256,omitempty"`
	Size        int64  `json:"size,omitempty"`
	ContentType string `json:"content_type,omitempty"`
	Error       string `json:"error,omitempty"`
}
type Detail struct {
	Text        string      `json:"text"`
	Markdown    string      `json:"markdown"`
	HTML        string      `json:"html"`
	RawHTML     string      `json:"-"`
	Attachments []Candidate `json:"attachments"`
}
type Metadata struct {
	Notice
	FetchedAt      string       `json:"fetched_at"`
	HTMLHash       string       `json:"html_sha256"`
	TextHash       string       `json:"text_sha256"`
	ContentVersion int          `json:"content_version"`
	ContentText    *string      `json:"content_text,omitempty"`
	SnapshotPath   string       `json:"snapshot_path"`
	Attachments    []Attachment `json:"attachments"`
}
