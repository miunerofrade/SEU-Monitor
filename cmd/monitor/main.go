package main

import (
	"context"
	"fmt"
	"github.com/miunerofrade/SEU-Monitor/internal/monitor"
	"os"
	"os/signal"
	"syscall"
)

var version = "0.3.0-dev"

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	code, err := monitor.Main(ctx, os.Args[1:], version)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
	}
	os.Exit(code)
}
