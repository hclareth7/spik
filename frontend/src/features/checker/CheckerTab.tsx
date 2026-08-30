import { useCallback, useEffect, useRef, useState } from 'react';
import { Trans, useTranslation } from 'react-i18next';

import {
  getCameraInfo,
  getNoiseStatus,
  getVcamStatus,
  micTestRecord,
  setDefaultNoise,
  setVcamFilters,
  startVcam,
  stopPreview,
  stopVcam,
  toggleNoise,
} from '../../api/client';
import type { MicLevelEvent, Mode, VcamFilters } from '../../api/types';
import { GlassButton } from '../../components/GlassButton';
import { Picker } from '../../components/Picker';
import { Slider } from '../../components/Slider';
import { Toggle } from '../../components/Toggle';
import { VuMeter } from '../../components/VuMeter';
import { WidgetCard } from '../../components/WidgetCard';
import { useSSE } from '../../hooks/useSSE';
import { colorForDb, dbToPct } from '../../lib/format';
import { useApp } from '../../state/AppContext';

// "Podcast studio" default look the Speak Cam starts with — mirror capture_pipeline.FILTER_DEFAULT.
// A polished, natural grade tuned to a typical webcam (gentle brightness/gamma lift, mild
// contrast/saturation, moderate sharpen, light denoise). Users can still dial everything to 0/1.
const DEFAULT_FILTERS: VcamFilters = {
  brightness: 0.05,
  contrast: 1.15,
  gamma: 1.06,
  saturation: 1.25,
  sharpness: 0.7,
  denoise: 'light',
};

