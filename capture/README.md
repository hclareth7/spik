# Captura — Fase 0 (adaptada a esta máquina)

Objetivo: grabar video + audio en **la mejor calidad posible**, de forma reproducible, y
que quede en disco listo para analizar. Todo local; nada se sube.

## Hardware detectado en esta máquina

| Componente | Detalle | Uso recomendado |
|---|---|---|
| **Cámara externa** (UVC, `1d6c:0103`) | `/dev/video4` — MJPEG y H.264 en cámara hasta **2560×1440@30** | ✅ Cámara principal |
| Cámara integrada (Luxvisions RGB) | `/dev/video0` | Respaldo |
| **Cámara: H.264 en el sensor** | La webcam codifica H.264 hasta 1440p ella misma | ✅ Capturar con `copy` (0 CPU) |
| GPU Intel Iris Xe (Alder Lake-P) | VAAPI/QSV presentes en ffmpeg, pero **falta el driver** (`intel-media-driver`) | ⚠️ Opcional (ver abajo) |
| Audio | PipeWire (compat. PulseAudio) | Ver abajo |
| **Condensador + tarjeta V8** | `card 2` (`SmartlinkTechnology V8`) — 48 kHz | ✅ **Micro principal** |
| Micro webcam USB | `card 1` — **16 kHz mono** | ❌ Evitar (grado teléfono) |
| Micros integrados | `card 0` DMIC/analog, 48 kHz | ⚠️ Respaldo |

> Verificado con capturas de prueba: 2560×1440@30 desde `/dev/video4` graba correctamente,
> y la V8 capta a 48 kHz (piso de ruido −65 dB, SNR ≈ 28 dB).
>
> ⚠️ **Cierra las apps que usen la cámara antes de grabar** (el navegador la toma:
> `fuser /dev/video4` la muestra ocupada por Chrome). `record.sh` avisa si está en uso.

**Sobre el audio (importante):** la V8 aparece bajo control de **PipeWire**, así que la
captura directa por ALSA (`hw:2,0`) **falla** con "Input/output error". Hay que grabar por
PipeWire usando el **nombre de la fuente**. Lístalas con:

```bash
pactl list sources short
# fuente de la V8 (verifica el nombre exacto, puede variar por puerto):
#   alsa_input.usb-SmartlinkTechnology_V8_...-01.iec958-stereo
```

**Ganancia:** en la prueba los picos llegaron a −27 dB (algo bajo). Sube la perilla de la
V8 o acércate al micro (15–20 cm) para que los picos de voz queden en **−12…−6 dB**.

## Por qué OBS/ffmpeg y no el navegador

El navegador (`MediaRecorder`) recomprime y limita resolución/bitrate. Capturamos nativo
para llegar a 1440p con audio de 48 kHz. Dos caminos:

### Opción A — OBS Studio (recomendado, con vista previa)

**Output → Recording**
- Recording Format: `mkv`
- Encoder: `x264` con `veryfast` / CRF 20 (funciona sin drivers extra).
  Si instalas `intel-media-driver`, cambia a `VAAPI H.264` (CQP ≈ 20) para descargar la CPU.

**Video**
- Base y Output Resolution: **2560×1440** (nativa de la cámara externa)
- FPS: `30`
- Fuente: "Video Capture Device (V4L2)" → `/dev/video4`, formato de entrada **MJPEG**
  (evita YUYV crudo: a 1440p satura el ancho de banda USB)

**Audio**
- Sample Rate: `48 kHz`, Channels: `Mono`
- Fuente: la **tarjeta V8 / condensador** (`card 2`), NO el micro de la webcam

### Opción B — ffmpeg headless (reproducible, sin GUI)

Script incluido, con encoder VAAPI por hardware y la V8 por defecto:

```bash
# ./capture/record.sh <salida.mkv> [fuente_audio_pipewire]
./capture/record.sh data/practica-01.mkv          # usa la V8 por defecto
# Ctrl-C para detener. Lista fuentes de audio con:  pactl list sources short
```

El script captura el H.264 que genera la cámara con `-c:v copy` (sin re-codificar).

### Opcional — habilitar VAAPI (codificación por GPU)

Solo necesario si quieres re-codificar/escalar en tiempo real o usar VAAPI en OBS:

```bash
sudo dnf install intel-media-driver libva-utils
vainfo    # debe listar los perfiles H264/HEVC del Iris Xe
```

Con la cámara entregando H.264 directamente, **no hace falta** para grabar.

## Encuadre

- Cámara a la altura de los ojos; torso y manos visibles (necesario para gestos en Fase 3).
- Iluminación frontal, evita contraluz (mejora detección de rostro/mirada).

## Dónde guardar y siguiente paso

Guarda en `speak/data/` (ignorado por git). Luego analiza:

```bash
speak analyze data/practica-01.mkv
```

El pipeline extrae el audio automáticamente; si quieres el WAV a mano:

```bash
./capture/extract_audio.sh data/practica-01.mkv
```
