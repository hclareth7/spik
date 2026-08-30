// Small pure helpers ported 1:1 from web/static/app.js.

// VU color mapping: target -12..-6 dBFS = green; -24..-12 = amber; > -6 = red (clipping).
// Returns a CSS var() token (resolved against the design tokens).
export function colorForDb(db: number): string {
  if (db > -6) return 'var(--danger)';
  if (db >= -12) return 'var(--success)';
  if (db >= -24) return 'var(--warning)';
  return 'var(--accent-solid)';
}

// Map -60..0 dBFS to a 0..100% meter width.
export function dbToPct(dbfs: number): number {
  return Math.max(0, Math.min(100, ((dbfs + 60) / 60) * 100));
}

// Sanitize a raw project/name string to the backend's allowed charset
// (letters, digits, - and _), trimming leading/trailing dashes. Matches app.js.
export function slugify(raw: string): string {
  return raw
    .trim()
    .replace(/[^A-Za-z0-9_-]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

// mm:ss from seconds.
export function fmtTime(s: number): string {
  const m = Math.floor(s / 60);
  const ss = Math.floor(s % 60);
  return String(m).padStart(2, '0') + ':' + String(ss).padStart(2, '0');
}

// Local timestamp YYYYMMDD-HHMMSS (only filename-safe characters).
export function tsStamp(): string {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(
    d.getMinutes(),
  )}${p(d.getSeconds())}`;
}
