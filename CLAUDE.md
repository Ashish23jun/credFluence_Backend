# CredFluence Backend

All backend rules and skills live in `../.claude/backend/`.
Read `.claude/backend/SKILL.md` before writing any backend code.

## Step 0 — Always Do This First

1. Read `../.claude/backend/SKILL.md` — build status + which file to read next
2. Read the relevant skill file for your task (see Quick Decision Guide in SKILL.md)
3. Never rewrite what's already marked ✅ in SKILL.md

## Non-Negotiable Rules

- Routes at root level — NO `/api/v1` prefix
- Every endpoint returns `{"success": bool, "message": str, "data": ...}`
- All handlers are `async def` — no sync routes ever
- All config from `app.core.config.settings` — never `os.getenv()`
- Every schema change → new Alembic migration, never `ALTER TABLE`
- `trust_score` default is **450** (not 500, not 600)
- Social stats in `social_accounts.stats`, NOT on Profile
- User auth Redis key: `user:{user_id}` (not `user_auth:`)
- Shared httpx client: `await get_http_client()` from `app.core.http_client`
- No `/api/v1` on any route

## Skill Files (all in `../.claude/backend/`)

| Task | File |
|------|------|
| New endpoint | `api-design.md` + `architecture.md` |
| New model or migration | `database.md` |
| Redis, Celery, HTTP calls | `async-patterns.md` |
| Rate limiting, file upload | `security.md` |
| Auth/OAuth code | `app/routers/auth.py` + `app/routers/oauth.py` directly |