// Checker tab: camera preview (MJPEG), camera formats, live mic VU (SSE), the
// record-and-listen mic test, and the RNNoise "Speak Clean Mic" filter card.
// The noise card only renders in local mode (host-only systemd --user); in appliance
// mode it is hidden (matches app.js, which sets #noiseCard display:none).
export function CheckerTab({ mode }: { mode: Mode }) {
  const { t } = useTranslation();
  const { cameras, sources, camera, mic, selectCamera, selectMic, refreshDevices, devicesError } =
    useApp();
  const showNoise = mode === 'local';
  const showVcam = mode === 'local';
  // Never offer the "Speak Cam" loopback as a capture SOURCE (feedback loop): it is an output.
  const captureCameras = cameras.filter((c) => !c.virtual);

  // ── Camera preview ──
  const [previewOn, setPreviewOn] = useState(false);
  const [previewSrc, setPreviewSrc] = useState('');
  const togglePreview = () => {
    if (previewOn) {
      // Remove the <img> AND tell the backend to kill ffmpeg (releases the camera). The
      // <img> removal alone is not enough: a StreamingResponse does not reliably observe
      // the client disconnect, so ffmpeg would linger and block the next recording.
      setPreviewSrc('');
      setPreviewOn(false);
      void stopPreview();
    } else {
      setPreviewSrc(`/video/preview.mjpeg?device=${encodeURIComponent(camera)}&t=${Date.now()}`);
      setPreviewOn(true);
    }
  };

  // Release the camera when leaving the Checker tab (this component unmounts) or on any
  // reload, so switching to Record/Feedback never leaves ffmpeg holding the device.
  useEffect(() => {
    return () => {
      void stopPreview();
    };
  }, []);

  // ── Camera formats ──
  const [cameraInfo, setCameraInfo] = useState<string[] | null>(null);
  const [cameraInfoMsg, setCameraInfoMsg] = useState('');
  const onCameraInfo = async () => {
    setCameraInfo(null);
    setCameraInfoMsg(t('checker.querying'));
    try {
      const { formats } = await getCameraInfo(camera);
      setCameraInfo(formats);
      setCameraInfoMsg('');
    } catch (e) {
      setCameraInfo(null);
      setCameraInfoMsg(t('common.error', { msg: (e as Error).message }));
    }
  };

  // ── VU meter (SSE) ──
  const sse = useSSE<MicLevelEvent>();
  const [vuActive, setVuActive] = useState(false);
  const [vuPct, setVuPct] = useState(0);
  const [vuColor, setVuColor] = useState('var(--accent-solid)');
  const [vuValue, setVuValue] = useState(t('checker.dbfsPlaceholder'));

  const toggleVu = () => {
    if (vuActive) {
      sse.close();
      setVuActive(false);
      setVuPct(0);
      setVuValue(t('checker.dbfsPlaceholder'));
      return;
    }
    setVuActive(true);
    sse.open('/api/mic-level?source=' + encodeURIComponent(mic), {
      onMessage: ({ dbfs }) => {
        setVuPct(dbToPct(dbfs));
        setVuColor(colorForDb(dbfs));
        setVuValue(t('checker.dbfs', { v: dbfs.toFixed(1) }));
      },
      onError: () => setVuValue(t('checker.noSignal')),
    });
  };

  // ── Mic test ──
  const [micTestBusy, setMicTestBusy] = useState(false);
  const [micTestSrc, setMicTestSrc] = useState('');
  const [micTestStatus, setMicTestStatus] = useState<{ text: string; cls: string; spin?: boolean }>(
    { text: '', cls: '' },
  );
  const onMicTest = async () => {
    if (!mic) {
      setMicTestStatus({ text: t('checker.selectMicSource'), cls: '' });
      return;
    }
    setMicTestBusy(true);
    setMicTestStatus({ text: t('checker.recording5s'), cls: '', spin: true });
    try {
      await micTestRecord(mic, 20);
      setMicTestSrc('/api/mic-test/audio?t=' + Date.now());
      setMicTestStatus({ text: t('checker.micTestDone'), cls: 'ok' });
    } catch (e) {
      setMicTestStatus({ text: t('common.error', { msg: (e as Error).message }), cls: 'err' });
    } finally {
      setMicTestBusy(false);
    }
  };

  // ── Noise filter ──
  const [noiseActive, setNoiseActive] = useState(false);
  const [noiseStatus, setNoiseStatus] = useState<{ text: string; cls: string }>({
    text: '',
    cls: '',
  });

  const refreshNoise = useCallback(async () => {
    try {
      const s = await getNoiseStatus();
      setNoiseActive(s.active);
      let msg = s.active ? t('checker.filterOn') : t('checker.filterOff');
      if (s.active && !s.available) msg += t('checker.filterNotYet');
      if (s.is_default) msg += t('checker.filterIsDefault');
      setNoiseStatus({ text: msg, cls: s.active ? 'ok' : '' });
    } catch (e) {
      setNoiseStatus({ text: (e as Error).message, cls: '' });
    }
  }, [t]);

  const onToggleNoise = async () => {
    const turnOn = !noiseActive;
    setNoiseStatus({ text: t('checker.applying'), cls: '' });
    try {
      await toggleNoise(turnOn);
      // Give PipeWire a moment to (un)load the source, then refresh both panels.
      setTimeout(() => {
        void refreshNoise();
        void refreshDevices();
      }, 800);
    } catch (e) {
      setNoiseStatus({ text: (e as Error).message, cls: 'err' });
    }
  };

  const onSetDefault = async () => {
    try {
      await setDefaultNoise();
      void refreshNoise();
    } catch (e) {
      setNoiseStatus({ text: (e as Error).message, cls: 'err' });
    }
  };

  // Initial noise status (local only). Surface a device-listing error here too, exactly
  // like app.js, which wrote "No pude listar dispositivos" into #noiseStatus.
  const noiseInit = useRef(false);
  useEffect(() => {
    if (showNoise && !noiseInit.current) {
      noiseInit.current = true;
      void refreshNoise();
    }
  }, [showNoise, refreshNoise]);
  useEffect(() => {
    if (devicesError) {
      setNoiseStatus({ text: t('checker.cannotListDevices', { msg: devicesError }), cls: '' });
    }
  }, [devicesError, t]);

  // ── Virtual camera (Speak Cam) ──
  const [vcamActive, setVcamActive] = useState(false);
  const [vcamAvailable, setVcamAvailable] = useState(false);
  const [vcamFilters, setVcamFiltersState] = useState<VcamFilters>(DEFAULT_FILTERS);
  const [vcamStatus, setVcamStatus] = useState<{ text: string; cls: string }>({ text: '', cls: '' });

  const refreshVcam = useCallback(async () => {
    try {
      const s = await getVcamStatus();
      setVcamActive(s.active);
      setVcamAvailable(s.available);
      if (s.filters) setVcamFiltersState(s.filters);
      let msg = s.active ? t('checker.vcamOn') : t('checker.vcamOff');
      if (!s.available) msg = t('checker.vcamUnavailable');
      setVcamStatus({ text: msg, cls: s.active ? 'ok' : '' });
    } catch (e) {
      setVcamStatus({ text: (e as Error).message, cls: '' });
    }
  }, [t]);

  const onToggleVcam = async () => {
    setVcamStatus({ text: t('checker.applying'), cls: '' });
    try {
      if (vcamActive) {
        await stopVcam();
        setVcamActive(false);
        setVcamStatus({ text: t('checker.vcamOff'), cls: '' });
      } else {
        await startVcam(camera, vcamFilters);
        setVcamActive(true);
        setVcamStatus({ text: t('checker.vcamOn'), cls: 'ok' });
      }
    } catch (e) {
      setVcamStatus({ text: (e as Error).message, cls: 'err' });
    }
  };

  // Apply filter changes with a debounce: each change restarts the single-owner ffmpeg
  // (~1 s), so we only push the LATEST value ~400 ms after the user stops dragging.
  const filterDebounce = useRef<ReturnType<typeof setTimeout> | null>(null);
  const onFilterChange = (patch: Partial<VcamFilters>) => {
    const next = { ...vcamFilters, ...patch };
    setVcamFiltersState(next);
    if (!vcamActive) return; // nothing running yet: values apply on next start
    if (filterDebounce.current) clearTimeout(filterDebounce.current);
    filterDebounce.current = setTimeout(() => {
      setVcamFilters(next).catch((e) =>
        setVcamStatus({ text: (e as Error).message, cls: 'err' }),
      );
    }, 400);
  };

  // Initial Speak Cam status (local only).
  const vcamInit = useRef(false);
  useEffect(() => {
    if (showVcam && !vcamInit.current) {
      vcamInit.current = true;
      void refreshVcam();
    }
  }, [showVcam, refreshVcam]);
  // Clear any pending debounce on unmount.
  useEffect(() => {
    return () => {
      if (filterDebounce.current) clearTimeout(filterDebounce.current);
    };
  }, []);

  return (
    <div className="grid">
      {/* Camera preview */}
      <WidgetCard wide label={t('checker.cameraCard')}>
        <div className="setting-row" style={{ border: 'none', paddingTop: 0 }}>
          <span className="setting-label">{t('checker.device')}</span>
          <Picker value={camera} onChange={selectCamera}>
            {captureCameras.map((c) => (
              <option key={c.device} value={c.device}>
                {c.name} ({c.device})
              </option>
            ))}
          </Picker>
        </div>
        <div className="preview-wrap" style={{ marginTop: 8 }}>
          {previewOn ? (
            <img src={previewSrc} alt={t('checker.previewAlt')} />
          ) : (
            <div className="preview-placeholder">{t('checker.previewPlaceholder')}</div>
          )}
        </div>
        <div className="flex-row" style={{ display: 'flex', gap: 8, marginTop: 12 }}>
          <GlassButton variant="primary" onClick={togglePreview}>
            {previewOn ? t('checker.stopPreview') : t('checker.startPreview')}
          </GlassButton>
          <GlassButton onClick={onCameraInfo}>{t('checker.viewFormats')}</GlassButton>
        </div>
        <div className="muted" id="cameraInfo" style={{ marginTop: 12 }}>
          {cameraInfo
            ? cameraInfo.map((f, i) => (
                <span key={i}>
                  {f}
                  <br />
                </span>
              ))
            : cameraInfoMsg}
        </div>
      </WidgetCard>

      {/* Microphone level */}
      <WidgetCard label={t('checker.micCard')}>
        <div className="setting-row" style={{ border: 'none', paddingTop: 0 }}>
          <span className="setting-label">{t('checker.source')}</span>
          <Picker value={mic} onChange={selectMic}>
            {sources.map((s) => (
              <option key={s.name} value={s.name}>
                {s.clean ? t('checker.cleanMic') : s.name}
              </option>
            ))}
          </Picker>
        </div>
        <VuMeter label={t('checker.level')} fillPct={vuPct} fillColor={vuColor} value={vuValue} />
        {/* <Trans> keeps the <b> markup as a real React element (no dangerouslySetInnerHTML). */}
        <p className="vu-hint">
          <Trans i18nKey="checker.vuHint" components={{ b: <b /> }} />
        </p>
        <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
          <GlassButton variant="primary" onClick={toggleVu}>
            {vuActive ? t('checker.stop') : t('checker.measure')}
          </GlassButton>
          <GlassButton onClick={onMicTest} disabled={micTestBusy}>
            {t('checker.recordAndListen')}
          </GlassButton>
        </div>
        {micTestSrc && (
          <div id="micTestWrap" style={{ marginTop: 12 }}>
            <audio src={micTestSrc} controls preload="auto" />
            {/* <Trans> keeps the <b> markup as a real React element (no dangerouslySetInnerHTML). */}
            <p className="vu-hint">
              <Trans i18nKey="checker.micTestHint" components={{ b: <b /> }} />
            </p>
          </div>
        )}
        <div className={'status-line ' + micTestStatus.cls}>
          {micTestStatus.spin && <span className="spinner" />}
          {micTestStatus.text}
        </div>
      </WidgetCard>

      {/* Noise filter (local only) */}
      {showNoise && (
        <WidgetCard id="noiseCard" label={t('checker.noiseCard')}>
          <div className="setting-row">
            <span className="setting-label">{t('checker.rnnoiseActive')}</span>
            <Toggle
              active={noiseActive}
              onClick={onToggleNoise}
              aria-label={t('checker.noiseToggleAria')}
            />
          </div>
          <div className="setting-row">
            <span className="setting-label">{t('checker.systemDefaultMic')}</span>
            <GlassButton sm onClick={onSetDefault}>
              {t('checker.useCleanMic')}
            </GlassButton>
          </div>
          {/* Plain text: this string carries no markup, so it needs no dangerouslySetInnerHTML. */}
          <p className="vu-hint">{t('checker.noiseHint')}</p>
          <div className={'status-line ' + noiseStatus.cls}>{noiseStatus.text}</div>
        </WidgetCard>
      )}

      {/* Virtual camera — Speak Cam (local only) */}
      {showVcam && (
        <WidgetCard id="vcamCard" wide label={t('checker.vcamCard')}>
          <div className="setting-row">
            <span className="setting-label">{t('checker.vcamActive')}</span>
            <Toggle
              active={vcamActive}
              onClick={onToggleVcam}
              aria-label={t('checker.vcamToggleAria')}
            />
          </div>
          <Slider
            label={t('checker.brightness')}
            value={vcamFilters.brightness}
            min={-0.3}
            max={0.3}
            step={0.01}
            disabled={!vcamAvailable}
            onChange={(v) => onFilterChange({ brightness: v })}
            format={(v) => v.toFixed(2)}
          />
          <Slider
            label={t('checker.contrast')}
            value={vcamFilters.contrast}
            min={0.5}
            max={1.8}
            step={0.05}
            disabled={!vcamAvailable}
            onChange={(v) => onFilterChange({ contrast: v })}
            format={(v) => v.toFixed(2)}
          />
          <Slider
            label={t('checker.gamma')}
            value={vcamFilters.gamma}
            min={0.5}
            max={2.0}
            step={0.05}
            disabled={!vcamAvailable}
            onChange={(v) => onFilterChange({ gamma: v })}
            format={(v) => v.toFixed(2)}
          />
          <Slider
            label={t('checker.saturation')}
            value={vcamFilters.saturation}
            min={0}
            max={2.5}
            step={0.05}
            disabled={!vcamAvailable}
            onChange={(v) => onFilterChange({ saturation: v })}
            format={(v) => v.toFixed(2)}
          />
          <Slider
            label={t('checker.sharpness')}
            value={vcamFilters.sharpness}
            min={0}
            max={1.5}
            step={0.05}
            disabled={!vcamAvailable}
            onChange={(v) => onFilterChange({ sharpness: v })}
            format={(v) => v.toFixed(2)}
          />
          <div className="setting-row">
            <span className="setting-label">{t('checker.denoise')}</span>
            <Picker
              value={vcamFilters.denoise}
              onChange={(v) => onFilterChange({ denoise: v as VcamFilters['denoise'] })}
            >
              <option value="off">{t('checker.denoiseOff')}</option>
              <option value="light">{t('checker.denoiseLight')}</option>
              <option value="strong">{t('checker.denoiseStrong')}</option>
            </Picker>
          </div>
          <p className="vu-hint">{t('checker.vcamHint')}</p>
          <div className={'status-line ' + vcamStatus.cls}>{vcamStatus.text}</div>
        </WidgetCard>
      )}
    </div>
  );
}
