# spik — coach personal de comunicación

Grábate, y recibe feedback accionable para mejorar **cómo te comunicas**: muletillas,
ritmo, pausas, estructura de ideas — y, en fases siguientes, prosodia (voz), gestos y
contacto visual. **Todo el procesamiento es local**: el video nunca sale de tu máquina;
a la nube (Claude) solo se envía texto + métricas para generar el feedback.

Bilingüe (español / inglés), detección automática de idioma.

## Demo

Flujo completo en **modo inglés**: grabar una práctica → analizarla en local
(transcripción con WhisperX + métricas verbales) → ver el feedback de Claude
(puntuación, resumen, fortalezas, mejoras, reescrituras y objetivos). El video/audio
nunca sale de la máquina; a la nube solo van texto y métricas.

![Demo de spik: grabar, analizar en local y ver el feedback (modo inglés)](docs/demo.gif)

## Arquitectura (resumen)

```
  [Cámara + micro]              [Análisis local, Python]              [Feedback]
  OBS Studio  ──►  video.mkv ──► ffmpeg ─► WhisperX ─► métricas ──►  Claude API
  (máx. calidad)                 (audio)   (transcript)  verbales     (texto → coaching)
                                                             │
                                                             ▼
                                                     SQLite (progreso)
```

Se usa captura nativa (OBS) en vez del navegador porque el navegador recomprime y limita
la calidad. Ver `capture/README.md`.

## Hardware de esta máquina (verificado)

| Componente | Detalle |
|---|---|
| Cámara externa | UVC `1d6c:0103` en `/dev/video4` — hasta **2560×1440@30** (MJPEG / H.264 en cámara) |
| GPU | Intel Iris Xe — codificación H.264 por hardware (VAAPI/QSV); **sin NVIDIA/CUDA** |
| Audio | PipeWire; **condensador + tarjeta V8** `card 2` (48 kHz, micro principal); webcam 16 kHz (evitar) |
| Software | ffmpeg 7.1.2, OBS Studio, Python 3.12 |

Detalles y perfiles de grabación en [`capture/README.md`](capture/README.md).

## Requisitos

- **Python 3.11 o 3.12** (NO 3.14: whisperx/mediapipe/torch aún no lo soportan).
  En esta máquina: `/usr/bin/python3.12`.
- `ffmpeg` y `OBS Studio` (ya instalados).
- **Sin GPU NVIDIA** → la transcripción corre en CPU (Whisper `small`/`medium`, int8).
  La *grabación* sí usa la GPU Intel (VAAPI) para no cargar la CPU.
- Acceso a **Google Vertex AI** con modelos de Anthropic (para el feedback). Alternativa:
  una `ANTHROPIC_API_KEY` con `SPIK_PROVIDER=anthropic`.
- Recomendado instalar diagnósticos: `sudo dnf install v4l-utils libva-utils`.

## Instalación

```bash
cd speak
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .            # Fase 1 (transcripción + feedback vía Vertex)
# Fases siguientes (opcional):
# pip install -e ".[prosody]"   # Fase 2: voz
# pip install -e ".[vision]"    # Fase 3: gestos/rostro
# pip install -e ".[dev]"       # tests + linter

cp .env.example .env       # ajusta SPIK_VERTEX_PROJECT y la región
```

### Autenticación con Vertex AI (una vez)

Vertex usa las credenciales de Google (ADC), **no** una API key de Anthropic:

```bash
gcloud auth application-default login
gcloud config set project TU-PROYECTO-GCP
```

Luego en `.env`: `SPIK_PROVIDER=vertex`, `SPIK_VERTEX_PROJECT=tu-proyecto`,
`SPIK_VERTEX_REGION=global`, y `SPIK_CLAUDE_MODEL=` con un modelo habilitado en tu
Model Garden. En esta máquina responden `claude-sonnet-4-5` (recomendado) y
`claude-haiku-4-5` en la región `global`.

> **Política de organización (Vertex):** este proyecto GCP veta `structured_outputs` y
> otras features avanzadas de modelos partner
> (`constraints/vertexai.allowedPartnerModelFeatures`). Por eso `feedback.py` pide el JSON
> **por prompt** y lo parsea, en vez de usar `output_config`/JSON schema nativo o
> *extended thinking*. Es 100% compatible; no requiere cambios del admin.

> Gestión de secretos: con Vertex no hay clave que guardar (ADC vive fuera del repo). Si
> más adelante automatizas esto en un servidor, usa una **service account** con rol mínimo
> (`roles/aiplatform.user`) en vez de credenciales de usuario.

## Uso

1. **Graba** a máxima calidad (1440p + audio 48 kHz). Dos opciones (ver `capture/README.md`):

   ```bash
   # A) OBS Studio (con vista previa) — cámara /dev/video4, audio V8, encoder VAAPI
   # B) ffmpeg headless (usa la tarjeta V8 por defecto):
   ./capture/record.sh data/practica-01.mkv
   ```

