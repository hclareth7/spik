package main

import (
	"context"
	"fmt"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"syscall"
	"time"
)

// App supervises the Python FastAPI sidecar and reverse-proxies the webview to it.
//
// Lifecycle: Start() (before wails.Run) picks a free loopback port, spawns the sidecar and
// blocks until it is healthy; Handler() serves every webview request through the proxy;
// shutdown()/Stop() terminate the sidecar (SIGTERM then SIGKILL) when the window closes.
type App struct {
	ctx     context.Context
	cmd     *exec.Cmd
	exited  chan struct{} // closed by the goroutine that owns cmd.Wait()
	proxy   *httputil.ReverseProxy
	baseURL string
}

// NewApp returns an un-started supervisor.
func NewApp() *App { return &App{} }

// Start reserves a free loopback port, launches the Python sidecar with SPIK_MODE=local /
// SPIK_HOST=127.0.0.1 / SPIK_PORT=<free>, builds the reverse proxy, and blocks until the
// backend answers GET /api/config (or the sidecar dies / the timeout elapses).
func (a *App) Start() error {
	port, err := freePort()
	if err != nil {
		return fmt.Errorf("could not reserve a local port: %w", err)
	}
	a.baseURL = fmt.Sprintf("http://127.0.0.1:%d", port)

	root, err := projectRoot()
	if err != nil {
		return err
	}
	python := resolvePython(root)

	cmd := exec.Command(python, "-m", "web.main")
	cmd.Dir = root
	cmd.Env = append(os.Environ(),
		"SPIK_MODE=local",
		"SPIK_HOST=127.0.0.1",
		fmt.Sprintf("SPIK_PORT=%d", port),
	)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	// Own process group so shutdown can signal the whole sidecar tree (uvicorn + children).
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	if err := cmd.Start(); err != nil {
		return fmt.Errorf("could not start the Python sidecar (%s): %w", python, err)
	}
	a.cmd = cmd
	a.exited = make(chan struct{})
	go func() {
		_ = cmd.Wait() // single owner of Wait; shutdown only signals + waits on a.exited
		close(a.exited)
	}()

	target, _ := url.Parse(a.baseURL)
	proxy := httputil.NewSingleHostReverseProxy(target)
	// FlushInterval=-1 flushes every write immediately so MJPEG (multipart/x-mixed-replace)
	// camera preview and SSE analysis progress stream to the webview without buffering.
	proxy.FlushInterval = -1
	a.proxy = proxy

	return a.waitHealthy(60 * time.Second)
}

// waitHealthy polls GET /api/config until it returns 200, the sidecar exits, or timeout.
func (a *App) waitHealthy(timeout time.Duration) error {
	deadline := time.After(timeout)
	tick := time.NewTicker(300 * time.Millisecond)
	defer tick.Stop()
	client := &http.Client{Timeout: 2 * time.Second}
	healthURL := a.baseURL + "/api/config"
	for {
		if resp, err := client.Get(healthURL); err == nil {
			_ = resp.Body.Close()
			if resp.StatusCode == http.StatusOK {
				return nil
			}
		}
		select {
		case <-a.exited:
			return fmt.Errorf("the Python sidecar exited before becoming healthy (check its logs above)")
		case <-deadline:
			return fmt.Errorf("the Python sidecar did not become healthy within %s", timeout)
		case <-tick.C:
		}
	}
}

// Handler serves every webview request through the reverse proxy to the Python sidecar.
func (a *App) Handler() http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if a.proxy == nil {
			http.Error(w, "spik backend not ready", http.StatusServiceUnavailable)
			return
		}
		a.proxy.ServeHTTP(w, r)
	})
}

// startup captures the Wails runtime context (bound lifecycle hook).
func (a *App) startup(ctx context.Context) { a.ctx = ctx }

// shutdown is the Wails OnShutdown hook; it terminates the sidecar.
func (a *App) shutdown(_ context.Context) { a.Stop() }

// Stop terminates the sidecar: SIGTERM its process group, then SIGKILL if it lingers.
// Idempotent — safe to call from both OnShutdown and the main() defer.
func (a *App) Stop() {
	if a.cmd == nil || a.cmd.Process == nil {
		return
	}
	select {
	case <-a.exited: // already gone
		return
	default:
	}
	pgid := -a.cmd.Process.Pid // negative PID => the whole process group
	_ = syscall.Kill(pgid, syscall.SIGTERM)
	select {
	case <-a.exited:
	case <-time.After(5 * time.Second):
		_ = syscall.Kill(pgid, syscall.SIGKILL)
		<-a.exited
	}
}

// freePort reserves an ephemeral loopback TCP port and returns it (closed immediately; the
// small race until the sidecar binds it is acceptable for local single-user use).
func freePort() (int, error) {
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return 0, err
	}
	defer l.Close()
	return l.Addr().(*net.TCPAddr).Port, nil
}

// projectRoot locates the spik repo root (the dir containing web/main.py). Honors
// SPIK_PROJECT_ROOT, else walks up from the executable dir and the working dir.
func projectRoot() (string, error) {
	if r := os.Getenv("SPIK_PROJECT_ROOT"); r != "" {
		return r, nil
	}
	var starts []string
	if exe, err := os.Executable(); err == nil {
		starts = append(starts, filepath.Dir(exe))
	}
	if wd, err := os.Getwd(); err == nil {
		starts = append(starts, wd)
	}
	for _, start := range starts {
		dir := start
		for {
			if fileExists(filepath.Join(dir, "web", "main.py")) {
				return dir, nil
			}
			parent := filepath.Dir(dir)
			if parent == dir {
				break
			}
			dir = parent
		}
	}
	return "", fmt.Errorf("could not locate the spik project root (web/main.py); set SPIK_PROJECT_ROOT")
}

// resolvePython picks the interpreter for the sidecar: SPIK_PYTHON, else the project's
// .venv/bin/python, else python3 on PATH.
func resolvePython(root string) string {
	if p := os.Getenv("SPIK_PYTHON"); p != "" {
		return p
	}
	venv := filepath.Join(root, ".venv", "bin", "python")
	if fileExists(venv) {
		return venv
	}
	return "python3"
}

func fileExists(path string) bool {
	info, err := os.Stat(path)
	return err == nil && !info.IsDir()
}
