import { useEffect, useRef, useState } from 'react';
import { Trans, useTranslation } from 'react-i18next';

import { createProject, startRecord, stopRecord } from '../../api/client';
import { GlassButton } from '../../components/GlassButton';
import { Modal } from '../../components/Modal';
import { Picker } from '../../components/Picker';
import { WidgetCard } from '../../components/WidgetCard';
import type { Mode } from '../../api/types';
import { fmtTime, slugify, tsStamp } from '../../lib/format';
import { playVideo } from '../../lib/playVideo';
import { useApp } from '../../state/AppContext';

// Record tab: project picker + new-project, optional name (+ unique timestamp), a LIVE
// preview while recording (served from the recording ffmpeg's fan-out — the camera is never
// opened twice), an MM:SS timer, start/stop wired to /api/record/*, and — once a clip is
// saved — Preview (opens it like Feedback) and Analyze (hands it to the Feedback tab).
export function RecordTab({
  mode,
  onRequestAnalyze,
}: {
  mode: Mode;
  onRequestAnalyze: (path: string) => void;
}) {
  const { t } = useTranslation();
  const { camera, mic, projects, refreshProjects } = useApp();

  const [project, setProject] = useState('');
  const [name, setName] = useState('');
  const [recording, setRecording] = useState(false);
  const [timer, setTimer] = useState('00:00');
  const [status, setStatus] = useState<{ text: string; cls: string }>({ text: '', cls: '' });
  // Live-preview <img> src while recording, and the path of the last saved clip (drives the
  // Preview/Analyze buttons). `previewSrc` is cleared on stop so the fan-out <img> disconnects.
  const [previewSrc, setPreviewSrc] = useState('');
  const [lastPath, setLastPath] = useState<string | null>(null);
  // New-project modal (replaces window.prompt): open flag, the typed name, and an inline error.
  const [newProjOpen, setNewProjOpen] = useState(false);
  const [newProjName, setNewProjName] = useState('');
  const [newProjErr, setNewProjErr] = useState('');
  const [creating, setCreating] = useState(false);

  const timerId = useRef<ReturnType<typeof setInterval> | null>(null);
  // Mirrors `recording` for reads inside async callbacks (the <img> onError retry) without
  // making them depend on a stale closure.
  const recordingRef = useRef(false);
  const retryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Default the project picker to the first available project once loaded.
  useEffect(() => {
    if (!project && projects.length) setProject(projects[0].slug);
  }, [projects, project]);

  // Clean up timers if the tab unmounts mid-recording.
  useEffect(() => () => {
    if (timerId.current) clearInterval(timerId.current);
    if (retryTimer.current) clearTimeout(retryTimer.current);
  }, []);

  // Reconnect the fan-out <img> if its stream drops while we are still recording (a proxy
  // hiccup or a transient reset). A fresh timestamp forces a new GET; guarded by
  // recordingRef so it never retries once recording has stopped.
  const onPreviewError = () => {
    if (!recordingRef.current) return;
    if (retryTimer.current) clearTimeout(retryTimer.current);
    const device = camera || '/dev/video4';
    retryTimer.current = setTimeout(() => {
      if (recordingRef.current) {
        setPreviewSrc(`/video/preview.mjpeg?device=${encodeURIComponent(device)}&t=${Date.now()}`);
      }
    }, 800);
  };

  const openNewProject = () => {
    setNewProjName('');
    setNewProjErr('');
    setNewProjOpen(true);
  };

  const submitNewProject = async () => {
    const slug = slugify(newProjName);
    if (!slug) {
      setNewProjErr(t('record.invalidProjectName'));
      return;
    }
    setCreating(true);
    try {
      await createProject(slug);
      await refreshProjects();
      setProject(slug);
      setNewProjOpen(false);
    } catch (e) {
      setNewProjErr((e as Error).message);
    } finally {
      setCreating(false);
    }
  };

  const onStart = async () => {
    const audio_source = mic || 'default';
    const video_device = camera || '/dev/video4';
    const proj = project || 'default';
    const typed = slugify(name || '');
    const finalName = (typed ? typed + '_' : t('record.defaultNamePrefix') + '_') + tsStamp();
    setStatus({ text: t('record.starting'), cls: '' });
    setLastPath(null); // clear any prior clip's Preview/Analyze buttons
    try {
      const r = await startRecord({
        audio_source,
        video_device,
        name: finalName,
        project: proj,
      });
      setRecording(true);
      recordingRef.current = true;
      // The recording ffmpeg tees a preview branch that the server drains into a fan-out
      // buffer; point the <img> at it. A browser (dis)connect never touches ffmpeg's pipe,
      // so this preview can never stall or corrupt the recording.
      setPreviewSrc(`/video/preview.mjpeg?device=${encodeURIComponent(video_device)}&t=${Date.now()}`);
      const start = Date.now();
      setTimer('00:00');
      timerId.current = setInterval(() => setTimer(fmtTime((Date.now() - start) / 1000)), 500);
      setStatus({ text: t('record.recordingIn', { path: r.path }), cls: 'ok' });
    } catch (e) {
      setStatus({ text: (e as Error).message, cls: 'err' });
    }
  };

  const onStop = async () => {
    try {
      const r = await stopRecord();
      if (timerId.current) clearInterval(timerId.current);
      if (retryTimer.current) clearTimeout(retryTimer.current);
      recordingRef.current = false;
      setRecording(false);
      setPreviewSrc(''); // disconnect the fan-out <img>
      setLastPath(r.path);
      setStatus({ text: t('record.saved', { path: r.path }), cls: 'ok' });
      void refreshProjects(); // refresh per-project recording counts
    } catch (e) {
      setStatus({ text: (e as Error).message, cls: 'err' });
    }
  };

  // Open the just-recorded clip like the Feedback tab does (local: system player; else download).
  const onPreview = async () => {
    if (!lastPath) return;
    const res = await playVideo(lastPath, mode);
    if ('opened' in res) setStatus({ text: t('feedback.opening'), cls: 'ok' });
    else if ('error' in res) setStatus({ text: res.error, cls: 'err' });
  };

  return (
    <div className="grid">
      <WidgetCard wide label={t('record.card')}>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 12,
            flexWrap: 'wrap',
            marginBottom: 12,
          }}
        >
          <span className="setting-label">{t('record.project')}</span>
          <Picker value={project} onChange={setProject} style={{ minWidth: 180 }}>
            {projects.map((p) => (
              <option key={p.slug} value={p.slug}>
                {p.slug} ({p.count})
              </option>
            ))}
          </Picker>
          <GlassButton sm onClick={openNewProject}>
            {t('record.newProject')}
          </GlassButton>
          <input
            className="text-input"
            placeholder={t('record.namePlaceholder')}
            value={name}
            onChange={(e) => setName(e.target.value)}
            style={{ marginLeft: 'auto', maxWidth: 220 }}
          />
        </div>

        <div className="preview-wrap">
          {recording && previewSrc ? (
            <img src={previewSrc} alt={t('record.previewAlt')} onError={onPreviewError} />
          ) : (
            <div className="preview-placeholder">{t('record.previewPausedHint')}</div>
          )}
          <div className={'rec-dot' + (recording ? ' on' : '')}>
            <span className="blink" /> {t('record.rec')}
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 20, marginTop: 16, flexWrap: 'wrap' }}>
          <div className={'timer' + (recording ? '' : ' idle')}>{timer}</div>
          <div style={{ display: 'flex', gap: 8, marginLeft: 'auto', flexWrap: 'wrap' }}>
            {lastPath && !recording && (
              <>
                <GlassButton onClick={() => void onPreview()}>
                  {t('record.vistaPrevia')}
                </GlassButton>
                <GlassButton onClick={() => onRequestAnalyze(lastPath)}>
                  {t('record.analizar')}
                </GlassButton>
              </>
            )}
            <GlassButton variant="primary" onClick={onStart} disabled={recording}>
              {t('record.newRecording')}
            </GlassButton>
            <GlassButton variant="danger" onClick={onStop} disabled={!recording}>
              {t('record.stop')}
            </GlassButton>
          </div>
        </div>

        <div className={'status-line ' + status.cls}>{status.text}</div>
        {/* Rendered via <Trans> so the <b> markup stays a real React element and this
            string can never become an XSS sink (no dangerouslySetInnerHTML). */}
        <p className="vu-hint" style={{ marginTop: 12 }}>
          <Trans i18nKey="record.recordHint" components={{ b: <b /> }} />
        </p>
      </WidgetCard>

      <Modal
        open={newProjOpen}
        onClose={() => setNewProjOpen(false)}
        title={t('modal.newProjectTitle')}
        footer={
          <>
            <GlassButton sm onClick={() => setNewProjOpen(false)} disabled={creating}>
              {t('modal.cancel')}
            </GlassButton>
            <GlassButton sm variant="primary" onClick={() => void submitNewProject()} disabled={creating}>
              {t('modal.create')}
            </GlassButton>
          </>
        }
      >
        <input
          className="text-input"
          style={{ width: '100%' }}
          placeholder={t('modal.projectNamePlaceholder')}
          value={newProjName}
          onChange={(e) => setNewProjName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') void submitNewProject();
          }}
        />
        {newProjErr && (
          <div className="status-line err" style={{ marginTop: 10 }}>
            {newProjErr}
          </div>
        )}
      </Modal>
    </div>
  );
}
