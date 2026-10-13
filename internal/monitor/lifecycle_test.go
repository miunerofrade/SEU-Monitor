package monitor

import (
	"context"
	"errors"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestCorePromptWithoutNewlineAndSecretFiltering(t *testing.T) {
	lines := coreLines(context.Background(), strings.NewReader("Visit /login to login\nPlease enter callback URL: "))
	var got []string
	for line := range lines {
		got = append(got, line)
	}
	if len(got) != 2 || got[1] != "Please enter callback URL:" {
		t.Fatal(got)
	}
	t.Setenv("VPN_PASSWORD", "must-not-leak")
	t.Setenv("HTTP_PROXY", "http://proxy")
	t.Setenv("FEISHU_WEBHOOK", "private")
	t.Setenv("ZJU_CONNECT_PASSWORD", "private")
	t.Setenv("GODEBUG", "tlsmlkem=1,other=1")
	env := strings.Join(coreEnv(), "\n")
	for _, value := range []string{"must-not-leak", "HTTP_PROXY=", "FEISHU_WEBHOOK=", "ZJU_CONNECT_PASSWORD=", "tlsmlkem=1"} {
		if strings.Contains(env, value) {
			t.Fatal("sensitive environment leaked")
		}
	}
	if !strings.Contains(env, "tlsmlkem=0") {
		t.Fatal("TLS compatibility flag lost")
	}
}
func TestSystemdUnitsKeepSchedulingAndPauseSemantics(t *testing.T) {
	texts := unitTexts("/app with space/monitor", "/data with space", 7200)
	if !strings.Contains(texts[monitorUnit], "Type=oneshot") || !strings.Contains(texts[monitorUnit], `"/app with space/monitor"`) {
		t.Fatal(texts[monitorUnit])
	}
	if !strings.Contains(texts[timerUnit], "OnUnitInactiveSec=7200s") {
		t.Fatal(texts[timerUnit])
	}
	if !strings.Contains(texts[vpnUnit], "RestartPreventExitStatus=3") {
		t.Fatal(texts[vpnUnit])
	}
}
func TestUpdateVerificationRollbackAndBackup(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "monitor")
	old := []byte("#!/bin/sh\necho 0.2.0\n")
	newBinary := []byte("#!/bin/sh\necho 0.3.0\n")
	os.WriteFile(p, old, 0700)
	paused, resumed := 0, 0
	pause := func() (func() error, error) { paused++; return func() error { resumed++; return nil }, nil }
	if e := installUpdate(context.Background(), p, []byte("broken"), "0.3.0", pause); e == nil {
		t.Fatal("invalid executable installed")
	}
	if paused != 0 {
		t.Fatal("services paused before verification")
	}
	if e := installUpdate(context.Background(), p, newBinary, "v0.3.0", pause); e != nil {
		t.Fatal(e)
	}
	b, _ := os.ReadFile(p + ".previous")
	if string(b) != string(old) || paused != 1 || resumed != 1 {
		t.Fatal("backup or lifecycle failed")
	}
	os.WriteFile(p, old, 0700)
	resumeCalls := 0
	failingPause := func() (func() error, error) {
		return func() error {
			resumeCalls++
			if resumeCalls == 1 {
				return errors.New("failure")
			}
			return nil
		}, nil
	}
	if e := installUpdate(context.Background(), p, newBinary, "0.3.0", failingPause); e == nil {
		t.Fatal("recovery failure hidden")
	}
	b, _ = os.ReadFile(p)
	if string(b) != string(old) {
		t.Fatal("old binary not restored")
	}
	paths, _ := filepath.Glob(filepath.Join(dir, ".monitor-update-*"))
	if len(paths) > 0 {
		t.Fatal("temporary files retained")
	}
}
func TestVersionOrdering(t *testing.T) {
	for _, pair := range [][2]string{{"0.2.0", "v0.3.0"}, {"0.3.0-dev", "v0.3.0"}} {
		if !newer(pair[0], pair[1]) {
			t.Fatal(pair)
		}
	}
	for _, pair := range [][2]string{{"0.3.0", "v0.3.0"}, {"1.0.0", "v0.9.0"}, {"invalid", "v0.3.0"}} {
		if newer(pair[0], pair[1]) {
			t.Fatal(pair)
		}
	}
}

func TestExplicitInvalidIntervalAndPort(t *testing.T) {
	for _, args := range [][]string{{"--interval", "0"}, {"vpn", "--port", "0"}} {
		o, e := parseOptions(args)
		if e != nil {
			t.Fatal(e)
		}
		c := DefaultConfig()
		if o.intervalSet {
			c.Interval = o.interval
		}
		if o.portSet {
			c.Port = o.port
		}
		if c.Validate() == nil {
			t.Fatal("explicit zero was ignored")
		}
	}
}

func TestCoreShutdownWithUnconsumedOutput(t *testing.T) {
	if os.Getenv("SEU_FAKE_CORE_OUTPUT") == "1" {
		for {
			os.Stdout.Write(make([]byte, 65536))
		}
	}
	binary, e := os.Executable()
	if e != nil {
		t.Fatal(e)
	}
	cmd := exec.Command(binary, "-test.run=TestCoreShutdownWithUnconsumedOutput")
	cmd.Env = append(os.Environ(), "SEU_FAKE_CORE_OUTPUT=1")
	stdin, _ := cmd.StdinPipe()
	reader, writer := io.Pipe()
	cmd.Stdout = writer
	cmd.Stderr = writer
	if e = cmd.Start(); e != nil {
		t.Fatal(e)
	}
	done := make(chan error, 1)
	go func() { done <- cmd.Wait() }()
	time.Sleep(100 * time.Millisecond)
	stopped := make(chan struct{})
	go func() { stopCore(cmd, stdin, reader, writer, done); close(stopped) }()
	select {
	case <-stopped:
	case <-time.After(5 * time.Second):
		t.Fatal("subprocess cleanup deadlocked")
	}
}
