//go:build linux || darwin

package monitor

import (
	"errors"
	"os"
	"path/filepath"
	"syscall"
)

func scanLock(root string) (func(), error) { return operationLock(filepath.Join(root, ".scan.lock")) }

func operationLock(path string) (func(), error) {
	f, e := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0600)
	if e != nil {
		return nil, e
	}
	if syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB) != nil {
		f.Close()
		return nil, errors.New("已有相同操作正在运行")
	}
	return func() { syscall.Flock(int(f.Fd()), syscall.LOCK_UN); f.Close() }, nil
}
