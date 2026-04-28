# Deploy MiniDash — Backend en VPS de laburo

## Arquitectura

```
[ usuario ]
     │
     ├── https://minidash.facundo.click            → Render Static (frontend/)
     │       (CNAME → mini-dash.onrender.com)
     │
     └── https://api-minidash.facundo.click        → VPS de laburo (master.facundo.click)
             (CNAME → master.facundo.click)
                  │
                  └── Traefik → mini-dash-api (Docker Swarm) → :8000 (FastAPI)
                                                                     │
                                                                     └── postgres_postgres (DB endado)
```

## Pre-flight (en local)

Antes de tocar el VPS, completar `deploy/.env` (copiado de `.env.production.example`):

```bash
cp deploy/.env.production.example deploy/.env
# Editar deploy/.env con DB_PASSWORD, JWT_SECRET, INTERNAL_SECRET, GOOGLE_CLIENT_ID
```

> `.env` esta en `.gitignore`, no entra al repo.

## Deploy (primera vez)

Asuncion: el VPS de laburo (`master.facundo.click`) ya tiene Docker Swarm + Traefik
corriendo, y conoces:

- Red de Traefik (default asumido: `traefik-public`).
- Cert resolver (default asumido: `letsencrypt`).
- Red de Postgres (default asumido: `postgres`; ajustar en `docker-stack.yml`).

### 1. Sync el repo al VPS

Desde el Mac:

```bash
# Reemplazar <user> y <ssh-host> con los datos del VPS de laburo
rsync -avz --delete \
  --exclude '.git/' --exclude '.venv/' --exclude '__pycache__/' \
  ./ <user>@master.facundo.click:/opt/mini-dash/
```

Alternativa: `git clone` directo en el VPS (repo es publico):

```bash
ssh <user>@master.facundo.click 'cd /opt && git clone https://github.com/facundozupel/mini-dash.git mini-dash'
```

### 2. Subir el `.env` al VPS

`.env` no esta en el repo. Hay que copiarlo aparte:

```bash
scp deploy/.env <user>@master.facundo.click:/opt/mini-dash/deploy/.env
```

### 3. Build de la imagen en el VPS

```bash
ssh <user>@master.facundo.click '
  cd /opt/mini-dash/backend
  docker build -t mini-dash-api:latest .
'
```

### 4. Verificar redes externas

```bash
ssh <user>@master.facundo.click 'docker network ls | grep -E "traefik|postgres"'
```

Si los nombres no son `traefik-public` y `postgres`, editar `deploy/docker-stack.yml`
ANTES del paso 5 y volver a sync.

### 5. Deploy del stack

```bash
ssh <user>@master.facundo.click '
  cd /opt/mini-dash/deploy
  docker stack deploy -c docker-stack.yml --with-registry-auth mini-dash
'
```

### 6. Verificar

```bash
# Servicio arriba?
ssh <user>@master.facundo.click 'docker stack services mini-dash'

# Logs en vivo
ssh <user>@master.facundo.click 'docker service logs -f mini-dash_api'

# Health desde fuera (debe responder ok despues de ~30s + cert issuance)
curl -fsSL https://api-minidash.facundo.click/health
```

Esperado: `{"status":"ok","db":true}`.

Despues recargar `https://minidash.facundo.click` — los KPIs deberian poblarse.

## Updates posteriores

```bash
# Local
git push

# VPS
ssh <user>@master.facundo.click '
  cd /opt/mini-dash && git pull &&
  cd backend && docker build -t mini-dash-api:latest . &&
  docker service update --image mini-dash-api:latest --force mini-dash_api
'
```

`update_config: order: start-first` en el stack hace zero-downtime: levanta el nuevo
container, espera healthcheck, despues mata el viejo.

> **Gotcha**: con imagen local (sin registry) NO uses `docker stack deploy` para
> updates — Swarm pinnea por digest del primer deploy y no detecta cambios en `:latest`
> aunque rebuild. Usar `docker service update --image ... --force` siempre. `stack deploy`
> solo para cambios al `docker-stack.yml` (labels, networks, replicas, env vars).

## Troubleshooting

| Sintoma | Causa probable | Fix |
|---|---|---|
| `404` Traefik en `api-minidash.facundo.click` | Stack no deployado o labels mal | `docker stack services mini-dash`, revisar labels |
| `502` o `503` | Healthcheck fallando | `docker service logs mini-dash_api` — error de DB o startup |
| `Cannot resolve postgres_postgres` | API no esta en la red de postgres | Verificar nombre real de la red en `docker-stack.yml` |
| Cert no se emite | Cloudflare en proxy mode (naranja) | Ponerlo en DNS only (gris) |
| CORS error en browser | `FRONTEND_ORIGIN` mal en `.env` | Tiene que ser `https://minidash.facundo.click` exacto |
