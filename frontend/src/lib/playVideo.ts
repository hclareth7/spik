import { openVideo, videoDownloadUrl } from '../api/client';
import type { Mode } from '../api/types';

// Result of a play attempt. `opened` is true only in local mode when the backend opened the
// file in the system player; `downloaded` is true when the browser triggered a download.
// Callers translate their own status text from this (the helper stays i18n-free).
export type PlayResult =
  | { opened: true }
  | { downloaded: true }
  | { error: string };

// Open a recording the same way the Feedback tab does — the single source of truth for
// "play this video" across Record and Feedback:
//   local  → ask the backend to open it in the system player (xdg-open; host-only).
//   other  → download it (no host desktop to open it on).
// `.mkv` (Matroska) recordings are not reliably playable inline in a browser <video>, so
// there is deliberately no in-page player path here.
export async function playVideo(path: string, mode: Mode): Promise<PlayResult> {
  if (!path) return { error: 'no path' };
  if (mode === 'local') {
    try {
      await openVideo(path);
      return { opened: true };
    } catch (e) {
      return { error: (e as Error).message };
    }
  }
  const a = document.createElement('a');
  a.href = videoDownloadUrl(path);
  a.download = path.split('/').pop() ?? 'recording';
  document.body.appendChild(a);
  a.click();
  a.remove();
  return { downloaded: true };
}
