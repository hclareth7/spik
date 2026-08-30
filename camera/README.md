# Speak Cam — cámara virtual con filtros en vivo (v4l2loopback)

Una **cámara virtual "Speak Cam"** que toma tu cámara física, le aplica realce de imagen en
vivo (luz/color + nitidez + reducción de ruido) y la expone como un `/dev/video` normal que
**cualquier app elige como webcam** (Zoom, Meet, OBS, Chrome). Es el análogo en video del
micrófono virtual "Speak Clean Mic" (`../noise/`).

```
cámara física ──► pipeline ffmpeg (dueño único) ──► /dev/video10 (v4l2loopback) ──► Zoom/Meet/OBS
 /dev/video4        filtros en vivo + preview          "Speak Cam"                  la eligen como webcam
```

**"Todo local":** el video nunca sale de la máquina; el loopback es un dispositivo del host.

## Cómo funciona (arquitectura)

- Un **solo** proceso ffmpeg (el "dueño único", `web/state.py::CaptureSession`) abre la
  cámara física **una vez** y reparte a varias salidas (`web/capture_pipeline.py`). La cámara
  virtual es una **tercera salida `-map`** de ese mismo proceso: frames filtrados escritos a
  `/dev/video10`. Como es la **misma** apertura del device real, nunca hay "device busy".
- **v4l2loopback** admite **múltiples lectores**: Zoom, Meet y el preview de spik pueden leer
  a la vez de `/dev/video10` (una webcam real es *single-open*, un solo lector).
- **WYSIWYG:** con Speak Cam encendida, el preview de spik muestra la **misma** imagen
  filtrada que verá Zoom.
- La app **nunca** ejecuta `modprobe`/`sudo` en runtime: solo *usa* un `/dev/video10` ya
  aprovisionado (igual que el filtro de ruido solo *usa* RNNoise ya cargado). El runtime
  sigue **sin privilegios**.

## Instalación (una vez — **requiere sudo/kernel**)

> Esta es la diferencia clave con `../noise/install.sh` (que es puro `systemd --user`, **sin
> root**). Aquí se instala un **módulo de kernel** y se escribe en `/etc`, así que **sí**
> requiere `sudo`.

```bash
sudo bash camera/install.sh
```

Qué hace (todo como root):

1. `dnf install akmod-v4l2loopback v4l2loopback-utils` (RPM Fusion). **akmod** reconstruye el
   módulo automáticamente tras cada actualización de kernel.
2. Escribe `/etc/modprobe.d/speak-cam.conf`:
   `options v4l2loopback exclusive_caps=1 video_nr=10 card_label="Speak Cam"`
   - `exclusive_caps=1` → el device anuncia solo *capture caps*, requisito para que
     Chrome/Zoom/Meet lo listen como webcam.
   - `video_nr=10` → fija `/dev/video10` (estable para regex/validación/selectores).
   - `card_label` → nombre amigable leído de `/sys/class/video4linux/video10/name`.
3. Escribe `/etc/modules-load.d/speak-cam.conf` (carga en cada arranque).
4. Carga el módulo ahora (sin reboot) y verifica que aparezca `/dev/video10`.

**Prerrequisito RPM Fusion (free)** si el `dnf install` falla:
```bash
sudo dnf install https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm
```

## Verificación

```bash
v4l2-ctl --list-devices            # debe listar "Speak Cam" en /dev/video10
```
En spik (pestaña **Checker**): enciende **Speak Cam**, mueve los sliders y verás el preview
retocado. En una llamada de prueba de Meet/Zoom, elige **"Speak Cam"** como webcam.

## Controles → filtros ffmpeg

Orden de la cadena: **denoise → color → sharpen → format** (denoise antes de sharpen para no
realzar ruido; color antes de sharpen para bordes predecibles). Definido en
`web/capture_pipeline.py::build_filter_chain`; validado por rango en `web/routers/vcam.py`.

| Slider | Filtro ffmpeg | Rango | Neutro |
|---|---|---|---|
| Brillo | `eq brightness` | −0.3 … 0.3 | 0 |
| Contraste | `eq contrast` | 0.5 … 1.8 | 1.0 |
| Gamma | `eq gamma` | 0.5 … 2.0 | 1.0 |
| Saturación | `eq saturation` | 0 … 2.5 | 1.0 |
| Nitidez | `unsharp` (5:5:amt) | 0 … 1.5 | 0 (se omite) |
| Ruido | `hqdn3d` | off / light `2:1.5:3:3` / strong `6:4:9:6` | off |

## Riesgos / notas

- **sudo/kernel:** rompe la propiedad "sin sudo" del filtro de audio; el runtime igual sigue
  sin privilegios (nunca `modprobe`/`sudo`).
- **Fragilidad del módulo:** `v4l2loopback` es *out-of-tree*; tras un update de kernel puede
  tardar en reconstruir (akmod) y `/dev/video10` desaparece temporalmente. La app degrada con
  gracia (`GET /api/vcam/status` → `available:false`, la card avisa "corre camera/install.sh")
  en vez de crashear.
- **CPU:** toda rama filtrada (preview y vcam) fuerza decode + re-encode; `hqdn3d` fuerte es
  lo más pesado. Por defecto 720p, denoise `off`, y los cambios de slider tienen *debounce*.
- **Solo host (`local`):** requiere el módulo de kernel del host y ser el **único** opener de
  la cámara real; por eso `appliance`/`server` no lo corren (gated por `require_host_session`).
- **Anti-realimentación:** nunca se abre `/dev/video10` como *fuente* (leer mientras se
  escribe daría bucle); `vcam/start` rechaza que la fuente sea el propio loopback.

## Fuera de alcance (v1)

- Fondo/blur (mediapipe) y `curves` — diferidos; `eq` cubre los 4 controles de color.
- Persistir los valores de filtros entre sesiones (hoy viven en la card).
