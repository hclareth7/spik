package main

import (
	"bytes"
	"io"
	"net/http"
	"strconv"
	"strings"
)

// injectMjpegShim rewrites HTML responses to inject a small client-side compatibility shim
// that renders the camera's MJPEG preview inside the Wails webview.
//
// Why: WebKitGTK (the webview engine Wails uses on Linux) does NOT render
// multipart/x-mixed-replace streams in an <img>, so the camera preview shows solid black even
// though the FastAPI backend serves perfectly valid JPEG frames (verified: the served bytes
// decode to normal-brightness images). Regular browsers render multipart natively, which is why
// the web build works.
//
// The shim reads the SAME /video/preview.mjpeg stream via fetch()+ReadableStream, splits each
// JPEG frame (SOI 0xFFD8 … EOI 0xFFD9), and paints it into the existing <img> as a blob URL —
// which WebKit renders fine. The React frontend and the FastAPI backend are left untouched; this
// desktop-only workaround lives entirely in the reverse-proxy layer.
func injectMjpegShim(resp *http.Response) error {
	if !strings.HasPrefix(strings.ToLower(resp.Header.Get("Content-Type")), "text/html") {
		return nil
	}
	// The backend does not compress index.html; if some encoding is present, skip rather than
	// corrupt the body.
	if enc := resp.Header.Get("Content-Encoding"); enc != "" && !strings.EqualFold(enc, "identity") {
		return nil
	}
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return err
	}
	_ = resp.Body.Close()

	tag := []byte("<script>" + mjpegShimJS + "</script>")
	var out []byte
	if i := bytes.LastIndex(body, []byte("</body>")); i != -1 {
		out = append(out, body[:i]...)
		out = append(out, tag...)
		out = append(out, body[i:]...)
	} else {
		out = append(append([]byte{}, body...), tag...)
	}

	resp.Body = io.NopCloser(bytes.NewReader(out))
	resp.ContentLength = int64(len(out))
	resp.Header.Set("Content-Length", strconv.Itoa(len(out)))
	resp.Header.Del("Transfer-Encoding")
	return nil
}

