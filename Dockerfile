# syntax=docker/dockerfile:1
# ─────────────────────────────────────────────────────────────────────────────
# Imagen de spik en MODO SERVIDOR: sirve Analizar + Feedback + Historial detrás de
# Traefik (spik.hclareth.local). NO hace captura (cámara/micro/PipeWire) — eso se queda
# en la app local del host. El video/audio a analizar llega por el volumen ./data montado;
# a Vertex solo va texto/métricas (misma ruta que el CLI).
#
# torch se instala desde el índice CPU de PyTorch (sin CUDA) para no arrastrar ~2 GB de
# wheels de GPU que esta imagen no usa.
# ─────────────────────────────────────────────────────────────────────────────

# ── Stage 0: frontend — build the React/Vite UI (Node only at build time) ──
# Pinned Node image for reproducibility. The built assets (web/dist) are copied into the
# runtime image; Node itself never ships in runtime. `npm ci` uses the committed
# package-lock.json so the dependency tree is deterministic.
FROM node:24-slim AS frontend

WORKDIR /fe
# Copy manifests first for layer caching (deps only re-install when they change).
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
# vite build.outDir is ../web/dist -> writes to /web/dist inside this stage.
RUN npm run build

# ── Stage 1: builder — crea un venv aislado con todas las dependencias ──
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

# git: algunas deps de whisperx se resuelven desde repos; build-essential por si hay
# paquetes con extensiones nativas sin wheel para 3.12.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv "$VIRTUAL_ENV"

WORKDIR /app

# torch + torchvision + torchaudio en su variante CPU, FIJADOS, desde el índice CPU de PyTorch.
# Se fijan las tres (no solo torch) y con versión exacta porque whisperx depende de torchvision:
# si no está pre-instalada en CPU, pip la resuelve desde PyPI y arrastra torch CUDA + ~5 GB de
# wheels nvidia-cu12 que esta máquina (sin GPU NVIDIA) nunca usa. Con las tres satisfechas, el
# install del paquete no las re-resuelve. Actualiza estas versiones si subes whisperx.
RUN pip install --no-cache-dir \
        torch==2.8.0 torchvision==0.23.0 torchaudio==2.8.0 \
        --index-url https://download.pytorch.org/whl/cpu

# Instala el paquete + extra 'web' (fastapi/uvicorn). Copiamos solo lo necesario para
# aprovechar la caché de capas de Docker.
COPY pyproject.toml README.md ./
COPY spik ./spik
RUN pip install --no-cache-dir ".[web]"

# Salvaguarda: si algún día una dep vuelve a colar torch CUDA, falla el build en vez de publicar
# una imagen de +8 GB con librerías nvidia inútiles en CPU.
RUN python -c "import torch,sys; v=torch.__version__; sys.exit(0 if v.endswith('+cpu') else f'torch no-CPU detectado: {v}')"

# ── Stage 2: runtime — imagen mínima con ffmpeg + el venv ya construido ──
FROM python:3.12-slim AS runtime

# ffmpeg incluye ffprobe (probe_duration/detect_silences/split_wav lo usan).
# v4l-utils: enumerar cámaras. pulseaudio-utils: parec/pactl (nivel de micro, grabación,
# lista de fuentes) — necesarios en modo 'appliance' (contenedor con captura); inertes en
# 'server'. Mantenemos UNA sola imagen para los dos modos (simplicidad).
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg v4l-utils pulseaudio-utils \
    && rm -rf /var/lib/apt/lists/*

ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Modo servidor: oculta captura y bloquea endpoints host-only.
    SPIK_MODE=server \
    # Datos (grabaciones + SQLite) en el volumen montado.
    SPIK_DATA_DIR=/data \
    # Cachés de modelos (WhisperX descarga ~1.5 GB la 1ª vez) bajo el volumen /data para que
    # PERSISTAN entre reinicios y sean escribibles por quien monte el volumen.
    HF_HOME=/data/.cache/huggingface \
    TORCH_HOME=/data/.cache/torch \
    XDG_CACHE_HOME=/data/.cache

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY spik ./spik
COPY web ./web
# Built React/Vite frontend from the Node stage (index.html + /assets, served by FastAPI).
# web/dist is excluded from the build context (.dockerignore) so only this copy provides it.
COPY --from=frontend /web/dist ./web/dist
# record.sh: lo invoca /api/record/start en modo 'appliance' (grabación nativa en el contenedor).
COPY capture ./capture

# Usuario no-root (buena práctica; UID fijo para permisos predecibles del volumen).
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin spik \
    && mkdir -p /data && chown -R spik:spik /data /app
USER spik

EXPOSE 8000

# Readiness/liveness: /api/config responde sin tocar hardware ni Vertex.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/config',timeout=3).status==200 else 1)"

# Dentro del contenedor escucha en 0.0.0.0; el aislamiento lo da la red de Traefik
# (el puerto no se publica al host, ver docker-compose.yaml).
CMD ["uvicorn", "web.main:app", "--host", "0.0.0.0", "--port", "8000"]
