// TypeScript types mirroring the backend API contract (see the plan's "Contrato de API").
// These intentionally match the JSON shapes returned by web/routers/* verbatim.

export type Mode = 'local' | 'appliance' | 'server';

export interface AppConfig {
  mode: Mode;
  noise: boolean;
  vcam: boolean;
}

export interface Prefs {
  camera?: string;
  mic?: string;
}

export interface Camera {
  device: string;
  name: string;
  // True for the "Speak Cam" v4l2loopback output: never offer it as a capture SOURCE
  // (reading it while we write to it would feed back on itself).
  virtual: boolean;
}

export interface Source {
  name: string;
  clean: boolean;
}

export interface DevicesResponse {
  cameras: Camera[];
  sources: Source[];
}

export interface CameraInfo {
  device: string;
  formats: string[];
}

export interface NoiseStatus {
  active: boolean;
  available: boolean;
  is_default: boolean;
}

// Speak Cam (virtual camera) live filter values. Ranges mirror capture_pipeline.FILTER_RANGES;
// denoise is an hqdn3d preset. These are sent as query params to /api/vcam/*.
export interface VcamFilters {
  brightness: number; // -0.3 .. 0.3   (neutral 0)
  contrast: number; //   0.5 .. 1.8   (neutral 1)
  gamma: number; //      0.5 .. 2.0   (neutral 1)
  saturation: number; // 0   .. 2.5   (neutral 1)
  sharpness: number; //  0   .. 1.5   (neutral 0)
  denoise: 'off' | 'light' | 'strong';
}

export interface VcamStatus {
  active: boolean;
  available: boolean;
  device: string;
  filters: VcamFilters | null;
}

export interface Project {
  slug: string;
  count: number;
}

export interface ProjectsResponse {
  projects: Project[];
}

export interface VideoItem {
  path: string;
  name: string;
  project: string;
  size_mb: number;
}

export interface VideosResponse {
  videos: VideoItem[];
}

export interface RecordStartResponse {
  recording: boolean;
  path: string;
}

export interface RecordStopResponse {
  recording: boolean;
  path: string;
}

export interface Metrics {
  language: string;
  duration_s: number;
  wpm: number;
  filler_count: number;
  fillers_per_min: number;
  long_pause_count: number;
  pause_ratio: number;
}

export interface Improvement {
  area: string;
  issue: string;
  suggestion: string;
}

export interface Rewrite {
  original: string;
  improved: string;
}

export interface Feedback {
  overall_score: number;
  summary: string;
  strengths: string[];
  improvements: Improvement[];
  rewrites: Rewrite[];
  next_session_goals: string[];
  input_tokens?: number;
  output_tokens?: number;
  cost_usd?: number;
  model?: string;
}

export interface AnalyzeResult {
  session_id?: number;
  transcript?: string;
  metrics: Metrics;
  feedback: Feedback | null;
  feedback_error: string | null;
}

export interface AnalyzeJobResponse {
  job_id: string;
}

export type JobStatus = 'running' | 'done' | 'error';

// SSE payload from /api/analyze/events/{id} and /api/analyze/result/{id}.
export interface JobEvent {
  status: JobStatus;
  stage: string;
  pct: number;
  result?: AnalyzeResult | null;
  error?: string | null;
}

// A single dBFS sample from /api/mic-level (SSE).
export interface MicLevelEvent {
  dbfs: number;
}

// Rows from /api/history — metrics_json / feedback_json arrive as JSON strings, and can
// be null (the backend columns are nullable). Consumers must parse them defensively.
export interface HistoryRow {
  project: string;
  created_at: string;
  video_path: string;
  overall_score: number | null;
  metrics_json: string | null;
  feedback_json: string | null;
}
