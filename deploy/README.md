# Despliegue de spik (modo servidor) — `spik.hclareth.local`

Corre spik detrás del **Traefik** local (ingress/reverse-proxy) en `https://spik.hclareth.local`,
en **modo servidor**: sirve **Analizar + Feedback + Historial**. La **captura** (cámara, micro,
filtro de ruido) **no** va en el contenedor — necesita `/dev/video*`, PipeWire y `systemctl
--user`, que un contenedor rootless no tiene. Eso sigue siendo la app local (`python -m web.main`
en `127.0.0.1`).

Glosario: **ADC** = Application Default Credentials (auth de gcloud sin API key). **GHCR** =
GitHub Container Registry. **Traefik** = reverse-proxy/ingress que enruta por dominio y termina TLS.

## Arquitectura

```
  Navegador ──https──> Traefik (:443, cert *.hclareth.local) ──http──> contenedor spik (:8000)
                                                                          │
                                          ./data (grabaciones + SQLite + caché de modelos)
                                          ADC de gcloud (RO) ──> Vertex AI (solo texto/métricas)
```

Privacidad ("todo local"): el puerto del contenedor **no** se publica al host (solo lo alcanza
Traefik por la red `traefik-net`). El video/audio nunca sale de la máquina; a Vertex solo va texto.

## Requisitos previos

1. **Traefik** corriendo con la red externa `traefik-net` y el cert wildcard `*.hclareth.local`
   (ya existe en esta máquina; no hace falta cert nuevo).

2. **DNS local** — no hay wildcard `*.hclareth.local`, así que añade la entrada explícita a
   `/etc/hosts` (requiere sudo):

   ```bash
   echo "127.0.0.1 spik.hclareth.local" | sudo tee -a /etc/hosts
   ```

3. **ADC de gcloud** (para el feedback vía Vertex):

   ```bash
   gcloud auth application-default login
   ```

4. **`.env`** en la raíz del repo con al menos `SPIK_VERTEX_PROJECT` (y opcionalmente
   `SPIK_VERTEX_REGION`, `SPIK_CLAUDE_MODEL`). Ver `.env.example`. **Nunca** se commitea.

## Levantar

Desde la **raíz del repo**:

```bash
# Opción A: usar la imagen publicada en GHCR (privada)
podman login ghcr.io            # usuario hclareth7 + token de GitHub con read:packages
podman-compose -f deploy/docker-compose.yaml up -d

# Opción B: construir localmente
podman-compose -f deploy/docker-compose.yaml build
podman-compose -f deploy/docker-compose.yaml up -d
```

Abre <https://spik.hclareth.local>.

> **Primera transcripción:** WhisperX descarga el modelo `medium` (~1.5 GB) a `./data/.cache`
> la primera vez. Persiste en el volumen, así que solo ocurre una vez.

> **Frontend en la imagen:** el `Dockerfile` compila la UI de React/Vite en un **stage
> multi-etapa** (`FROM node:24-slim AS frontend` → `npm ci` + `npm run build`) y copia el
> resultado (`web/dist`) al runtime. **Node solo existe en build**, no en la imagen final.
> No hace falta compilar el frontend a mano antes del build de la imagen: `podman build` lo
> hace dentro. Las fuentes van vendorizadas en el bundle (sin CDN). Para desarrollo con
> hot-reload usa `cd frontend && npm run dev` (proxy de `/api` y `/video` a `:8000`); ver
> `web/README.md`.

## Notas de configuración

- **Usuario/permisos:** el contenedor corre como tu UID/GID de host (`SPIK_UID`/`SPIK_GID`,
  default 1000) para compartir `./data` con la app de captura local sin conflictos de propiedad
  (rootless podman). La imagen trae un usuario no-root propio para uso genérico; el compose lo
  alinea con el host.
- **SELinux:** los volúmenes usan la etiqueta `:z` (relabel compartido). No se hace `chown` del
  host.
- **Modo:** `SPIK_MODE=server` (fijado en la imagen y en el compose) oculta las pestañas de
  captura y bloquea los endpoints host-only con HTTP 503.

## Operación