2. **Analiza:**

   ```bash
   spik analyze data/2026-08-27_practica-01.mkv
   # sin feedback de Claude (solo métricas locales):
   spik analyze data/practica.mkv --no-feedback
   # forzar idioma o modelo:
   spik analyze data/practica.mkv --language es --whisper-model small
   ```

3. **Ve tu progreso** en el tiempo:

   ```bash
   spik history
   ```

## GUI local + filtro de ruido

Además del CLI, hay una **interfaz gráfica web local** (FastAPI, escucha solo en
`127.0.0.1`) y un **filtro de ruido RNNoise** para el aire acondicionado de la oficina.

```bash
pip install -e ".[web]"     # instala fastapi + uvicorn
python -m web.main          # abre http://127.0.0.1:8000
```

La GUI tiene tres pestañas: **Checker de inputs** (preview de cámara, nivel de micro en
vivo, toggle del filtro de ruido), **Grabar** (start/stop) y **Feedback** (corre el mismo
pipeline del CLI). Detalles en [`web/README.md`](web/README.md).

**Filtro de ruido "Speak Clean Mic"** — crea una fuente de micrófono virtual y ya limpia
que Chrome, Zoom o Meet pueden elegir. Es IaC (un `.conf` de PipeWire filter-chain) y se
prende/apaga con `systemctl --user`. Requiere compilar el wrapper LADSPA una sola vez:

```bash
bash noise/build-rnnoise-ladspa.sh   # compila librnnoise_ladspa.so → ~/.local/lib/ladspa (sin sudo)
bash noise/install.sh                # enlaza el filter-chain y el drop-in de systemd
systemctl --user start filter-chain.service   # o el toggle de la GUI
```

Detalles, arquitectura y ajuste del VAD en [`noise/README.md`](noise/README.md). El
realce de imagen de la cámara (estilo Meet) está **diferido** y documentado en
[`camera/README.md`](camera/README.md).

## Audios largos (2–3 h)

La transcripción corre en CPU y es el cuello de botella. Para audios largos, spik **divide el
audio en trozos conscientes del silencio** (para no cortar palabras) y los transcribe en
**procesos paralelos**, cosiendo los timestamps a tiempo absoluto. En la GUI el análisis corre
**en segundo plano** con **barra de progreso** (SSE), así el navegador no hace timeout.

Todo es configurable por `.env` (ver `.env.example`): `SPIK_CHUNK_THRESHOLD_S` (umbral para
activar la ruta troceada), `SPIK_WHISPER_CHUNK_S` (tamaño de trozo), `SPIK_WHISPER_WORKERS`
(0 = auto según núcleos/RAM) y `SPIK_WHISPER_BATCH_SIZE`. Los defaults están dimensionados para
esta máquina (20 núcleos, ~16 GB libres → ~5 workers, ~3 GB c/u).

## Despliegue (Docker/Podman + Traefik + GHCR)

Hay un despliegue en **modo servidor** (solo Analizar + Feedback + Historial; la captura sigue
siendo local) detrás del Traefik local, en `https://spik.hclareth.local`. La imagen se publica
en `ghcr.io/hclareth7/spik` (privada). Guía completa en [`deploy/README.md`](deploy/README.md).

```bash
podman-compose -f deploy/docker-compose.yaml up -d
```

## Qué mide hoy (Fase 1)

- **Muletillas** (es/en): "eh", "este", "o sea", "um", "you know", ...
- **Ritmo** (WPM = palabras por minuto) con rango humano cómodo de referencia.
- **Pausas** largas y % de silencio.
- **Feedback de Claude** con rúbricas de comunicación (Pirámide de Minto, STAR,
  criterios Toastmasters): fortalezas, mejoras priorizadas, reescrituras y objetivos.

## Roadmap

| Fase | Contenido | Estado |
|---|---|---|
| 0 | Setup + captura (OBS/ffmpeg) | ✅ |
| 1 | MVP verbal (transcripción + métricas + feedback + SQLite) | ✅ |
| 2 | Prosodia (tono, energía, monotonía) — `spik/prosody.py` | ⬜ stub |
| 3 | Visión (gestos, postura, rostro, mirada) — `spik/vision.py` | ⬜ stub |
| 4 | GUI web local (checker de inputs + grabar + feedback) + filtro de ruido RNNoise | ✅ |
| 4b | Realce de imagen de cámara (v4l2loopback + GStreamer) | ⬜ diferido |
| 4c | App de escritorio (Wails/Go) — misma UI web en ventana nativa (`desktop/`) | ✅ MVP |
| 5 | Empaquetado autocontenido (intérprete + modelos embebidos) + feedback en tiempo real | ⬜ |

## Tests

```bash
PYTHONPATH=. python -m pytest tests/ -q
```

Los tests de métricas verbales son puros (no requieren las librerías pesadas ni red).

## Privacidad

- El video/audio **nunca** se sube: Whisper y (en fases futuras) MediaPipe corren local.
- A Claude (vía Vertex AI) solo se le manda el **transcript + métricas** (texto).
  Revisa `spik/feedback.py`.
- `data/` y `.env` están en `.gitignore`.
