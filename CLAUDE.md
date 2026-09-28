# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Visão geral

Chatly: atendimento de WhatsApp com IA. Backend Django 6 (Python 3.13, gerenciado com `uv`) expondo API REST com **django-ninja-extra** + **django-ninja-jwt** (JWT global). Frontend Nuxt 4 + Nuxt UI (SPA, `ssr: false`). Banco PostgreSQL com pgvector (RAG), Redis + Celery (tasks), MinIO próprio e privado (mídia, URL assinada). Em produção desde 2026-07-16 em https://app.chatly.ia.br.

Dois canais de WhatsApp, deliberadamente separados:
- **Oficial** (`apps/chats`): Cloud API da Meta, com IA, RAG, memória, campanhas e templates. UI em `pages/conversas.vue`.
- **Pessoal** (`apps/personal`): número pessoal do atendente via gateway **OpenWA** (não-oficial, engine baileys, SQLite no próprio volume), 1:1 manual, **sem IA e sem campanhas**. UI em `pages/meu-whatsapp.vue` + bolha flutuante `AppChatWidget.vue`.

## Comandos

Backend, de dentro de `backend/` (dependências em `pyproject.toml`/`uv.lock`, não há `requirements.txt`):

```bash
uv sync                                   # instala/atualiza dependências
uv run manage.py runserver                # dev server (API em /api/, docs em /api/docs)
uv run manage.py makemigrations           # gerar migrations
uv run manage.py migrate                  # aplicar migrations
uv run manage.py test                     # suíte inteira
uv run manage.py test apps.personal       # testes de um app
uv run manage.py test apps.chats.tests.MinhaClasse.test_x   # um teste
```

Frontend, de dentro de `frontend/` (usa **npm**, `package-lock.json`; o campo `packageManager: pnpm` no `package.json` é decorativo):

```bash
npm run dev          # dev server
npm run lint         # eslint
npm run typecheck    # nuxt typecheck
npm run build        # em produção o Dockerfile usa `nuxt generate`, não `build`
```