```bash
podman-compose -f deploy/docker-compose.yaml logs -f      # logs
podman-compose -f deploy/docker-compose.yaml ps           # estado + healthcheck
podman-compose -f deploy/docker-compose.yaml down         # parar
```

El healthcheck consulta `GET /api/config` (no toca hardware ni Vertex).

## Compartir con amigos (modo appliance)

El **modo servidor** de arriba oculta la captura a propósito. Si lo que quieres es **empaquetar
spik completo** (checker/preview + grabar + análisis + feedback) y dárselo a un amigo **sin
dolor**, usa el **modo appliance**: un contenedor **privilegiado** que sí toca la cámara, el micro
y la CPU del host. Todo se guarda en el dispositivo del amigo; a Claude solo va texto/métricas.

Glosario: **appliance** = "aparato llave en mano" (todo dentro de un contenedor). **PipeWire /
PulseAudio** = el sistema de audio del escritorio Linux; el contenedor usa su socket para el micro.

### Requisitos del amigo

- **Linux de escritorio** con **PipeWire** o **PulseAudio** (para cámara y micro). Mac/Windows
  quedan para una fase futura: el contenedor corre en una VM y no ve la cámara/micro nativos.
- **podman** (recomendado, rootless) o **docker**.
- Una **`ANTHROPIC_API_KEY`** propia si quiere el feedback de Claude (opcional; ver abajo).

### Poner la API key

En la raíz del repo, copiar `.env.example` a `.env` y dejar:

```bash
SPIK_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
```

Cada amigo paga **su propio** feedback con su clave. **Sin** clave, el contenedor corre igual pero
**solo métricas locales** (transcripción + WPM/muletillas/pausas, $0). La `.env` **nunca** se
commitea.

### Levantar

Desde la **raíz del repo**:

```bash
# Opción A: usar la imagen publicada en GHCR (privada; requiere login)
podman login ghcr.io            # usuario hclareth7 + token con read:packages
podman-compose -f deploy/docker-compose.share.yaml up -d

# Opción B: construir localmente
podman-compose -f deploy/docker-compose.share.yaml build
podman-compose -f deploy/docker-compose.share.yaml up -d
```

Abre <http://localhost:8000> (sin Traefik ni dominio).

> **Privacidad ("todo local"):** el puerto se publica **solo en `127.0.0.1`** (loopback, no en la
> LAN). Las grabaciones (`.mkv`/`.wav`) se quedan en `./data`. A Claude solo va texto/métricas.

### Notas y caveats

- **Privilegios:** el compose usa `privileged: true` para llegar a `/dev/video*` (cámara) y
  `/dev/dri` (GPU). Es amplio; aceptado para uso local entre amigos. Endurecimiento futuro:
  cambiar por `devices:` explícitos + grupos `video`/`render` en vez de privilegios totales.
- **podman rootless (recomendado):** el compose **no** fija `user:`, así que el "root" del
  contenedor mapea a **tu usuario del host**; puede leer/escribir `./data` y el socket de audio
  sin conflictos de propiedad, y sin privilegio real sobre el host.
- **docker rootful (caveat):** ahí el "root" del contenedor **es root real**. Los archivos de
  `./data` quedarán como `root:root` (usa `sudo` para borrarlos) y puede que necesites ajustar
  permisos del socket de PulseAudio. En rootful, considera `--user "$(id -u):$(id -g)"`.
- **Audio:** el compose monta `${XDG_RUNTIME_DIR}/pulse/native` en el contenedor y fija
  `PULSE_SERVER`. Requiere un host con PipeWire/PulseAudio activo (estándar en escritorio Linux).

## Publicar la imagen en GHCR

La imagen se publica en `ghcr.io/hclareth7/spik` (**privada**). Hay dos vías:

- **Manual (local):**

  ```bash
  podman build -t ghcr.io/hclareth7/spik:latest .
  echo "$GH_TOKEN" | podman login ghcr.io -u hclareth7 --password-stdin
  podman push ghcr.io/hclareth7/spik:latest
  ```

- **GitOps (CI):** `.github/workflows/publish.yml` construye y publica en cada push a `main`
  (y por tag `v*`). Usa el `GITHUB_TOKEN` del repo con permiso `packages: write`. El paquete
  nace privado; hazlo público solo si lo decides explícitamente en GHCR.
