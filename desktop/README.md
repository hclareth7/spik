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
  todas las rutas relativas del React (`/api`, `/video`, SSE del progreso) funcionan sin tocar
  el frontend. `FlushInterval = -1` fuerza *flush* inmediato para que los SSE no se bufericen.
- **Shim de preview de cámara**: WebKitGTK **no** renderiza `multipart/x-mixed-replace` en un
  `<img>` (el preview saldría en negro), así que el proxy inyecta un pequeño script
  (`mjpeg_shim.go`) que pinta el preview con *snapshots* — ver la sección siguiente.
- **Sidecar**: Go elige un puerto libre de *loopback*, arranca Python con ese `SPIK_PORT` y
  espera a que responda `GET /api/config` (200) antes de abrir la ventana (timeout 60 s).
- **Privacidad ("todo local")**: el sidecar escucha solo en `127.0.0.1`; el guard de
  `web/main.py` rechaza cualquier host que no sea *loopback*. Video/audio nunca salen de la
  máquina (misma garantía que la CLI y la web).
- **Cierre**: al cerrar la ventana, Go manda `SIGTERM` al grupo de procesos del sidecar y, si
  no termina en 5 s, `SIGKILL` (no quedan `uvicorn` huérfanos).

**Acrónimos:** *MJPEG* = Motion-JPEG (stream del preview de cámara); *SSE* = Server-Sent
Events (progreso del análisis); *ADC* = Application Default Credentials (auth de Vertex).

## Preview de cámara en WebKitGTK

WebKitGTK (el motor del webview de Wails en Linux) no renderiza
`multipart/x-mixed-replace` en un `<img>` **ni** entrega de forma incremental un cuerpo
`fetch()` sin fin, así que el `/video/preview.mjpeg` clásico se ve **en negro**. El shim
inyectado (`mjpeg_shim.go`) lo resuelve así:

- **Transporte por *snapshots***: en vez del stream infinito, sondea `GET /video/snapshot.jpg`
  (cada ~30 ms, es decir ~33 fps) y pinta cada JPEG como `blob:` URL. Cada respuesta es finita,
  que WebKitGTK sí entrega. Backend y snapshot comparten el mismo búfer *fan-out* del servidor,
  así que la cámara (de apertura única) se abre **una sola vez** (ver `web/routers/preview.py`,
  `web/state.py`).
- **Sin parpadeo (doble búfer)**: antes de intercambiar el `<img>` visible, el frame se
  **decodifica fuera de pantalla** (`Image.decode()`) y solo entonces se hace el *swap*. Asignar
  un `blob:` sin decodificar deja el elemento en negro (`.preview-wrap`) hasta que carga, lo que
  a ~30/s se percibía como un parpadeo rápido.
- **Baja latencia**: el backend añade `-flush_packets 1` a la salida MJPEG (escribe cada JPEG al
  *pipe* en cuanto se codifica, en vez de acumular ~0,5 s en el búfer AVIO) y los flags de
  demuxer `-fflags nobuffer -flags low_delay` en la entrada de la cámara siempre que **no** haya
  grabación compartiendo el input. Resultado: latencia sub-*frame* (~25 ms).
- **Espejo (self-view)**: el `<img>` que gestiona el shim lleva `transform: scaleX(-1)` para que
  el auto-vídeo se lea como un espejo (orientación *selfie*, como Zoom/Meet). El archivo grabado
  y el feed crudo de la cámara **no** se tocan.

## Prerrequisitos

- **Go** ≥ 1.23 (probado con 1.25).
- **Wails v2** (`wails version` → probado con v2.14.0): `go install github.com/wailsapp/wails/v2/cmd/wails@latest`.
- **WebKitGTK 4.1 + GTK3** (Fedora): `sudo dnf install webkit2gtk4.1-devel gtk3-devel`. Como el
  sistema trae `webkit2gtk-4.1` (no la 4.0 que Wails asume por defecto), la compilación usa la
  *tag* `webkit2_41` (ver más abajo).
