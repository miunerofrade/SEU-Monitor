package monitor

import (
	"context"
	"net/http"
	"strings"
	"testing"
)

func TestUpdateDownloadsAndChecksums(t *testing.T) {
	binary := "new executable"
	checksum := hash([]byte(binary))
	client := NewClient("")
	client.Transport = roundTripFunc(func(r *http.Request) (*http.Response, error) {
		switch r.URL.Path {
		case "/checksums":
			return response(r, 200, checksum+"  monitor_linux_amd64\n", nil), nil
		case "/binary":
			return response(r, 200, binary, nil), nil
		}
		t.Fatal("unexpected download")
		return nil, nil
	})
	release := Release{Tag: "v0.3.0", Assets: []ReleaseAsset{{"monitor_linux_amd64", "https://example.com/binary"}, {"checksums.txt", "https://example.com/checksums"}}}
	b, e := checkedDownload(context.Background(), client, release, "linux_amd64")
	if e != nil || string(b) != binary {
		t.Fatal(e)
	}
	checksum = strings.Repeat("0", 64)
	if _, e = checkedDownload(context.Background(), client, release, "linux_amd64"); e == nil {
		t.Fatal("checksum mismatch accepted")
	}
	if _, e = checkedDownload(context.Background(), client, release, "linux_arm64"); e == nil {
		t.Fatal("missing architecture accepted")
	}
}
