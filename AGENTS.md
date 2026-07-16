# AGENTS.md

Monorepo: `backend/` (Django 6 + Python 3.13, `uv`) and `frontend/` (Nuxt 4 + pnpm). Root `.env` is a symlink to `backend/.env`. Note: the older `CLAUDE.md` at root is stale (describes no frontend, single app, missing migrations) — trust this file.

## Backend

All commands run from `backend/` via `uv` (no `requirements.txt`; deps in `pyproject.toml`/`uv.lock`):

```bash
uv sync                                                          # install/deps
uv run manage.py runserver                                       # dev API on :8000 (docs at /api/docs)
uv run manage.py makemigrations && uv run manage.py migrate      # migrations
uv run manage.py test                                            # all tests
uv run manage.py test apps.accounts                              # one app
uv run manage.py test apps.accounts.tests.LoginTests.test_login_success  # one test
```

No project-level linter/formatter is wired into a script. `ruff` is configured in `pyproject.toml` (`line-length = 150`) but has no run alias — invoke `uv run ruff check .` manually if needed. Dev deps include `django-stubs`/`django-types` only; there is no `mypy` task.

### Infra prerequisites

`docker-compose.yaml` runs PostgreSQL (pgvector/pg17) and Redis. Start it before `runserver`/tests that touch the DB:

```bash
docker compose up -d
```

Postgres must be `pgvector`-enabled — `apps.agents` uses `pgvector.django.VectorField(dimensions=1536)` for `DocumentChunk.embedding`. Tests inherit the real DB config from `backend/.env` (Django `TestCase` without `--keepdb` will drop/create); there is no separate test DB or mock.

### Required env (`backend/.env`, read via `python-decouple`)

Missing any of these breaks Django import-time: `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS` (CSV), `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT`, `MINIO_ENDPOINT_URL`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_BUCKET_NAME`, `FIELD_ENCRYPTION_KEY` (Fernet key for `EncryptedTextField`), `GROQ_API_KEY`, `EMBEDDING_API_KEY`. Optional with defaults: `CORS_ALLOWED_ORIGINS`, `REDIS_URL`, `CELERY_BROKER_URL`, `EMAIL_*`.

### Architecture (layering — do not bypass)

- `core/` — settings, urls, celery, `exceptions.py` (`DomainError` + subclasses mapped to HTTP status by a handler in `api/api.py`), `fields.py` (`EncryptedTextField` via Fernet).
- `api/api.py` — single `NinjaExtraAPI(title="Chatly", auth=JWTAuth())`. **Auth is global**; public endpoints override with `auth=None`. Routers live in `api/endpoints/` and must be registered here with `api.add_router(...)`.
- `api/endpoints/*` — HTTP layer only: parse payload, delegate to `apps/<domain>/services.py`, return. Do not put business logic here.
- `apps/<domain>/` — one Django app per domain. Required internal pattern (replicate for new apps): `models.py`, `schemas.py` (ninja `Schema`/`ModelSchema`), `services.py` (module-level functions; the source of truth for business rules), `tasks.py` (Celery `shared_task`), `tests.py`, `apps.py` (with `name='apps.<nome>'`), `migrations/`.
- `apps/ai.py` is a **shared module** (not an app): LLM/embeddings/transcription (Agno + OpenAI/Anthropic + Groq). Called from `apps/chats/tasks.py`.
- Current apps: `accounts` (User, auth), `companies` (`Company` owner-scoping), `agents` (`Agent`, `Credential`, `Document`/`DocumentChunk` RAG), `chats` (`WhatsAppAccount`, `Contact`, `Message`, `Sticker`, `Campaign` + `whatsapp.py` Meta client + `events.py` Redis pubsub).

### Conventions that bite

- `AUTH_USER_MODEL = 'accounts.User'` — login by **email**, no `username`, custom `UserManager`. Never import `User` at module top-level in code that runs during import; use `get_user_model()` or reference via `settings.AUTH_USER_MODEL` in FKs.
- New apps must be added to `INSTALLED_APPS` as `'apps.<nome>'` and `AppConfig.name` must match.
- Multi-tenant scoping: almost every `companies/...` service takes `owner=request.auth` or `company=Company` and filters by it. Endpoints in `api/endpoints/*` resolve the `Company` via `companies_services.get_company(request.auth, company_id)` before delegating — follow this.
- Multiple routers share the `/companies` prefix in `api/api.py`; order is preserved by `add_router` calls. Keep company-scoped endpoints under `/companies`.
- `EncryptedTextField` (Fernet) is used for `Credential.api_key`, `WhatsAppAccount.access_token`/`app_secret`. Changing the Fernet key invalidates all existing values. Never log decrypted values.
- WhatsApp webhook (`/api/whatsapp/webhook`) is public (`auth=None`) and verifies `X-Hub-Signature-256` HMAC with `account.app_secret`. Verification GET uses `verify_token`. Both are stored encrypted.
- Celery: `core/celery.py` (`autodiscover_tasks`); tasks in `apps/*/tasks.py` use `@shared_task(bind=True, max_retries=3)` with `countdown=2**self.request.retries`. `runserver` does **not** run workers — start one with `uv run celery -A core worker -l info` for webhook-driven flows and campaigns. Redis is also the pubsub bus for `apps/chats/events.py` (uses `CELERY_BROKER_URL`, channel `chatly:events:<company_id>`).
- Media files are served by S3/MinIO (`django-storages` `S3Boto3Storage`, `querystring_auth=False`, `file_overwrite=False`) in dev and prod; `MEDIA_ROOT`/`MEDIA_URL` static serving is only added in `DEBUG`.

### Tests

Django `TestCase` (no pytest). Pattern is `apps/<domain>/tests.py` with one class per feature group; HTTP tests use `self.client` with `content_type='application/json'`. For authenticated calls, `apps/testing.py` exposes `auth_header(user)` returning `{'Authorization': 'Bearer <access>'}` (issues a real JWT). Celery tasks are typically mocked at the service boundary (e.g. `@patch('apps.accounts.services.send_reset_email_task')`), not `apply_async` patches.

## Frontend

From `frontend/` (pnpm; `packageManager: pnpm@11.9.0`):

```bash
pnpm install      # also runs `nuxt prepare` postinstall
pnpm run dev      # dev server
pnpm run build
pnpm run lint     # eslint (flat config, .nuxt/eslint.config.mjs base)
pnpm run typecheck # vue-tsc via `nuxt typecheck`
```

CI (`.github/workflows/ci.yml`) runs `pnpm run lint` then `pnpm run typecheck` on Node 22 — this is the required `lint -> typecheck` order; there is no test step for the frontend.

- Nuxt 4 with `@nuxt/ui` v4 (Tailwind v4). `ssr: false` (SPA). Theme colors set in `app/app.config.ts`: `primary: emerald`, `neutral: zinc`.
- API base comes from `runtimeConfig.public.apiBase` (`http://localhost:8000/api`); always go through `apps/composables/useApi.ts` (`useApi()`) — it attaches the JWT bearer, refreshes once on 401, and logs out if refresh fails. Error `detail` is extracted via `apiErrorDetail()`.
- ESLint stylistic overrides (from `nuxt.config.ts`): `commaDangle: 'never'`, `braceStyle: '1tbs'` — do not reformat files to trailing commas.
- Auth state is in `composables/useAuth.ts`; SSE/Redis-event listener in `composables/useEventStream.ts` + `useChatEvents.ts`.