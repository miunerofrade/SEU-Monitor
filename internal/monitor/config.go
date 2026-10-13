package monitor

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
)

type Config struct {
	Account  string `json:"account"`
	Password string `json:"password"`
	Webhook  string `json:"webhook"`
	Interval int    `json:"interval"`
	Port     int    `json:"port"`
}

func DefaultConfig() Config {
	return Config{Interval: 3600, Port: 8888, Webhook: os.Getenv("FEISHU_WEBHOOK")}
}
func (c Config) Validate() error {
	if c.Interval < 60 {
		return errors.New("扫描间隔至少 60 秒")
	}
	if c.Port < 1024 || c.Port > 65535 {
		return errors.New("代理端口必须在 1024–65535 之间")
	}
	return nil
}
func DataDirectory(value string) (string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	if value == "" {
		value = os.Getenv("MONITOR_DATA_DIR")
	}
	if value == "" {
		value = filepath.Join(home, ".local/share/seu-monitor")
	}
	if strings.HasPrefix(value, "~/") {
		value = filepath.Join(home, value[2:])
	}
	value, err = filepath.Abs(value)
	if err != nil {
		return "", err
	}
	if err = os.MkdirAll(value, 0700); err != nil {
		return "", err
	}
	if err = os.Chmod(value, 0700); err != nil {
		return "", err
	}
	return value, nil
}
func LoadConfig(dir string) (Config, error) {
	c := DefaultConfig()
	data, err := os.ReadFile(filepath.Join(dir, "config.json"))
	if err != nil && !os.IsNotExist(err) {
		return c, errors.New("无法读取配置")
	}
	if err == nil {
		if json.Unmarshal(data, &c) != nil {
			return c, errors.New("配置格式无效")
		}
	}
	return c, c.Validate()
}
func atomicWrite(path string, data []byte, mode os.FileMode) error {
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		return err
	}
	f, err := os.CreateTemp(filepath.Dir(path), ".monitor-write-*")
	if err != nil {
		return err
	}
	name := f.Name()
	defer os.Remove(name)
	if err = f.Chmod(mode); err == nil {
		_, err = f.Write(data)
	}
	if err == nil {
		err = f.Sync()
	}
	closeErr := f.Close()
	if err == nil {
		err = closeErr
	}
	if err != nil {
		return err
	}
	return os.Rename(name, path)
}
func SaveConfig(dir string, c Config) error {
	if err := c.Validate(); err != nil {
		return err
	}
	b, e := json.MarshalIndent(c, "", "  ")
	if e != nil {
		return e
	}
	return atomicWrite(filepath.Join(dir, "config.json"), b, 0600)
}
