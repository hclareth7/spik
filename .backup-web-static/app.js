// spik GUI — lógica del frontend. Sin dependencias, solo fetch + EventSource.
'use strict';

const $ = (id) => document.getElementById(id);
let APP_MODE = 'local';  // lo fija applyMode() según /api/config; decide cómo reproducir.
const api = async (path, opts) => {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = (await r.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  return r.json();
};

// ── Tabs ──
document.querySelectorAll('.tab').forEach((btn) => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach((b) => b.classList.remove('active'));
    document.querySelectorAll('.tabpanel').forEach((p) => p.classList.remove('active'));
    btn.classList.add('active');
    $('tab-' + btn.dataset.tab).classList.add('active');
    if (btn.dataset.tab === 'feedback') { loadProjects(); loadVideos(); loadHistory(); }
  });
});

// ============================ CHECKER ============================
let previewOn = false;

async function loadDevices() {
  try {
    const { cameras, sources } = await api('/api/devices');
    const cam = $('cameraPicker');
    cam.innerHTML = cameras.map((c) => `<option value="${c.device}">${c.name} (${c.device})</option>`).join('');
    const mic = $('micPicker');
    mic.innerHTML = sources.map((s) =>
      `<option value="${s.name}">${s.clean ? '★ Speak Clean Mic' : s.name}</option>`).join('');
    // Aplica los dispositivos recordados (si siguen presentes) tras poblar los selects.
    const prefs = await loadPrefs();
    if (prefs.camera && [...cam.options].some((o) => o.value === prefs.camera)) cam.value = prefs.camera;
    if (prefs.mic && [...mic.options].some((o) => o.value === prefs.mic)) mic.value = prefs.mic;
  } catch (e) { $('noiseStatus').textContent = 'No pude listar dispositivos: ' + e.message; }
}

// Preferencias de dispositivo (persisten entre recargas vía /api/prefs).
async function loadPrefs() {
  try { return await api('/api/prefs'); } catch (_) { return {}; }
}
async function savePrefs() {
  try {
    await api('/api/prefs', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ camera: $('cameraPicker').value, mic: $('micPicker').value }),
    });
  } catch (_) { /* silencioso: no es crítico */ }
}
$('cameraPicker').addEventListener('change', savePrefs);
$('micPicker').addEventListener('change', savePrefs);

$('previewBtn').addEventListener('click', () => {
  const img = $('previewImg'), ph = $('previewPlaceholder');
  if (previewOn) {
    img.src = ''; img.style.display = 'none'; ph.style.display = ''; previewOn = false;
    $('previewBtn').textContent = 'Iniciar preview';
  } else {
    const dev = $('cameraPicker').value;
    img.src = `/video/preview.mjpeg?device=${encodeURIComponent(dev)}&t=${Date.now()}`;
    img.style.display = ''; ph.style.display = 'none'; previewOn = true;
    $('previewBtn').textContent = 'Detener preview';
  }
});

$('cameraInfoBtn').addEventListener('click', async () => {
  const dev = $('cameraPicker').value;
  $('cameraInfo').textContent = 'Consultando…';
  try {
    const { formats } = await api('/api/camera-info?device=' + encodeURIComponent(dev));
    $('cameraInfo').innerHTML = formats.map((f) => f.replace(/</g, '&lt;')).join('<br>');
  } catch (e) { $('cameraInfo').textContent = 'Error: ' + e.message; }
});

// VU meter
let vuSource = null;
function colorForDb(db) {
  // Objetivo -12..-6 dBFS = verde; entre -24 y -12 = ámbar; > -6 = rojo (saturando).
  if (db > -6) return 'var(--danger)';
  if (db >= -12) return 'var(--success)';
  if (db >= -24) return 'var(--warning)';
  return 'var(--accent-solid)';
}
$('vuBtn').addEventListener('click', () => {
  if (vuSource) {
    vuSource.close(); vuSource = null; $('vuBtn').textContent = 'Medir';
    $('vuFill').style.width = '0%'; $('vuValue').textContent = '— dBFS';
    return;
  }
  const src = $('micPicker').value;
  vuSource = new EventSource('/api/mic-level?source=' + encodeURIComponent(src));
  $('vuBtn').textContent = 'Detener';
  vuSource.onmessage = (ev) => {
    const { dbfs } = JSON.parse(ev.data);
    // Mapea -60..0 dBFS a 0..100% de ancho.
    const pct = Math.max(0, Math.min(100, ((dbfs + 60) / 60) * 100));
    $('vuFill').style.width = pct + '%';
    $('vuFill').style.background = colorForDb(dbfs);
    $('vuValue').textContent = dbfs.toFixed(1) + ' dBFS';
  };
  vuSource.onerror = () => { $('vuValue').textContent = 'sin señal'; };
});

