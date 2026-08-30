// Typed, same-origin API client. Mirrors the legacy app.js `api()` helper: on a non-OK
// response it extracts the backend's `detail` field (FastAPI HTTPException) and throws it.
//
// "Everything local": every request is same-origin (relative paths). No external calls.

import type {
  AnalyzeJobResponse,
  AppConfig,
  CameraInfo,
  DevicesResponse,
  HistoryRow,
  JobEvent,
  NoiseStatus,
  Prefs,
  ProjectsResponse,
  RecordStartResponse,
  RecordStopResponse,
  VcamFilters,
  VcamStatus,
  VideosResponse,
} from './types';

async function request<T>(path: string, opts?: RequestInit): Promise<T> {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let detail = r.statusText;
    try {
      detail = ((await r.json()) as { detail?: string }).detail || detail;
    } catch {
      /* body was not JSON — keep statusText */
    }
    throw new Error(detail);
  }
  return (await r.json()) as T;
}

const qs = (params: Record<string, string | number | boolean | undefined>): string => {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== '') sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : '';
};

// ── Config + prefs ──
export const getConfig = () => request<AppConfig>('/api/config');
export const getPrefs = () => request<Prefs>('/api/prefs');
export const setPrefs = (prefs: Prefs) =>
  request<Prefs>('/api/prefs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(prefs),
  });

// ── Devices ──
export const getDevices = () => request<DevicesResponse>('/api/devices');
export const getCameraInfo = (device: string) =>
  request<CameraInfo>('/api/camera-info' + qs({ device }));

// Fire-and-forget: stop the camera preview so the backend terminates ffmpeg and releases
// the device. `keepalive` lets the request complete even when the component unmounts (tab
// switch) right after. Errors are ignored — this is best-effort teardown.
export const stopPreview = () =>
  fetch('/video/preview/stop', { method: 'POST', keepalive: true }).catch(() => {});

// ── Noise filter ──
export const getNoiseStatus = () => request<NoiseStatus>('/api/noise/status');
export const toggleNoise = (on: boolean) =>
  request<{ active: boolean }>('/api/noise/toggle' + qs({ on }), { method: 'POST' });
export const setDefaultNoise = () =>
  request<{ is_default: boolean }>('/api/noise/set-default', { method: 'POST' });

// ── Virtual camera (Speak Cam) ──
export const getVcamStatus = () => request<VcamStatus>('/api/vcam/status');
export const startVcam = (source: string, filters: VcamFilters) =>
  request<VcamStatus>('/api/vcam/start' + qs({ source, ...filters }), { method: 'POST' });
export const stopVcam = () =>
  request<{ active: boolean }>('/api/vcam/stop', { method: 'POST' });
export const setVcamFilters = (filters: VcamFilters) =>
  request<VcamStatus>('/api/vcam/set-filters' + qs({ ...filters }), { method: 'POST' });

// ── Mic test ──
export const micTestRecord = (source: string, seconds = 20) =>
  request<{ ok: boolean; seconds: number }>(
    '/api/mic-test/record' + qs({ seconds, source }),
    { method: 'POST' },
  );

// ── Projects ──
export const listProjects = () => request<ProjectsResponse>('/api/projects');
export const createProject = (slug: string) =>
  request<ProjectsResponse>('/api/projects' + qs({ slug }), { method: 'POST' });
// Delete an EMPTY project (backend rejects non-empty and 'default'). Returns the fresh list.
export const deleteProject = (slug: string) =>
  request<ProjectsResponse>('/api/projects' + qs({ slug }), { method: 'DELETE' });

// ── Recording ──
export const listVideos = (project?: string) =>
  request<VideosResponse>('/api/videos' + qs({ project }));
export const startRecord = (args: {
  audio_source: string;
  video_device: string;
  name: string;
  project: string;
}) => request<RecordStartResponse>('/api/record/start' + qs(args), { method: 'POST' });
export const stopRecord = () =>
  request<RecordStopResponse>('/api/record/stop', { method: 'POST' });

// ── Analysis ──
export const analyze = (video: string, feedback = true) =>
  request<AnalyzeJobResponse>('/api/analyze' + qs({ video, feedback }), { method: 'POST' });
export const getAnalyzeResult = (jobId: string) =>
  request<JobEvent>('/api/analyze/result/' + encodeURIComponent(jobId));

// ── History + video serving ──
export const getHistory = (limit = 50, project?: string) =>
  request<HistoryRow[]>('/api/history' + qs({ limit, project }));
export const openVideo = (path: string) =>
  request<{ opened: boolean; path: string }>('/api/video/open' + qs({ path }), {
    method: 'POST',
  });
export const videoDownloadUrl = (path: string) =>
  '/api/video' + qs({ path });
// Delete a recording from disk + its history rows (backend refuses during recording/analysis).
export const deleteVideo = (path: string) =>
  request<{ deleted: boolean; path: string; sessions_removed: number }>(
    '/api/video' + qs({ path }),
    { method: 'DELETE' },
  );