- El **entorno Python del proyecto** ya preparado: `.venv/` con las dependencias
  (`pip install -e ".[vision]"`) **y** el frontend construido (`cd frontend && npm run build`,
  que genera `web/dist`). El shell no instala nada: solo arranca lo que ya existe.

## Ejecutar

Desde este directorio (`desktop/`):

```bash
wails dev   -tags webkit2_41   # desarrollo: ventana + recarga del binario Go
wails build -tags webkit2_41   # produce un binario nativo en build/bin/spik
```

La *tag* `webkit2_41` enlaza contra `webkit2gtk-4.1` (la que trae Fedora ≥ 41); sin ella la
compilación busca la 4.0 y falla. `go build -tags webkit2_41 .` compila y valida el código Go
sin abrir ventana (útil en CI headless).

### Icono (ventana / barra de tareas)

`desktop/build/appicon.png` (1024×1024 RGBA) es la marca de spik rasterizada desde
`frontend/public/favicon.svg`. `main.go` lo embebe con `//go:embed` y lo pasa a
`linux.Options.Icon`, y `ProgramName: "spik"` fija la *WM class* de la ventana. `build/` está
en `.gitignore`, así que el PNG se versiona con `git add -f` (los binarios de `build/bin/` no).

### Variables de entorno (opcionales)

| Variable | Default | Para qué |
|---|---|---|
| `SPIK_PYTHON` | `<root>/.venv/bin/python` | Intérprete del sidecar (si no, usa el `.venv` del proyecto, y como último recurso `python3`). |
| `SPIK_PROJECT_ROOT` | autodetectado (busca `web/main.py` hacia arriba) | Raíz del repo, por si el binario se ejecuta fuera del árbol. |
| `SPIK_WEBKIT_COMPAT` | (desactivado) | `=1` activa `WEBKIT_DISABLE_COMPOSITING_MODE` y `WEBKIT_DISABLE_DMABUF_RENDERER`. El preview de cámara ya no necesita esto (lo resuelve el shim de *snapshots*); queda como *fallback* por si algún `<video>` u otro contenido parpadea en Wayland. |

> `SPIK_DATA_DIR` se deja sin definir a propósito: el sidecar reutiliza el `data/` del repo,
> así que tu historial de sesiones aparece igual que en la web. Para un instalado
> autocontenido conviene apuntarlo a una ruta XDG (`~/.local/share/spik`).

## Alcance de este MVP

Este shell **lanza el `.venv` del proyecto** (funciona ya en esta máquina). Un ejecutable
**autocontenido** (intérprete Python congelado + modelos de WhisperX/MediaPipe pre-bajados
embebidos) es un **seguimiento documentado**, no parte de este MVP: congelar
torch/whisperx/mediapipe es un esfuerzo grande e independiente (ver Fase 5 del roadmap).

## Notas de diseño

- **AssetServer solo con `Handler`** (sin `Assets` fs.FS): toda petición —incluida la raíz—
  la sirve el reverse proxy hacia el sidecar Python, que sirve el frontend real (`web/dist`).
  Pasar un `Assets` sin `index.html` hace que Wails trate la raíz como error en vez de caer al
  `Handler`, así que se omite por completo. `frontend/dist/` queda como placeholder de la
  estructura convencional de Wails, sin embeberse.
- **Teardown robusto del sidecar**: `SysProcAttr{Setpgid, Pdeathsig: SIGKILL}`. `OnShutdown`
  hace `SIGTERM`→`SIGKILL` al grupo al cerrar la ventana; además `Pdeathsig` hace que el kernel
  mate el sidecar si el proceso Go muere por cualquier causa (crash, `SIGTERM`/`SIGKILL`
  externo), de modo que una ventana matada a la fuerza nunca deja un `uvicorn` huérfano.
- No se exponen métodos Go al frontend (`Bind` vacío): el shell es puramente ventana + proxy.
