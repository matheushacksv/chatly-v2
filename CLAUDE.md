# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Visão geral

Backend Django 6 (Python 3.13) em estágio inicial, gerenciado com `uv`. API REST construída com **django-ninja-extra** + **django-ninja-jwt** (JWT auth global). Banco: PostgreSQL. Só existe o app `accounts` por enquanto; partes do código estão incompletas (ex.: `apps/accounts/services.py` tem `register_user` sem corpo, e `apps/accounts/migrations/` ainda não tem a migration inicial do `User` customizado).

Não há frontend ainda - o repositório contém apenas `backend/`.

## Comandos

Todos rodam de dentro de `backend/`, via `uv` (dependências em `pyproject.toml`/`uv.lock`, não há `requirements.txt`):

```bash
uv sync                                  # instala/atualiza dependências
uv run manage.py runserver               # dev server (API em /api/, docs em /api/docs)
uv run manage.py makemigrations          # gerar migrations
uv run manage.py migrate                 # aplicar migrations
uv run manage.py test                    # todos os testes
uv run manage.py test apps.accounts      # testes de um app
uv run manage.py test apps.accounts.tests.MinhaClasse.test_x  # um teste
```

Não há linter/formatter configurado. Dev deps incluem `django-stubs`/`django-types` (type hints do Django).

## Configuração

Settings lidos do `backend/.env` via `python-decouple` (`config(...)`). Chaves obrigatórias - ausência quebra no import do settings: `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS` (CSV), `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT`.

## Arquitetura

Separação em camadas que todo código novo deve seguir:

- **`core/`** - configuração do projeto. `settings.py`, `urls.py` (monta `admin/` e `api/`), `wsgi.py`/`asgi.py`.
- **`api/`** - camada HTTP. `api.py` cria a instância única `NinjaExtraAPI` (título "Chatly", `auth=JWTAuth()` global, registra `NinjaJWTDefaultController` → endpoints de token em `/api/token/*`). `api/endpoints/` (hoje vazio) é onde ficam os routers por domínio; cada router novo precisa ser registrado em `api.py` via `api.add_router(...)`.
- **`apps/<dominio>/`** - um app Django por domínio. Estrutura interna do `accounts` (padrão a replicar):
  - `models.py` - modelos ORM
  - `schemas.py` - DTOs Ninja/pydantic (`Schema`/`ModelSchema`, validação com `@model_validator`)
  - `services.py` - lógica de negócio como funções de módulo; endpoints só parseiam payload e delegam para cá

## Convenções importantes

- **User customizado**: `AUTH_USER_MODEL = 'accounts.User'` - login por **email** (sem `username`), manager próprio (`UserManager`). Sempre use `get_user_model()`, nunca importe `User` direto em código que roda no import.
- **JWT**: access token 30 min, refresh 7 dias (`NINJA_JWT` no settings). Auth é global na API; endpoints públicos precisam sobrescrever `auth` explicitamente.
- Apps locais entram em `INSTALLED_APPS` como `'apps.<nome>'` e o `AppConfig.name` deve ser `'apps.<nome>'`.