// mjpegShimJS drives an <img src=".../video/preview.mjpeg"> by polling single JPEG frames
// from GET /video/snapshot.jpg (~25 fps) and painting them as blob: URLs.
//
// Why polling and not the multipart stream: WebKitGTK cannot render multipart/x-mixed-replace
// natively, AND it does not reliably deliver an unbounded fetch() body incrementally through a
// ReadableStream — so consuming /video/preview.mjpeg via fetch starves (the reader never yields
// frames) and the preview stays black. Each snapshot is a finite response, which WebKitGTK
// delivers normally. The backend serves both from the same server-drained fan-out buffer, so
// this opens the single-open camera exactly once (see web/routers/preview.py, web/state.py).
//
// Must contain no backticks (embedded in a Go raw string literal) and stay ES2017-compatible.
const mjpegShimJS = `
(function(){
  if (window.__spikMjpeg) return;
  window.__spikMjpeg = true;
  function beacon(m){ try{ fetch('/__spikdbg?m='+encodeURIComponent(m),{cache:'no-store'}).catch(function(){}); }catch(e){} }
  beacon('loaded');
  window.addEventListener('error', function(ev){ beacon('winerr '+(ev && ev.message)); });
  var BLANK='data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==';
  var FRAME_MS=30; // ~33 fps poll cadence (polls faster than the 30 fps source → low latency)
  // Horizontal flip so the self-view reads like a mirror (selfie orientation), matching
  // what webcam/video-call apps show. Applied to the <img> we drive; the recording file and
  // the raw camera feed are untouched.
  var MIRROR='scaleX(-1)';
  function isMjpeg(u){ return !!u && u.indexOf('.mjpeg') !== -1; }
  function isOurs(u){ return !!u && (u.indexOf('blob:')===0 || u.indexOf('data:')===0); }
  function sleep(ms){ return new Promise(function(r){ setTimeout(r,ms); }); }
  // Build the snapshot URL from the preview.mjpeg URL, preserving only the device param.
  function snapBase(u){
    var q=u.indexOf('?'), dev='';
    if(q!==-1){ u.substring(q+1).split('&').forEach(function(p){ if(p.indexOf('device=')===0) dev=p; }); }
    return '/video/snapshot.jpg?'+(dev?dev+'&':'')+'t=';
  }
  function stop(img){
    if(img.__ctl){ img.__ctl.stopped=true; img.__ctl=null; }
    if(img.__o){ try{URL.revokeObjectURL(img.__o);}catch(e){} img.__o=null; }
    try{ img.style.transform=''; }catch(e){}  // drop the mirror when we stop driving it
    img.__u=null;
  }
  async function paint(img,blob){
    // Double-buffer: decode the frame off-screen in a reused, detached Image BEFORE swapping
    // the visible <img>. Assigning an already-decoded (cached) blob URL lets WebKitGTK swap
    // with no blank frame; assigning an undecoded URL blanks the element to the black
    // .preview-wrap background until it loads, which at ~25/s reads as fast flicker.
    var url=URL.createObjectURL(blob);
    var probe=img.__probe||(img.__probe=new Image());
    probe.src=url;
    if(probe.decode){ try{ await probe.decode(); }catch(e){ try{URL.revokeObjectURL(url);}catch(e2){} return; } }
    var prev=img.__o;
    img.__o=url; img.setAttribute('src',url);
    if(prev){ try{URL.revokeObjectURL(prev);}catch(e){} }
  }
  async function poll(img,base,ctl){
    while(!ctl.stopped){
      var t0=Date.now();
      try{
        var resp=await fetch(base+Date.now(),{cache:'no-store'});
        if(resp.ok){
          var blob=await resp.blob();
          if(ctl.stopped) break;
          await paint(img,blob);
        }
      }catch(e){}
      var dt=Date.now()-t0;
      if(dt<FRAME_MS) await sleep(FRAME_MS-dt);
    }
  }
  function adopt(img){
    var u=img.getAttribute('src');
    if(!isMjpeg(u)) return;
    if(img.__u===u && img.__ctl && !img.__ctl.stopped){
      // React re-applied the .mjpeg src on an <img> we already drive (e.g. the Record tab's
      // onError remount). Re-assert our last painted frame instead of restarting the poll,
      // so the native loader never shows a black frame between polls.
      if(img.__o) img.setAttribute('src', img.__o);
      return;
    }
    stop(img); img.__u=u;
    try{ img.style.transform=MIRROR; }catch(e){}  // selfie/mirror orientation
    // Cancel the native multipart load (WebKitGTK can't render it and fires an <img> 'error',
    // which the React Record tab turns into an 800 ms reconnect loop). A blank data: URL aborts
    // the pending native load before it can error; we then paint polled JPEG frames as blob: URLs.
    img.setAttribute('src', BLANK);
    var ctl={stopped:false}; img.__ctl=ctl;
    poll(img,snapBase(u),ctl);
  }
  function scan(root){
    if(!root.querySelectorAll) return;
    var xs=root.querySelectorAll('img');
    for(var i=0;i<xs.length;i++){ if(isMjpeg(xs[i].getAttribute('src'))) adopt(xs[i]); }
  }
  new MutationObserver(function(ms){
    for(var k=0;k<ms.length;k++){
      var m=ms[k];
      if(m.type==='attributes' && m.target.tagName==='IMG'){
        var src=m.target.getAttribute('src');
        if(isMjpeg(src)) adopt(m.target);
        else if(isOurs(src)){ /* our own blank/frame write — ignore */ }
        else stop(m.target);
      } else if(m.type==='childList'){
        for(var a=0;a<m.addedNodes.length;a++){ var n=m.addedNodes[a]; if(n.nodeType===1){ if(n.tagName==='IMG'&&isMjpeg(n.getAttribute('src'))) adopt(n); else scan(n); } }
        for(var b=0;b<m.removedNodes.length;b++){ var rn=m.removedNodes[b]; if(rn.nodeType===1&&rn.tagName==='IMG') stop(rn); }
      }
    }
  }).observe(document.documentElement,{subtree:true,childList:true,attributes:true,attributeFilter:['src']});
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',function(){scan(document);});
  else scan(document);
})();
`
