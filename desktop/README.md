# spik — app de escritorio (Wails v2 / Go)

Ventana nativa que reutiliza **exactamente** la misma interfaz web de spik. No hay un
segundo frontend ni cambios de diseño: el shell de escritorio lanza el backend FastAPI que
ya existe como *sidecar* (`python -m web.main`) y apunta el webview a él mediante un
**reverse proxy en Go**.

## Cómo funciona

```
┌─────────────────────────────┐        reverse proxy (FlushInterval=-1)
│  Ventana Wails (WebKitGTK)  │  ─────────────────────────────────────┐
│  AssetServer.Handler ───────┼──▶ httputil.ReverseProxy               │
└─────────────────────────────┘                                        ▼
                                          ┌──────────────────────────────────┐
   spawn: python -m web.main             │  FastAPI en 127.0.0.1:<puerto libre> │
   env: SPIK_MODE=local                  │  sirve web/dist + /api + /video + SSE │
        SPIK_HOST=127.0.0.1              └──────────────────────────────────┘
        SPIK_PORT=<libre>
```

- **Reverse proxy** (`net/http/httputil`): el origen del webview es el proxy Go, así que
  todas las rutas relativas del React (`/api`, `/video` MJPEG del preview, SSE del progreso)
  funcionan sin tocar el frontend. `FlushInterval = -1` fuerza *flush* inmediato para que el
  streaming MJPEG (`multipart/x-mixed-replace`) y los SSE no se bufericen.
- **Sidecar**: Go elige un puerto libre de *loopback*, arranca Python con ese `SPIK_PORT` y
  espera a que responda `GET /api/config` (200) antes de abrir la ventana (timeout 60 s).
- **Privacidad ("todo local")**: el sidecar escucha solo en `127.0.0.1`; el guard de
  `web/main.py` rechaza cualquier host que no sea *loopback*. Video/audio nunca salen de la
  máquina (misma garantía que la CLI y la web).
- **Cierre**: al cerrar la ventana, Go manda `SIGTERM` al grupo de procesos del sidecar y, si
  no termina en 5 s, `SIGKILL` (no quedan `uvicorn` huérfanos).

**Acrónimos:** *MJPEG* = Motion-JPEG (stream del preview de cámara); *SSE* = Server-Sent
Events (progreso del análisis); *ADC* = Application Default Credentials (auth de Vertex).

## Prerrequisitos

- **Go** ≥ 1.23 (probado con 1.25).
- **Wails v2** (`wails version` → probado con v2.14.0): `go install github.com/wailsapp/wails/v2/cmd/wails@latest`.
- **WebKitGTK + GTK3** (Fedora): `sudo dnf install webkit2gtk4.1-devel gtk3-devel`.
- El **entorno Python del proyecto** ya preparado: `.venv/` con las dependencias
  (`pip install -e ".[vision]"`) **y** el frontend construido (`cd frontend && npm run build`,
  que genera `web/dist`). El shell no instala nada: solo arranca lo que ya existe.

## Ejecutar

Desde este directorio (`desktop/`):

```bash
wails dev     # desarrollo: ventana + recarga del binario Go
wails build   # produce un binario nativo en build/bin/spik
```

`go build .` compila y valida el código Go sin abrir ventana (útil en CI headless).

### Variables de entorno (opcionales)

| Variable | Default | Para qué |
|---|---|---|
| `SPIK_PYTHON` | `<root>/.venv/bin/python` | Intérprete del sidecar (si no, usa el `.venv` del proyecto, y como último recurso `python3`). |
| `SPIK_PROJECT_ROOT` | autodetectado (busca `web/main.py` hacia arriba) | Raíz del repo, por si el binario se ejecuta fuera del árbol. |
| `SPIK_WEBKIT_COMPAT` | (desactivado) | `=1` activa `WEBKIT_DISABLE_COMPOSITING_MODE` y `WEBKIT_DISABLE_DMABUF_RENDERER`. Úsalo si el preview de cámara/`<video>` parpadea o se queda en negro en Wayland. |

> `SPIK_DATA_DIR` se deja sin definir a propósito: el sidecar reutiliza el `data/` del repo,
> así que tu historial de sesiones aparece igual que en la web. Para un instalado
> autocontenido conviene apuntarlo a una ruta XDG (`~/.local/share/spik`).

## Alcance de este MVP

Este shell **lanza el `.venv` del proyecto** (funciona ya en esta máquina). Un ejecutable
**autocontenido** (intérprete Python congelado + modelos de WhisperX/MediaPipe pre-bajados
embebidos) es un **seguimiento documentado**, no parte de este MVP: congelar
torch/whisperx/mediapipe es un esfuerzo grande e independiente (ver Fase 5 del roadmap).

## Notas de diseño

- `frontend/dist/` solo contiene un placeholder porque el directivo `//go:embed` exige que el
  directorio exista. **No** se sirven esos assets: al no haber `index.html`, toda petición cae
  del FS de assets (404) al `Handler` (el proxy), y Python sirve el frontend real.
- No se exponen métodos Go al frontend (`Bind` vacío): el shell es puramente ventana + proxy.