// Prueba de voz: graba 5 s de la fuente elegida y la reproduce.
$('micTestBtn').addEventListener('click', async () => {
  const src = $('micPicker').value;
  if (!src) { $('micTestStatus').textContent = 'Selecciona una fuente de micrófono.'; return; }
  const btn = $('micTestBtn');
  btn.disabled = true;
  $('micTestStatus').className = 'status-line';
  $('micTestStatus').innerHTML = '<span class="spinner"></span>Grabando 5 s… habla ahora.';
  try {
    await api('/api/mic-test/record?seconds=5&source=' + encodeURIComponent(src), { method: 'POST' });
    const audio = $('micTestAudio');
    audio.src = '/api/mic-test/audio?t=' + Date.now();
    audio.load();
    $('micTestWrap').style.display = '';
    $('micTestStatus').className = 'status-line ok';
    $('micTestStatus').textContent = 'Listo — reprodúcelo abajo. Repite con otra fuente para comparar.';
  } catch (e) {
    $('micTestStatus').className = 'status-line err';
    $('micTestStatus').textContent = 'Error: ' + e.message;
  } finally {
    btn.disabled = false;
  }
});

// Filtro de ruido
async function refreshNoise() {
  try {
    const s = await api('/api/noise/status');
    $('noiseToggle').classList.toggle('active', s.active);
    let msg = s.active ? 'Filtro activo.' : 'Filtro apagado.';
    if (s.active && !s.available) msg += ' (Aún no aparece la fuente; espera un segundo.)';
    if (s.is_default) msg += ' Es tu micrófono por defecto.';
    $('noiseStatus').textContent = msg;
    $('noiseStatus').className = 'status-line ' + (s.active ? 'ok' : '');
  } catch (e) { $('noiseStatus').textContent = e.message; }
}
$('noiseToggle').addEventListener('click', async () => {
  const turnOn = !$('noiseToggle').classList.contains('active');
  $('noiseStatus').textContent = 'Aplicando…';
  try {
    await api('/api/noise/toggle?on=' + turnOn, { method: 'POST' });
    setTimeout(() => { refreshNoise(); loadDevices(); }, 800);
  } catch (e) { $('noiseStatus').className = 'status-line err'; $('noiseStatus').textContent = e.message; }
});
$('setDefaultBtn').addEventListener('click', async () => {
  try { await api('/api/noise/set-default', { method: 'POST' }); refreshNoise(); }
  catch (e) { $('noiseStatus').className = 'status-line err'; $('noiseStatus').textContent = e.message; }
});

// ============================ PROYECTOS ============================
// Los proyectos agrupan grabaciones (una subcarpeta por proyecto en data/). Este selector se
// comparte entre la pestaña Grabar (dónde guardar) y el filtro del historial.
async function loadProjects() {
  try {
    const { projects } = await api('/api/projects');
    const opts = projects
      .map((p) => `<option value="${p.slug}">${p.slug} (${p.count})</option>`).join('');
    const rec = $('recProjectPicker'), hist = $('histProjectPicker');
    if (rec) { const cur = rec.value; rec.innerHTML = opts; if (cur) rec.value = cur; }
    if (hist) {
      const cur = hist.value;
      hist.innerHTML = '<option value="">Todos</option>' + opts;
      if (cur) hist.value = cur;
    }
  } catch (_) { /* si falla, los selects quedan vacíos: no es crítico */ }
}

