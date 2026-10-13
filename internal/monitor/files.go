package monitor

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
)

func jsonBytes(v any) ([]byte, error) { return json.MarshalIndent(v, "", "  ") }
func copyDirectory(source, target string) error {
	return filepath.WalkDir(source, func(p string, entry os.DirEntry, e error) error {
		if e != nil {
			return e
		}
		relative, e := filepath.Rel(source, p)
		if e != nil {
			return e
		}
		dest := filepath.Join(target, relative)
		if entry.Type()&os.ModeSymlink != 0 {
			return errors.New("归档中存在符号链接，停止迁移")
		}
		if entry.IsDir() {
			return os.MkdirAll(dest, 0700)
		}
		if entry.Name() == "raw.html" || entry.Name() == "raw.html.tmp" {
			return nil
		}
		b, e := os.ReadFile(p)
		if e != nil {
			return e
		}
		return atomicWrite(dest, b, 0600)
	})
}
