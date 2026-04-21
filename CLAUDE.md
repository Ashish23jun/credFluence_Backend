# CredFluence Backend — Mandatory Rules for Claude

This file is automatically loaded every session. These rules are NON-NEGOTIABLE.
**Read `skills/SKILL.md` before writing ANY backend code.** No exceptions.

---

## Step 0 — Always Do This First

Before writing or editing any backend code:

1. **Read `skills/SKILL.md`** — build status + which skill file to read next
2. **Read the relevant skill file** for your task (see Quick Decision Guide in SKILL.md)
3. **Check the build status table** in SKILL.md — never rewrite what's already built

---

## Build Status (Summary — Full Detail in `skills/SKILL.md`)

### ✅ Built — Do NOT rewrite
- Auth: email register + OTP, login (with role validation), refresh token
- OAuth: Google/YouTube, LinkedIn, Instagram — `mode=signup/login`, role mismatch, `account_not_found`
- Social accounts: `social_accounts` table, all 3 OAuth flows write to it
- Infrastructure: Redis pool, DB pool, shared httpx client, Gunicorn, GZip, rate limiter, structlog

### 🚧 Models exist, no router/service yet
Profiles, Reviews, Disputes, Notifications, Badges, Tag Aggregations, Admin

### ❌ Not started
File upload (S3/R2), AI/OCR pipeline, Celery tasks, score engine

---

## Non-Negotiable Rules

### Architecture
- **Routers** — HTTP parsing + call one service. Zero business logic, zero DB queries.
- **Services** — All logic lives here. No `Request`/`Response` imports.
- **Never** write a DB query inside a router. Move it to the service.

### Code conventions
- **All route handlers are `async def`** — no sync handlers ever
- **Never `os.getenv()`** — all config from `app.core.config.settings`
- **Every endpoint returns** `{"success": bool, "message": str, "data": ...}` — no exceptions
- **Never raw SQL** (`text()`) with string formatting — SQL injection risk
- **Error messages** are user-facing — write them as a real human will read them
- **Routes have NO `/api/v1` prefix** — registered at root level (`/profiles`, `/auth`, etc.)

### Database
- **Every schema change** → new Alembic migration. Never `ALTER TABLE` directly.
- **`trust_score` default is 500** (not 600)
- **Social stats** live in `social_accounts.stats` JSONB, NOT on Profile model
- **`access_level`** (full|limited) lives on Profile, NOT on User

### Redis / Async
- **Shared httpx client** from `app.core.http_client.get_http_client()` (async) — never per-request
- **User auth cache key**: `user:{user_id}` (not `user_auth:`)
- **Rate limit window**: 60s sliding (not 1hr)
- **Never** `GET` then `DELETE` for OAuth state — must be atomic pipeline

### Security
- **`evidence_urls`** never in any public-facing schema
- **Phone numbers** always encrypted (Fernet) — never logged, never returned in responses
- **Ownership checks** before every mutation — never trust the frontend

---

## Quick Skill File Reference

| Task | Read |
|------|------|
| New endpoint | `skills/api-design.md` + `skills/architecture.md` |
| New model or migration | `skills/database.md` |
| Redis, Celery, HTTP calls | `skills/async-patterns.md` |
| Rate limiting, file upload, validation | `skills/security.md` |
| Auth/OAuth internals | `app/routers/auth.py` + `app/routers/oauth.py` directly |