$('newProjectBtn')?.addEventListener('click', async () => {
  const raw = prompt('Nombre del proyecto (letras, dígitos, - y _):');
  if (!raw) return;
  const slug = raw.trim().replace(/[^A-Za-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '');
  if (!slug) { $('recStatus').textContent = 'Nombre de proyecto inválido.'; return; }
  try {
    await api('/api/projects?slug=' + encodeURIComponent(slug), { method: 'POST' });
    await loadProjects();
    $('recProjectPicker').value = slug;
  } catch (e) { $('recStatus').className = 'status-line err'; $('recStatus').textContent = e.message; }
});

// ============================ GRABAR ============================
let recTimerId = null, recStart = 0;
function fmtTime(s) {
  const m = Math.floor(s / 60), ss = Math.floor(s % 60);
  return String(m).padStart(2, '0') + ':' + String(ss).padStart(2, '0');
}
// Timestamp local YYYYMMDD-HHMMSS (solo caracteres válidos para el nombre de archivo).
function tsStamp() {
  const d = new Date(), p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
}
$('recStartBtn').addEventListener('click', async () => {
  const src = $('micPicker').value || 'default';
  const cam = $('cameraPicker').value || '/dev/video4';
  const project = $('recProjectPicker').value || 'default';
  // Nombre = (texto opcional saneado + '_')? + timestamp — siempre único.
  const typed = ($('recNameInput').value || '').trim().replace(/[^A-Za-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '');
  const name = (typed ? typed + '_' : 'grabacion_') + tsStamp();
  $('recStatus').textContent = 'Iniciando grabación…';
  try {
    // Pausa cualquier preview: la cámara no se puede abrir dos veces.
    if (previewOn) $('previewBtn').click();
    const r = await api(`/api/record/start?audio_source=${encodeURIComponent(src)}&video_device=${encodeURIComponent(cam)}&name=${encodeURIComponent(name)}&project=${encodeURIComponent(project)}`, { method: 'POST' });
    $('recDot').classList.add('on');
    $('recStartBtn').disabled = true; $('recStopBtn').disabled = false;
    $('recTimer').classList.remove('idle');
    recStart = Date.now();
    recTimerId = setInterval(() => { $('recTimer').textContent = fmtTime((Date.now() - recStart) / 1000); }, 500);
    $('recStatus').className = 'status-line ok';
    $('recStatus').textContent = 'Grabando en ' + r.path;
  } catch (e) { $('recStatus').className = 'status-line err'; $('recStatus').textContent = e.message; }
});
$('recStopBtn').addEventListener('click', async () => {
  try {
    const r = await api('/api/record/stop', { method: 'POST' });
    clearInterval(recTimerId);
    $('recDot').classList.remove('on');
    $('recStartBtn').disabled = false; $('recStopBtn').disabled = true;
    $('recStatus').className = 'status-line ok';
    $('recStatus').textContent = 'Guardado: ' + r.path + ' — analízalo en la pestaña Feedback.';
    loadProjects();  // refresca los contadores de grabaciones por proyecto
  } catch (e) { $('recStatus').className = 'status-line err'; $('recStatus').textContent = e.message; }
});

// ============================ FEEDBACK ============================
async function loadVideos() {
  try {
    const { videos } = await api('/api/videos');
    const sel = $('videoPicker');
    sel.innerHTML = videos.length
      ? videos.map((v) => `<option value="${v.path}">[${v.project}] ${v.name} (${v.size_mb} MB)</option>`).join('')
      : '<option value="">(no hay grabaciones en data/)</option>';
  } catch (e) { $('analyzeStatus').textContent = e.message; }
}

// Nombres legibles de cada etapa del pipeline (los emite el backend por SSE).
const STAGE_LABELS = {
  extract: 'Extrayendo audio…',
  split: 'Dividiendo el audio en partes…',
  transcribe: 'Transcribiendo (WhisperX)…',
  metrics: 'Calculando métricas…',
  feedback: 'Generando feedback con Claude…',
  save: 'Guardando la sesión…',
};

function setProgress(stage, pct) {
  $('analyzeProgressWrap').style.display = '';
  $('analyzeStage').textContent = STAGE_LABELS[stage] || stage || 'Procesando…';
  const p = Math.max(0, Math.min(100, Math.round(pct || 0)));
  $('analyzePct').textContent = p + '%';
  $('analyzeProgress').style.width = p + '%';
}

let analyzeSource = null;

// El análisis puede tardar de minutos a más de una hora (audios de 2–3 h). Por eso el backend
// lo corre en segundo plano (devuelve job_id al instante) y el progreso llega por SSE; así el
// navegador no hace timeout esperando una respuesta síncrona.
$('analyzeBtn').addEventListener('click', async () => {
  const video = $('videoPicker').value;
  if (!video) { $('analyzeStatus').textContent = 'No hay grabación seleccionada.'; return; }
  if (analyzeSource) { analyzeSource.close(); analyzeSource = null; }
  $('analyzeBtn').disabled = true;
  $('analyzeStatus').className = 'status-line';
  $('analyzeStatus').innerHTML = '<span class="spinner"></span>Encolando análisis…';
  setProgress('extract', 0);
  try {
    const { job_id } = await api('/api/analyze?video=' + encodeURIComponent(video), { method: 'POST' });
    $('analyzeStatus').innerHTML = '<span class="spinner"></span>Análisis en curso…';
    analyzeSource = new EventSource('/api/analyze/events/' + job_id);
    analyzeSource.onmessage = (ev) => {
      const d = JSON.parse(ev.data);
      if (d.status === 'running') { setProgress(d.stage, d.pct); return; }
      // Estado terminal: cierra el stream.
      analyzeSource.close(); analyzeSource = null;
      $('analyzeBtn').disabled = false;
      if (d.status === 'done') {
        setProgress('save', 100);
        renderFeedback(d.result);
        $('analyzeStatus').className = 'status-line ok';
        $('analyzeStatus').textContent = 'Listo (sesión #' + d.result.session_id + ').';
        setTimeout(() => { $('analyzeProgressWrap').style.display = 'none'; }, 1500);
        loadHistory();
      } else {
        $('analyzeStatus').className = 'status-line err';
        $('analyzeStatus').textContent = 'Error: ' + (d.error || 'falló el análisis');
        $('analyzeProgressWrap').style.display = 'none';
      }
    };
    analyzeSource.onerror = () => {
      // Fallback por polling si el SSE se corta (proxy, reconexión).
      analyzeSource.close(); analyzeSource = null;
      pollResult(job_id);
    };
  } catch (e) {
    $('analyzeBtn').disabled = false;
    $('analyzeStatus').className = 'status-line err';
    $('analyzeStatus').textContent = 'Error: ' + e.message;
    $('analyzeProgressWrap').style.display = 'none';
  }
});

// Fallback: si el SSE falla, sondea /result hasta que el job termine.
async function pollResult(jobId) {
  try {
    const d = await api('/api/analyze/result/' + jobId);
    if (d.status === 'running') { setProgress(d.stage, d.pct); setTimeout(() => pollResult(jobId), 2000); return; }
    $('analyzeBtn').disabled = false;
    if (d.status === 'done') {
      setProgress('save', 100);
      renderFeedback(d.result);
      $('analyzeStatus').className = 'status-line ok';
      $('analyzeStatus').textContent = 'Listo (sesión #' + d.result.session_id + ').';
      setTimeout(() => { $('analyzeProgressWrap').style.display = 'none'; }, 1500);
      loadHistory();
    } else {
      $('analyzeStatus').className = 'status-line err';
      $('analyzeStatus').textContent = 'Error: ' + (d.error || 'falló el análisis');
      $('analyzeProgressWrap').style.display = 'none';
    }
  } catch (e) {
    $('analyzeBtn').disabled = false;
    $('analyzeStatus').className = 'status-line err';
    $('analyzeStatus').textContent = 'Error: ' + e.message;
  }
}

function li(text, dotClass) {
  return `<li><span class="dot ${dotClass}"></span><span>${escapeHtml(text)}</span></li>`;
}
function escapeHtml(s) { return String(s).replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c])); }

