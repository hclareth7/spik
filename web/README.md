# GUI web local de spik

Interfaz gráfica ligera (FastAPI + HTML/CSS/JS estático) para: revisar cámara y micrófono,
activar el filtro de ruido, grabar una práctica y ver el feedback. Reutiliza el mismo pipeline
del CLI (`spik.report`), así que el análisis y el costo son idénticos.

**Privacidad:** el servidor escucha **solo en `127.0.0.1`**. El video y el audio nunca salen de
tu máquina; a Vertex solo se envía texto/métricas (igual que el CLI).

## Requisitos

- Dependencias web: `pip install -e ".[web]"` (instala `fastapi` y `uvicorn`).
- Herramientas del sistema (ya presentes en esta máquina): `ffmpeg`, `parec`, `pactl`, `wpctl`,
  `systemctl`. Opcional: `v4l-utils` (`dnf install v4l-utils`) mejora el detalle de formatos de cámara.
- Para el toggle de ruido: haber corrido antes `noise/build-rnnoise-ladspa.sh` y `noise/install.sh`
  (ver `noise/README.md`).

## Ejecutar

```bash
# desde la raíz del proyecto (con el venv activo)
python -m web.main
# o, equivalente:
uvicorn web.main:app --host 127.0.0.1 --port 8000
```

Abre <http://127.0.0.1:8000>.

## Frontend (React + Vite + TypeScript)

La interfaz vive en `frontend/` (React + Vite + TS, i18n es/en). FastAPI **sirve el build**
desde `web/dist/` (index en `/`, assets en `/assets`). Las fuentes (Inter + JetBrains Mono)
están **vendorizadas localmente** (self-host vía `@fontsource`, empaquetadas en el build) — no
hay llamadas a CDN ni Google Fonts. Todo el API es same-origin.

### Compilar la UI (necesario para servirla desde FastAPI)

```bash
cd frontend
npm ci            # instala dependencias desde package-lock.json (reproducible)
npm run build     # type-check (tsc) + vite build -> escribe en ../web/dist
```

Tras `npm run build`, `python -m web.main` sirve la UI nueva. Si `web/dist/` no existe,
FastAPI recurre a la UI estática legacy en `web/static/` (rollback). El endpoint `/` devuelve
404 con instrucciones si no hay ninguna de las dos.

### Desarrollo con hot-reload (Vite dev server + proxy)

```bash
# Terminal 1: backend
uvicorn web.main:app --host 127.0.0.1 --port 8000
# Terminal 2: frontend con recarga en caliente
cd frontend && npm run dev        # http://localhost:5173
```

El dev server de Vite (`vite.config.ts`) hace **proxy** de `/api` y `/video` a `127.0.0.1:8000`.
node-http-proxy transmite las respuestas sin bufferear, así que **SSE** (`text/event-stream`:
nivel de micro y progreso de análisis) y **MJPEG** (`multipart/x-mixed-replace`: preview de
cámara) fluyen en tiempo real a través del proxy.

### Type-check aislado

```bash
cd frontend && npm run typecheck   # tsc --noEmit
```

## Pestañas

1. **Checker de inputs** — preview de cámara (MJPEG, 720p si la cámara lo soporta), formatos
   soportados, medidor de nivel de micrófono en vivo (verde = −12…−6 dBFS), **prueba de voz**
   (graba 5 s de la fuente elegida y la reproduce — elige *Speak Clean Mic* vs. el micro físico
   para comparar el filtro), y toggle del filtro **Speak Clean Mic** + “usar por defecto”.
   El dispositivo de cámara/micrófono que elijas se **recuerda** entre recargas.
2. **Grabar** — start/stop (envuelve `capture/record.sh`), cronómetro; guarda el `.mkv` en `data/`.
3. **Feedback** — elige una grabación, corre el análisis y muestra score, fortalezas, mejoras,
   reescrituras, objetivos, tokens/costo y la tendencia del historial.

## Nota de contención de dispositivo

Una cámara `/dev/videoN` **no se puede abrir dos veces a la vez**. Por eso el preview se **pausa
automáticamente al grabar** (y la grabación usa el dispositivo en exclusiva). Si ves “dispositivo
ocupado”, cierra el preview u otras apps (Chrome/Zoom) que estén usando la cámara.

## Endpoints (referencia)

| Método | Ruta | Qué hace |
|---|---|---|
| GET | `/api/devices` | Lista cámaras y micrófonos. |
| GET | `/api/camera-info?device=` | Formatos/resoluciones de la cámara. |
| GET | `/video/preview.mjpeg?device=` | Preview MJPEG (multipart). |
| GET | `/api/mic-level?source=` | Nivel dBFS por SSE. |
| POST/GET | `/api/mic-test/record?source=&seconds=` · `/audio` | Graba y reproduce una prueba de voz. |
| GET/POST | `/api/prefs` | Dispositivos recordados (cámara/micrófono). |
| GET/POST | `/api/noise/status` · `/toggle?on=` · `/set-default` | Filtro de ruido. |
| POST | `/api/record/start` · `/stop` | Grabación. |
| GET | `/api/videos` | Grabaciones en `data/`. |
| POST | `/api/analyze?video=&feedback=` | Encola el análisis en segundo plano; devuelve `{job_id}` al instante. |
| GET | `/api/analyze/events/{job_id}` | Progreso del job por SSE (`stage`, `pct`) hasta el resultado. |
| GET | `/api/analyze/result/{job_id}` | Resultado final del job (para reconexión / fallback por polling). |
| GET | `/api/history` | Historial de sesiones. |
| GET | `/api/config` | Modo de ejecución (`local`/`server`); el frontend oculta captura en `server`. |

## Audios largos y modo de ejecución

- **Jobs en segundo plano:** `POST /api/analyze` no bloquea — el análisis (que para audios de
  2–3 h puede tardar) corre en segundo plano y el progreso llega por **SSE** con barra en la GUI.
  Guardia de **un solo job a la vez**. Los resultados se persisten en SQLite aunque se recargue.
- **Modo servidor** (`SPIK_MODE=server`, ver `deploy/`): el contenedor detrás de Traefik sirve
  solo Analizar/Feedback/Historial; las pestañas de captura (cámara/micro/ruido) se ocultan y sus
  endpoints host-only responden **HTTP 503**. La captura sigue siendo la app local en `127.0.0.1`.

El diseño (glass morphism, botones pill, sin `border-left`, feedback visual) sigue el sistema
de nout (`nout/spec/mockups/nout-widgets.html`).