Stack local via `docker compose up -d` na raiz: `db`, `redis`, `openwa`, `minio` (console em http://localhost:9001), `api` (bind mount `./backend:/app`, roda `migrate` + `runserver` com autoreload) e `worker` (celery, sem autoreload: mexeu em `tasks.py`, `docker compose restart worker`). OpenWA de dev expõe dashboard + Swagger em http://localhost:2785 (`/api/docs`). Dependência nova no backend: `docker compose up -d --build -V api worker` (o volume anônimo `/app/.venv` guarda o venv velho).

Não há linter/formatter no backend. Dev deps incluem `django-stubs`/`django-types`.

## Configuração

Settings lidos do `.env` via `python-decouple`. **`config(...)` sem default quebra no import do settings** - toda var nova precisa entrar no `.env` de cada ambiente (inclusive `/opt/chatly/.env` no servidor, que não é versionado) **antes** do deploy, senão `api` e `worker` entram em restart loop e o site inteiro cai.

Obrigatórias: `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS` (CSV), `POSTGRES_*`, `FIELD_ENCRYPTION_KEY`, `REDIS_URL`, `CELERY_BROKER_URL`, `MINIO_*` (`MINIO_ENDPOINT_URL` interno, `MINIO_PUBLIC_URL` onde o navegador chega, bucket `media`) e `OPENWA_API_URL` / `OPENWA_API_KEY` (em prod também semeia a chave do OpenWA via `API_MASTER_KEY`, mín. 32 caracteres) / `OPENWA_WEBHOOK_SECRET` (HMAC do webhook) / `OPENWA_WEBHOOK_URL` (URL interna, `http://api:8000/api/personal/webhook`).

## Arquitetura

Separação em camadas que todo código novo deve seguir:

- **`core/`** - configuração. `settings.py`, `urls.py`, `celery.py`, `exceptions.py` (`DomainError` e filhas: `NotFound` 404, `ValidationError` 400, `Forbidden` 403, `Conflict` 409, `GatewayError` 502), `fields.py` (`EncryptedTextField`).
- **`api/`** - camada HTTP. `api.py` cria a instância única `NinjaExtraAPI` e registra os routers de `api/endpoints/`; todo router novo precisa de `api.add_router(...)` lá. Endpoint só parseia payload, resolve a company e delega pro service.
- **`apps/<dominio>/`** - um app por domínio: `accounts`, `companies`, `agents`, `chats`, `crm`, `personal`, `automations` (+ `apps/ai.py`, wrapper do Agno).
  - `models.py` ORM · `schemas.py` DTOs Ninja/pydantic · `services.py` regra de negócio como funções de módulo · `tasks.py` Celery (`@shared_task`) · `<boundary>.py` cliente HTTP da API externa (`whatsapp.py`, `personal/gateway.py`, `pipedrive.py`)
  - **boundary é a única camada que fala HTTP com terceiro**; services chamam o boundary, nunca `httpx` direto.
- **`frontend/app/`** - `pages/` (rotas), `components/`, `composables/` (`useApi`, `useCompany`, `usePersonalChat`, `useChatEvents`...), `layouts/default.vue` (nav + SSE + bolha de chat), `types/api.ts` (espelha os DTOs do backend).

## Convenções importantes

- **User customizado**: `AUTH_USER_MODEL = 'accounts.User'`, login por **email** (sem `username`). Sempre `get_user_model()`.
- **JWT**: access 30 min, refresh 7 dias. Auth global na API; público (webhooks) precisa de `auth=None` explícito.
- **Multi-tenant**: quase todo endpoint recebe `company_id` e resolve com `companies_services.get_company(request.auth, company_id)` (membro) ou `get_owned_company(...)` (dono). Nunca filtrar recurso sem escopo de company.
- **Erros de domínio**: levantar as exceções de `core.exceptions` nos services; o handler em `api/api.py` traduz pra `{"detail": ...}` com o status certo. Nada de `HttpResponse` de erro no endpoint.
- **Canal pessoal (OpenWA)**: o gateway escolhe o id da mensagem e só devolve na resposta do envio; o eco `message.sent` pode chegar antes. Envio grava com `update_or_create(wa_message_id=...)` e webhook com `get_or_create`, nunca `create`. Webhook confere `X-OpenWA-Signature` (`sha256=` + HMAC hex do corpo cru) antes de parsear.
- **Mídia é privada**: `core.storage.PrivateMediaStorage` assina a URL com `MINIO_PUBLIC_URL` (a assinatura cobre host e caminho, por isso não serve o endereço interno). Nada de `default_acl='public-read'` nem URL montada na mão: sempre `field.url`.
- **Nunca fazer `filter()` por `EncryptedTextField`**: Fernet tem IV aleatório, a cifra não é determinística e a igualdade nunca casa (já causou um bug que matou o pipeline de webhook inteiro).
- **SSE**: `apps/chats/events.publish(company_id, payload)` é o canal único; eventos do canal pessoal levam `channel: 'personal'` + `account_id` pra não colidir com os IDs da tabela oficial.
- Apps locais entram em `INSTALLED_APPS` como `'apps.<nome>'` e o `AppConfig.name` deve ser `'apps.<nome>'`.
- **Sem travessão** (—) em código, UI ou docs: usar hífen, vírgula ou dois pontos. Placeholder de nome vazio é "Sem nome".

## Deploy

Push em qualquer branch roda `.github/workflows/ci.yml` (testes do backend com Postgres/Redis/MinIO reais + `nuxt generate`). Na `main`, o deploy só roda se os dois passarem: ssh → `git reset --hard origin/main` → build com `IMAGE_TAG=<sha>` → `up -d` → `caddy reload`. **O servidor espelha a main: nada de editar arquivo versionado direto lá** (o Caddyfile inclusive, que também serve o `pipe.chatly.ia.br`). Rollback: `IMAGE_TAG=<sha antigo> docker compose -f docker-compose.prod.yaml up -d api worker`. Serviços: `caddy` (SPA estática + proxy `/api` + `/media` pro MinIO), `api` (gunicorn, com healthcheck), `worker` (celery, `--concurrency 2`, sobe depois da api saudável), `db`, `redis`, `openwa`, `minio` (sem porta publicada), `backup` (`pg_dump` diário em `/opt/chatly/backups`, 7 dias; como restaurar em `deploy/backup.sh`). Antes do push, `docker compose -f docker-compose.prod.yaml build` local para pegar erro de build cedo.