function renderFeedback(r) {
  const m = r.metrics, fb = r.feedback;
  $('feedbackCard').style.display = '';

  $('fbMetrics').innerHTML = [
    ['Idioma', m.language],
    ['Duración', Math.round(m.duration_s) + ' s'],
    ['Ritmo', Math.round(m.wpm) + ' WPM'],
    ['Muletillas', m.filler_count + ' (' + m.fillers_per_min.toFixed(1) + '/min)'],
    ['Pausas largas', m.long_pause_count],
    ['Silencio', Math.round(m.pause_ratio * 100) + '%'],
  ].map(([l, v]) => `<div><div class="metric-value">${v}</div><div class="metric-label">${l}</div></div>`).join('');

  if (!fb) {
    $('fbScore').textContent = '–';
    $('fbSummary').textContent = r.feedback_error
      ? 'Solo métricas locales — feedback de Claude omitido: ' + r.feedback_error
      : 'Sin feedback de Claude (configura el proveedor en .env: ANTHROPIC_API_KEY o Vertex).';
    $('fbStrengths').innerHTML = $('fbImprovements').innerHTML = $('fbRewrites').innerHTML = $('fbGoals').innerHTML = '';
    $('fbCost').textContent = '';
    return;
  }
  $('fbScore').textContent = fb.overall_score;
  $('fbSummary').textContent = fb.summary;
  $('fbStrengths').innerHTML = (fb.strengths || []).map((s) => li(s, 'ok')).join('');
  $('fbImprovements').innerHTML = (fb.improvements || [])
    .map((i) => li(`[${i.area}] ${i.issue} → ${i.suggestion}`, 'warn')).join('');
  $('fbRewrites').innerHTML = (fb.rewrites || []).map((rw) =>
    `<div class="rewrite"><div class="before">${escapeHtml(rw.original)}</div><div class="after">${escapeHtml(rw.improved)}</div></div>`).join('');
  $('fbRewritesWrap').style.display = (fb.rewrites || []).length ? '' : 'none';
  $('fbGoals').innerHTML = (fb.next_session_goals || []).map((g) => li(g, 'accent')).join('');

  const tot = (fb.input_tokens || 0) + (fb.output_tokens || 0);
  const cost = fb.cost_usd ? ('~$' + fb.cost_usd.toFixed(4) + ' USD') : '(precio no configurado)';
  $('fbCost').textContent = tot
    ? `Tokens: ${fb.input_tokens} entrada + ${fb.output_tokens} salida = ${tot}. Costo: ${cost}. Modelo: ${fb.model}. (Transcripción y métricas: local, $0.)`
    : '';
}

