// Command spik-desktop is a Wails v2 (Go) desktop shell around the existing spik FastAPI
// backend. It reuses the EXACT same web UI: instead of embedding assets, the webview's origin
// is a Go reverse proxy pointing at a locally-spawned Python sidecar (`python -m web.main`).
// All of the React app's relative calls (/api, /video MJPEG preview, SSE progress) therefore
// "just work" and Python serves web/dist unchanged — no design changes, no second frontend.
//
// Privacy ("todo local"): the sidecar binds 127.0.0.1 on an ephemeral port and nothing is
// exposed to the network; video/audio never leave the machine (same guarantee as the CLI/web).
package main

import (
	"embed"
	"log"
	"os"

	"github.com/wailsapp/wails/v2"
	"github.com/wailsapp/wails/v2/pkg/options"
	"github.com/wailsapp/wails/v2/pkg/options/assetserver"
	"github.com/wailsapp/wails/v2/pkg/options/linux"
)

// The embedded dist holds only a placeholder (no index.html), so every request 404s in the
// asset FS and falls through to AssetServer.Handler — our reverse proxy to Python. Wails
// requires the directory to exist for the embed directive; it is intentionally unused.
//
//go:embed all:frontend/dist
var assets embed.FS

func main() {
	// WebKitGTK compositing fallbacks must be set on THIS process (the webview host) BEFORE
	// Wails initializes WebKit — not on the Python child. On some Wayland setups <video>/MJPEG
	// can flicker or blank; opt into the safe fallbacks with SPIK_WEBKIT_COMPAT=1.
	if os.Getenv("SPIK_WEBKIT_COMPAT") == "1" {
		_ = os.Setenv("WEBKIT_DISABLE_COMPOSITING_MODE", "1")
		_ = os.Setenv("WEBKIT_DISABLE_DMABUF_RENDERER", "1")
	}

	app := NewApp()

	// Launch the Python sidecar and block until it answers before opening the window, so the
	// very first webview request already has a live reverse-proxy target.
	if err := app.Start(); err != nil {
		log.Fatalf("spik: %v", err)
	}
	// Guarantee sidecar teardown even if Wails exits without firing OnShutdown.
	defer app.Stop()

	err := wails.Run(&options.App{
		Title:            "spik",
		Width:            1280,
		Height:           860,
		MinWidth:         960,
		MinHeight:        640,
		BackgroundColour: &options.RGBA{R: 11, G: 13, B: 16, A: 255}, // #0B0D10, avoids white flash
		AssetServer: &assetserver.Options{
			Assets:  assets,
			Handler: app.Handler(),
		},
		OnStartup:  app.startup,
		OnShutdown: app.shutdown,
		Linux: &linux.Options{
			ProgramName: "spik",
		},
	})
	if err != nil {
		log.Printf("spik: wails exited with error: %v", err)
	}
}
