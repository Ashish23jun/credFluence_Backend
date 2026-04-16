# CredFluence Backend

FastAPI backend for the CredFluence trust/review platform.

## Quick Start

```bash
# Copy env file
cp .env.example .env

# Start all services
docker-compose up

# API docs
open http://localhost:8000/docs

# MailHog (email)
open http://localhost:8025

# MinIO (storage)
open http://localhost:9001  # user: minioadmin / minioadmin
```

## Run migrations

```bash
# With Docker running Postgres:
alembic upgrade head
```

## Dev without Docker

```bash
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
uvicorn app.main:app --reload
```

## Project structure

```
app/
├── main.py           # FastAPI app, middleware, routers
├── core/
│   ├── config.py     # Settings (pydantic-settings)
│   ├── database.py   # SQLAlchemy async engine + session
│   ├── security.py   # JWT RS256, bcrypt, AES-256-GCM
│   ├── dependencies.py  # get_current_user, require_business_access
│   ├── middleware.py  # Rate limit, logging, error handler
│   ├── redis.py      # Redis client
│   └── logging.py    # structlog config
├── models/           # SQLAlchemy models (9 tables)
├── schemas/          # Pydantic v2 request/response schemas
├── routers/          # FastAPI routers (auth, profiles, reviews, ...)
├── services/         # Business logic
└── tasks/            # Celery tasks
migrations/           # Alembic migrations
tests/
```