// Historial: lista clicable agrupada por proyecto. Cada fila muestra su score, ver su feedback
// guardado (reutiliza renderFeedback) y reproducir el video (Play).
let histRows = [];

function histItemHtml(r, i) {
  const date = (r.created_at || '').slice(0, 16).replace('T', ' ');
  const name = (r.video_path || '').split('/').pop() || '(sin archivo)';
  const score = r.overall_score != null ? r.overall_score : '–';
  return `<div class="hist-item" data-idx="${i}">
    <span class="hist-item-score">${score}</span>
    <div class="hist-item-info">
      <div class="hist-item-name">${escapeHtml(name)}</div>
      <div class="hist-item-date">${escapeHtml(date)}</div>
    </div>
    <div class="hist-item-actions">
      <button class="glass sm hist-feedback-btn" data-idx="${i}">Ver feedback</button>
      <button class="glass sm hist-play-btn" data-idx="${i}">▶ Play</button>
    </div>
  </div>`;
}

async function loadHistory() {
  const box = $('histBars');
  try {
    const project = $('histProjectPicker') ? $('histProjectPicker').value : '';
    const q = '/api/history?limit=50' + (project ? '&project=' + encodeURIComponent(project) : '');
    histRows = await api(q);
    if (!histRows.length) { box.innerHTML = '<p class="muted">Aún no hay grabaciones analizadas.</p>'; return; }
    const groups = {};
    histRows.forEach((r, i) => { (groups[r.project || 'default'] = groups[r.project || 'default'] || []).push(i); });
    box.innerHTML = Object.keys(groups).sort().map((proj) => {
      const items = groups[proj].map((i) => histItemHtml(histRows[i], i)).join('');
      return `<div class="hist-group"><div class="hist-group-head">${escapeHtml(proj)}</div>${items}</div>`;
    }).join('');
  } catch (e) { box.innerHTML = '<p class="muted">' + escapeHtml(e.message) + '</p>'; }
}

