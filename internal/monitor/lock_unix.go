//go:build linux || darwin

package monitor

import (
	"errors"
	"os"
	"path/filepath"
	"syscall"
)

func scanLock(root string) (func(), error) {
	f, e := os.OpenFile(filepath.Join(root, ".scan.lock"), os.O_CREATE|os.O_RDWR, 0600)
	if e != nil {
		return nil, e
	}
	if syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB) != nil {
		f.Close()
		return nil, errors.New("已有扫描正在运行")
	}
	return func() { syscall.Flock(int(f.Fd()), syscall.LOCK_UN); f.Close() }, nil
}
