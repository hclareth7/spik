import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  analyze,
  deleteProject,
  deleteVideo,
  getAnalyzeResult,
  getHistory,
  listVideos,
} from '../../api/client';
import type { Feedback, HistoryRow, JobEvent, Metrics, Mode, VideoItem } from '../../api/types';
import { GlassButton } from '../../components/GlassButton';
import { HistItem } from '../../components/HistItem';
import { Modal } from '../../components/Modal';
import { Picker } from '../../components/Picker';
import { ProgressBar } from '../../components/ProgressBar';
import { WidgetCard } from '../../components/WidgetCard';
import { useSSE } from '../../hooks/useSSE';
import { playVideo } from '../../lib/playVideo';
import { useApp } from '../../state/AppContext';
import { FeedbackView, type FeedbackData } from './FeedbackView';

// Defensive parse of the metrics_json column. Returns a typed Metrics only when the
// column is present, valid JSON, and carries the fields the feedback card relies on;
// otherwise null so callers fall back to the graceful "no metrics" state instead of
// rendering a blank/broken card.
function parseMetrics(json: string | null): Metrics | null {
  if (!json) return null;
  try {
    const parsed = JSON.parse(json) as unknown;
    if (
      parsed !== null &&
      typeof parsed === 'object' &&
      typeof (parsed as Metrics).language === 'string' &&
      typeof (parsed as Metrics).duration_s === 'number'
    ) {
      return parsed as Metrics;
    }
  } catch {
    /* malformed JSON — treat as no metrics */
  }
  return null;
}

// Defensive parse of the feedback_json column. null/empty/malformed => no Claude
// feedback (the card renders metrics-only via FeedbackView), never a broken card.
function parseFeedback(json: string | null): Feedback | null {
  if (!json) return null;
  try {
    const parsed = JSON.parse(json) as unknown;
    if (parsed !== null && typeof parsed === 'object') {
      return parsed as Feedback;
    }
  } catch {
    /* malformed JSON — treat as no feedback */
  }
  return null;
}

