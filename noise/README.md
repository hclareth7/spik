# Filtro de ruido — "Speak Clean Mic" (RNNoise + PipeWire)

Crea una **fuente de micrófono virtual limpia** que suprime el ruido de fondo (p. ej. el
zumbido del aire acondicionado) con **RNNoise** (red neuronal de supresión de ruido). Otras
apps — **Chrome, Zoom, Meet, Speak** — la eligen como micrófono y reciben el audio ya limpio,
sin que ellas sepan nada del filtro. Es "aparte pero integrable": vive fuera de la app pero la
GUI de Speak lo prende/apaga.

Todo local, reproducible (Infrastructure as Code) y **sin sudo**.

## Arquitectura

```
Micrófono físico (V8) ──> [ filter-chain de PipeWire + RNNoise (LADSPA) ] ──> "Speak Clean Mic"
                                                                                     │
                                          Chrome / Zoom / Meet / Speak  <────────────┘
                                          (lo seleccionan como micrófono)
```

- **RNNoise**: el motor de supresión (mejor calidad para ruido estacionario como un A/C).
- **filter-chain de PipeWire**: módulo nativo que encadena el micro → RNNoise → fuente virtual.
- **LADSPA** (*Linux Audio Developer's Simple Plugin API*): el formato de plugin que PipeWire
  carga; RNNoise se usa a través del wrapper `librnnoise_ladspa.so`.

## Por qué hay un paso de compilación

El paquete `rnnoise` de Fedora **solo** trae la librería base (`librnnoise.so`), no el wrapper
LADSPA que el filter-chain necesita, y `noise-suppression-for-voice` no está en los repos. Por
eso `build-rnnoise-ladspa.sh` compila el wrapper una vez desde fuente y lo instala en
`~/.local/lib/ladspa/` (dentro de tu HOME, sin permisos de root).

## Instalación (una vez)

```bash
# 1) Compilar el plugin RNNoise LADSPA (usa git/cmake/gcc, ya presentes)
bash noise/build-rnnoise-ladspa.sh

# 2) Registrar el filtro en PipeWire y preparar el toggle
bash noise/install.sh
```

## Uso

```bash
# Prender (aparece la fuente "Speak Clean Mic")
systemctl --user start filter-chain.service

# Arranque automático al iniciar sesión
systemctl --user enable filter-chain.service

# Apagar
systemctl --user stop filter-chain.service

# Verificar
wpctl status | grep -i "Speak Clean Mic"

# Ponerlo como micrófono por defecto (Chrome/Zoom lo toman solos)
wpctl set-default "$(pactl list sources short | awk '/speak_clean_mic/{print $1; exit}')"
```

Desde la **GUI de Speak** (pestaña *Checker*) el mismo toggle y el "usar por defecto" están a
un clic.

## Archivos

| Archivo | Rol |
|---|---|
| `build-rnnoise-ladspa.sh` | Compila e instala `librnnoise_ladspa.so` en `~/.local/lib/ladspa/` (idempotente; `--force` recompila). |
| `speak-clean-mic.conf` | Fragmento de filter-chain: micro físico → RNNoise → fuente `speak_clean_mic`. |
| `install.sh` | Enlaza el fragmento a `~/.config/pipewire/filter-chain.conf.d/` y exporta `LADSPA_PATH` a `filter-chain.service` (drop-in de systemd). |

## Ajustes

- **Otro micrófono**: edita `target.object` en `speak-clean-mic.conf` (lista con
  `pactl list sources short`). Por defecto apunta al condensador V8, igual que `capture/record.sh`.
- **Agresividad**: sube/baja `"VAD Threshold (%)"` (0–100). Más alto = corta más lo que no es voz.
- **Mono vs estéreo**: si tu micro es mono, cambia `noise_suppressor_stereo` por
  `noise_suppressor_mono` y `audio.position` a `[ MONO ]`.

## Notas y trade-offs

- `filter-chain.service` (la unit que trae Fedora) carga **todos** los fragmentos en
  `filter-chain.conf.d/`; si tienes otros, se activan/desactivan juntos.
- Corre como instancia cliente de PipeWire **aparte** del daemon principal: prender/apagar el
  filtro **no** interrumpe el resto del audio del sistema.
- Tras editar `speak-clean-mic.conf`, aplica cambios con
  `systemctl --user restart filter-chain.service`.