// Muestra el feedback guardado de una sesión reutilizando renderFeedback (el JSON viene como
// string desde /api/history, así que lo parseamos antes).
function showHistFeedback(i) {
  const r = histRows[i];
  if (!r) return;
  let metrics = null, feedback = null;
  try { metrics = r.metrics_json ? JSON.parse(r.metrics_json) : null; } catch (_) {}
  try { feedback = r.feedback_json ? JSON.parse(r.feedback_json) : null; } catch (_) {}
  if (!metrics) { $('analyzeStatus').textContent = 'Esta sesión no tiene métricas guardadas.'; return; }
  renderFeedback({ metrics, feedback, feedback_error: null });
  $('feedbackCard').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// Play: en local abre el archivo en el reproductor de video del sistema (xdg-open en backend);
// en appliance/server no hay escritorio del host, así que se descarga para abrir localmente.
async function playVideo(path) {
  if (!path) return;
  if (APP_MODE === 'local') {
    try {
      await api('/api/video/open?path=' + encodeURIComponent(path), { method: 'POST' });
      $('analyzeStatus').className = 'status-line ok';
      $('analyzeStatus').textContent = 'Abriendo en tu reproductor de video…';
    } catch (e) { $('analyzeStatus').className = 'status-line err'; $('analyzeStatus').textContent = e.message; }
  } else {
    const a = document.createElement('a');
    a.href = '/api/video?path=' + encodeURIComponent(path);
    a.download = path.split('/').pop();
    document.body.appendChild(a); a.click(); a.remove();
  }
}

$('histBars').addEventListener('click', (ev) => {
  const play = ev.target.closest('.hist-play-btn');
  if (play) { playVideo(histRows[+play.dataset.idx].video_path); return; }
  const item = ev.target.closest('.hist-item');
  if (item) showHistFeedback(+item.dataset.idx);
});
$('histProjectPicker')?.addEventListener('change', loadHistory);

// ── Modo de ejecución ──
// El backend expone SPIK_MODE en /api/config:
//   local     — app del host: todo, incluido el filtro de ruido en vivo (systemctl --user + PipeWire).
//   appliance — contenedor privilegiado para compartir: captura + grabar + feedback, PERO sin filtro
//               de ruido en vivo (no hay systemctl --user en el contenedor) → se oculta esa tarjeta.
//   server    — contenedor detrás de Traefik: solo análisis, sin cámara/micro → se ocultan las
//               pestañas de captura y se arranca en Feedback.
async function applyMode() {
  let mode = 'local';
  try { mode = (await api('/api/config')).mode || 'local'; } catch (_) {}
  APP_MODE = mode;
  if (mode === 'local') { loadDevices(); refreshNoise(); loadProjects(); return; }
  if (mode === 'appliance') {
    // Captura sí (dispositivos vía contenedor privilegiado); filtro de ruido en vivo no.
    document.body.classList.add('appliance-mode');
    const noiseCard = $('noiseCard');
    if (noiseCard) noiseCard.style.display = 'none';
    loadDevices();
    loadProjects();
    return;
  }
  // server
  document.body.classList.add('server-mode');
  ['checker', 'record'].forEach((t) => {
    const tab = document.querySelector(`.tab[data-tab="${t}"]`);
    if (tab) tab.style.display = 'none';
  });
  // Activa la pestaña Feedback por defecto.
  document.querySelectorAll('.tab, .tabpanel').forEach((el) => el.classList.remove('active'));
  document.querySelector('.tab[data-tab="feedback"]').classList.add('active');
  $('tab-feedback').classList.add('active');
  loadProjects();
  loadVideos();
  loadHistory();
}

// ── Init ──
applyMode();