// Feedback tab: pick a recording, analyze it (SSE progress + poll fallback), show the
// full feedback, and browse history grouped by project with play/download.
// `pendingAnalyze` is a filesystem path handed over from the Record tab: when set, this tab
// selects that recording and kicks off its analysis automatically, then calls
// `onPendingConsumed` so it fires exactly once.
export function FeedbackTab({
  mode,
  pendingAnalyze,
  onPendingConsumed,
}: {
  mode: Mode;
  pendingAnalyze?: string | null;
  onPendingConsumed?: () => void;
}) {
  const { t, i18n } = useTranslation();
  const { projects, refreshProjects } = useApp();
  const sse = useSSE<JobEvent>();

  // Delete affordances only make sense where the files live (local/appliance host).
  const canDelete = mode !== 'server';

  const [videos, setVideos] = useState<VideoItem[]>([]);
  const [video, setVideo] = useState('');

  const [analyzing, setAnalyzing] = useState(false);
  const [progress, setProgress] = useState<{ show: boolean; stage: string; pct: number }>({
    show: false,
    stage: '',
    pct: 0,
  });
  const [status, setStatus] = useState<{ text: string; cls: string; spin?: boolean }>({
    text: '',
    cls: '',
  });
  const [result, setResult] = useState<FeedbackData | null>(null);

  const [histRows, setHistRows] = useState<HistoryRow[]>([]);
  const [histProject, setHistProject] = useState('');
  const [histMsg, setHistMsg] = useState(t('feedback.noSessions'));

  // Pending delete drives the confirm modal (null = closed). A discriminated union so the
  // modal renders the right copy and confirm() calls the right endpoint.
  type PendingDelete =
    | { kind: 'video'; path: string; name: string }
    | { kind: 'project'; slug: string };
  const [pendingDelete, setPendingDelete] = useState<PendingDelete | null>(null);
  const [deleting, setDeleting] = useState(false);

  const feedbackRef = useRef<HTMLDivElement>(null);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const progressHideTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Records the last Record-tab path we already kicked off, so the handoff analysis runs
  // exactly ONCE per distinct clip. Without this the effect below re-runs — React 18
  // StrictMode double-invokes effects in dev, and its own onAnalyze/loadVideos identities
  // change when it calls setVideo — firing analyze() twice; the backend rejects the second
  // with "Ya hay un análisis en curso", which surfaced even on the first analysis.
  const handedOffRef = useRef<string | null>(null);
  // True from the moment an analysis is requested until it terminates (done/error). Guards
  // against concurrent analyze() calls that the async `analyzing` state can't catch in time.
  const inFlightRef = useRef(false);

  // Human-readable stage label (STAGE_LABELS in app.js), with graceful fallback.
  const stageLabel = useCallback(
    (stage: string) => {
      const key = 'stage.' + stage;
      if (i18n.exists(key)) return t(key);
      return stage || t('stage.processing');
    },
    [i18n, t],
  );

  const setProgressStage = useCallback(
    (stage: string, pct: number) => setProgress({ show: true, stage, pct }),
    [],
  );

  const loadVideos = useCallback(async () => {
    try {
      const { videos: vs } = await listVideos();
      setVideos(vs);
      setVideo((prev) => (prev && vs.some((v) => v.path === prev) ? prev : vs[0]?.path ?? ''));
    } catch (e) {
      setStatus({ text: (e as Error).message, cls: 'err' });
    }
  }, []);

  const loadHistory = useCallback(
    async (project: string) => {
      try {
        const rows = await getHistory(50, project || undefined);
        setHistRows(rows);
        if (!rows.length) setHistMsg(t('feedback.noAnalyzed'));
      } catch (e) {
        setHistRows([]);
        setHistMsg((e as Error).message);
      }
    },
    [t],
  );

  // On mount: load recordings + history. Projects come from the shared context.
  useEffect(() => {
    void loadVideos();
    void loadHistory('');
  }, [loadVideos, loadHistory]);

  // Clean up timers on unmount (SSE is torn down by useSSE itself).
  useEffect(
    () => () => {
      if (pollTimer.current) clearTimeout(pollTimer.current);
      if (progressHideTimer.current) clearTimeout(progressHideTimer.current);
    },
    [],
  );

  const finishDone = useCallback(
    (ev: JobEvent) => {
      inFlightRef.current = false;
      setAnalyzing(false);
      setProgressStage('save', 100);
      if (ev.result) {
        setResult({
          metrics: ev.result.metrics,
          feedback: ev.result.feedback,
          feedback_error: ev.result.feedback_error,
        });
        setStatus({ text: t('feedback.done', { id: ev.result.session_id }), cls: 'ok' });
      }
      progressHideTimer.current = setTimeout(
        () => setProgress((p) => ({ ...p, show: false })),
        1500,
      );
      void loadHistory(histProject);
    },
    [setProgressStage, t, loadHistory, histProject],
  );

  const finishError = useCallback(
    (ev: JobEvent) => {
      inFlightRef.current = false;
      setAnalyzing(false);
      setStatus({
        text: t('common.error', { msg: ev.error || t('feedback.analyzeFailed') }),
        cls: 'err',
      });
      setProgress((p) => ({ ...p, show: false }));
    },
    [t],
  );

  // Polling fallback if the SSE stream drops (proxy, reconnect) — mirrors pollResult().
  const pollResult = useCallback(
    async (jobId: string) => {
      try {
        const d = await getAnalyzeResult(jobId);
        if (d.status === 'running') {
          setProgressStage(d.stage, d.pct);
          pollTimer.current = setTimeout(() => void pollResult(jobId), 2000);
          return;
        }
        if (d.status === 'done') finishDone(d);
        else finishError(d);
      } catch (e) {
        inFlightRef.current = false;
        setAnalyzing(false);
        setStatus({ text: (e as Error).message, cls: 'err' });
      }
    },
    [setProgressStage, finishDone, finishError],
  );

  // `path` overrides the selected file (used by the Record-tab handoff, which analyzes the
  // just-recorded clip before the picker's list has necessarily caught up). The Analyze
  // button calls onAnalyze() with no argument and falls back to the selected `video`.
  const onAnalyze = useCallback(
    async (path?: string) => {
    const target = path ?? video;
    if (!target) {
      setStatus({ text: t('feedback.noVideoSelected'), cls: '' });
      return;
    }
    // In-flight guard: `analyzing` is async React state, so two calls in the same tick (the
    // handoff effect + a stray re-run, or a fast double-click) would both pass a state check.
    // This ref flips synchronously, so the second call is dropped before it reaches analyze().
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    sse.close();
    setAnalyzing(true);
    setStatus({ text: t('feedback.queuing'), cls: '', spin: true });
    setProgressStage('extract', 0);
    try {
      const { job_id } = await analyze(target);
      setStatus({ text: t('feedback.analyzing'), cls: '', spin: true });
      sse.open('/api/analyze/events/' + job_id, {
        onMessage: (d) => {
          if (d.status === 'running') {
            setProgressStage(d.stage, d.pct);
            return;
          }
          sse.close();
          if (d.status === 'done') finishDone(d);
          else finishError(d);
        },
        onError: () => {
          sse.close();
          void pollResult(job_id);
        },
      });
    } catch (e) {
      inFlightRef.current = false;
      setAnalyzing(false);
      setStatus({ text: (e as Error).message, cls: 'err' });
      setProgress((p) => ({ ...p, show: false }));
    }
  },
    [video, sse, t, setProgressStage, finishDone, finishError, pollResult],
  );

  // Cross-tab handoff: when the Record tab requests analysis of a fresh clip, refresh the
  // recordings list, select it, start the analysis, and mark the request consumed so it runs
  // exactly once (never re-fires when the user returns to this tab).
  useEffect(() => {
    if (!pendingAnalyze) return;
    // Guard: run once per distinct path. Survives StrictMode's dev double-fire and any
    // dependency-identity churn (see handedOffRef). Set BEFORE the async work so a second
    // synchronous invocation is short-circuited immediately.
    if (handedOffRef.current === pendingAnalyze) return;
    handedOffRef.current = pendingAnalyze;
    void (async () => {
      await loadVideos();
      setVideo(pendingAnalyze);
      void onAnalyze(pendingAnalyze);
      onPendingConsumed?.();
    })();
  }, [pendingAnalyze, loadVideos, onAnalyze, onPendingConsumed]);

  // View saved feedback for a history row. The JSON columns arrive as strings and can be
  // null; parse them defensively so a missing/broken metrics blob shows the same "no saved
  // metrics" state (not a blank card) and missing feedback falls back to metrics-only.
  const showHistFeedback = (row: HistoryRow) => {
    const metrics = parseMetrics(row.metrics_json);
    if (!metrics) {
      setStatus({ text: t('feedback.noSavedMetrics'), cls: '' });
      return;
    }
    const feedback = parseFeedback(row.feedback_json);
    setResult({ metrics, feedback, feedback_error: null });
    // Defer scroll until the card has rendered.
    setTimeout(
      () => feedbackRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }),
      0,
    );
  };

  // Play: local opens the file in the system player (xdg-open on the backend); other
  // modes download it (no host desktop). Shared with the Record tab via playVideo().
  const play = async (path: string) => {
    const r = await playVideo(path, mode);
    if ('opened' in r) setStatus({ text: t('feedback.opening'), cls: 'ok' });
    else if ('error' in r) setStatus({ text: r.error, cls: 'err' });
  };

  const onHistProjectChange = (p: string) => {
    setHistProject(p);
    void loadHistory(p);
  };

  // Execute the pending delete (video or empty project), then refresh the affected lists.
  // Backend errors (recording/analysis in progress, non-empty project, 'default') surface
  // in the status line via their `detail`.
  const confirmDelete = async () => {
    if (!pendingDelete) return;
    setDeleting(true);
    try {
      if (pendingDelete.kind === 'video') {
        await deleteVideo(pendingDelete.path);
        setStatus({ text: t('delete.videoDone'), cls: 'ok' });
      } else {
        await deleteProject(pendingDelete.slug);
        setStatus({ text: t('delete.projectDone'), cls: 'ok' });
        // If we were filtering by the just-deleted project, fall back to "all".
        if (histProject === pendingDelete.slug) setHistProject('');
      }
      setPendingDelete(null);
      await loadVideos();
      await loadHistory(pendingDelete.kind === 'project' && histProject === pendingDelete.slug ? '' : histProject);
      void refreshProjects();
    } catch (e) {
      setPendingDelete(null);
      setStatus({ text: (e as Error).message, cls: 'err' });
    } finally {
      setDeleting(false);
    }
  };

  const selectedVideoName = videos.find((v) => v.path === video)?.name ?? video;
  // Project delete is enabled only for a specific, non-default project selection.
  const canDeleteProject = canDelete && !!histProject && histProject !== 'default';

  // Group history rows by project (sorted), keeping original indices for actions.
  const grouped = useMemo(() => {
    const groups: Record<string, HistoryRow[]> = {};
    histRows.forEach((r) => {
      const key = r.project || 'default';
      (groups[key] = groups[key] || []).push(r);
    });
    return Object.keys(groups)
      .sort()
      .map((proj) => ({ proj, rows: groups[proj] }));
  }, [histRows]);

  return (
    <div className="grid">
      <WidgetCard wide label={t('feedback.analyzeCard')}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <span className="setting-label">{t('feedback.file')}</span>
          <Picker
            value={video}
            onChange={setVideo}
            style={{ maxWidth: 'none', flex: 1, minWidth: 240 }}
          >
            {videos.length ? (
              videos.map((v) => (
                <option key={v.path} value={v.path}>
                  [{v.project}] {v.name} ({v.size_mb} MB)
                </option>
              ))
            ) : (
              <option value="">{t('feedback.noVideosOption')}</option>
            )}
          </Picker>
          <GlassButton variant="primary" onClick={() => void onAnalyze()} disabled={analyzing}>
            {t('feedback.analyze')}
          </GlassButton>
          {canDelete && (
            <GlassButton
              variant="danger"
              aria-label={t('delete.videoAria')}
              title={t('delete.videoTitle')}
              disabled={!video || analyzing}
              onClick={() =>
                video && setPendingDelete({ kind: 'video', path: video, name: selectedVideoName })
              }
            >
              🗑
            </GlassButton>
          )}
        </div>
        {progress.show && (
          <ProgressBar stageLabel={stageLabel(progress.stage) || t('feedback.preparing')} pct={progress.pct} />
        )}
        <div className={'status-line ' + status.cls}>
          {status.spin && <span className="spinner" />}
          {status.text}
        </div>
      </WidgetCard>

      {result && <FeedbackView ref={feedbackRef} data={result} />}

      <WidgetCard wide>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 12,
            flexWrap: 'wrap',
            marginBottom: 8,
          }}
        >
          <div className="card-label" style={{ marginBottom: 0 }}>
            {t('feedback.historyCard')}
          </div>
          <Picker
            value={histProject}
            onChange={onHistProjectChange}
            style={{ marginLeft: 'auto', minWidth: 180 }}
          >
            <option value="">{t('feedback.allProjects')}</option>
            {projects.map((p) => (
              <option key={p.slug} value={p.slug}>
                {p.slug} ({p.count})
              </option>
            ))}
          </Picker>
          {canDelete && (
            <GlassButton
              sm
              variant="danger"
              aria-label={t('delete.projectAria')}
              title={t('delete.projectTitle')}
              disabled={!canDeleteProject}
              onClick={() =>
                canDeleteProject && setPendingDelete({ kind: 'project', slug: histProject })
              }
            >
              🗑
            </GlassButton>
          )}
        </div>
        <div>
          {histRows.length ? (
            grouped.map(({ proj, rows }) => (
              <div className="hist-group" key={proj}>
                <div className="hist-group-head">{proj}</div>
                {rows.map((row, i) => (
                  <HistItem
                    key={i}
                    row={row}
                    onShowFeedback={() => showHistFeedback(row)}
                    onPlay={() => void play(row.video_path)}
                    onDelete={
                      canDelete
                        ? () =>
                            setPendingDelete({
                              kind: 'video',
                              path: row.video_path,
                              name: (row.video_path || '').split('/').pop() || row.video_path,
                            })
                        : undefined
                    }
                  />
                ))}
              </div>
            ))
          ) : (
            <p className="muted">{histMsg}</p>
          )}
        </div>
      </WidgetCard>

      <Modal
        open={pendingDelete !== null}
        onClose={() => setPendingDelete(null)}
        title={pendingDelete?.kind === 'project' ? t('delete.projectTitle') : t('delete.videoTitle')}
        footer={
          <>
            <GlassButton sm onClick={() => setPendingDelete(null)} disabled={deleting}>
              {t('delete.cancel')}
            </GlassButton>
            <GlassButton sm variant="danger" onClick={() => void confirmDelete()} disabled={deleting}>
              {deleting ? t('delete.deleting') : t('delete.confirm')}
            </GlassButton>
          </>
        }
      >
        {pendingDelete?.kind === 'project'
          ? t('delete.projectConfirm', { slug: pendingDelete.slug })
          : pendingDelete
            ? t('delete.videoConfirm', { name: pendingDelete.name })
            : ''}
      </Modal>
    </div>
  );
}
