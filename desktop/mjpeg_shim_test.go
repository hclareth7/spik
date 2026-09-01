package main

import (
	"io"
	"net/http"
	"strconv"
	"strings"
	"testing"
)

func htmlResp(body string) *http.Response {
	r := &http.Response{
		Header: http.Header{},
		Body:   io.NopCloser(strings.NewReader(body)),
	}
	r.Header.Set("Content-Type", "text/html; charset=utf-8")
	r.Header.Set("Content-Length", strconv.Itoa(len(body)))
	return r
}

func TestInjectMjpegShim_InsertsBeforeBody(t *testing.T) {
	body := "<html><head></head><body><div id=\"root\"></div></body></html>"
	resp := htmlResp(body)
	if err := injectMjpegShim(resp); err != nil {
		t.Fatalf("injectMjpegShim: %v", err)
	}
	out, _ := io.ReadAll(resp.Body)
	got := string(out)
	if !strings.Contains(got, "__spikMjpeg") {
		t.Fatal("shim marker not injected")
	}
	if !strings.Contains(got, "<script>") || !strings.Contains(got, "</script></body>") {
		t.Fatalf("shim not placed before </body>: %q", got)
	}
	if resp.ContentLength != int64(len(out)) {
		t.Fatalf("Content-Length not updated: got %d want %d", resp.ContentLength, len(out))
	}
	if resp.Header.Get("Content-Length") != strconv.Itoa(len(out)) {
		t.Fatal("Content-Length header not updated")
	}
}

func TestInjectMjpegShim_SkipsNonHTML(t *testing.T) {
	body := "console.log('js')"
	resp := htmlResp(body)
	resp.Header.Set("Content-Type", "application/javascript")
	if err := injectMjpegShim(resp); err != nil {
		t.Fatalf("injectMjpegShim: %v", err)
	}
	out, _ := io.ReadAll(resp.Body)
	if string(out) != body {
		t.Fatalf("non-HTML body was modified: %q", string(out))
	}
}

func TestInjectMjpegShim_SkipsCompressed(t *testing.T) {
	body := "<html><body></body></html>"
	resp := htmlResp(body)
	resp.Header.Set("Content-Encoding", "gzip")
	if err := injectMjpegShim(resp); err != nil {
		t.Fatalf("injectMjpegShim: %v", err)
	}
	out, _ := io.ReadAll(resp.Body)
	if string(out) != body {
		t.Fatal("compressed body must not be modified (would corrupt it)")
	}
}

func TestInjectMjpegShim_AppendsWhenNoBodyTag(t *testing.T) {
	body := "<div>no body tag</div>"
	resp := htmlResp(body)
	if err := injectMjpegShim(resp); err != nil {
		t.Fatalf("injectMjpegShim: %v", err)
	}
	out, _ := io.ReadAll(resp.Body)
	got := string(out)
	if !strings.HasPrefix(got, body) || !strings.Contains(got, "__spikMjpeg") {
		t.Fatalf("shim not appended: %q", got)
	}
}
