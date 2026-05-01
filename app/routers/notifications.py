import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_delete, cache_get, cache_set
from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.models.notification import Notification
from app.models.review import Review, ReviewEvidence
from app.services.storage_service import presign_get

router = APIRouter(prefix="/notifications", tags=["notifications"])

_UNREAD_COUNT_TTL = 60  # seconds


def _unread_key(user_id: str) -> str:
    return f"notif:unread:{user_id}"


def _notif_serialise(n: Notification) -> dict:
    extra = dict(n.extra_data) if n.extra_data else {}
    # Enrich evidence items with fresh presigned download URLs
    if extra.get("evidence"):
        extra["evidence"] = [
            {**e, "url": presign_get(e.get("file_key"), expires=3600)}
            for e in extra["evidence"]
        ]
    return {
        "id": str(n.id),
        "type": n.notification_type,
        "title": n.title,
        "body": n.body,
        "extra_data": extra,
        "is_read": n.is_read,
        "read_at": n.read_at.isoformat() if n.read_at else None,
        "created_at": n.created_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# GET /notifications
# ---------------------------------------------------------------------------

@router.get("")
async def list_notifications(
    limit: int = Query(default=20, ge=1, le=100),
    before: str | None = Query(default=None, description="ISO datetime cursor — fetch items older than this"),
    unread_only: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
) -> dict:
    user_id = uuid.UUID(current_user["id"])

    stmt = select(Notification).where(Notification.user_id == user_id)
    if unread_only:
        stmt = stmt.where(Notification.is_read == False)  # noqa: E712
    if before:
        try:
            cursor_dt = datetime.fromisoformat(before)
            if cursor_dt.tzinfo is None:
                cursor_dt = cursor_dt.replace(tzinfo=UTC)
            stmt = stmt.where(Notification.created_at < cursor_dt)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid cursor format — use ISO datetime")

    stmt = stmt.order_by(Notification.created_at.desc()).limit(limit)
    result = await db.execute(stmt)
    notifications = result.scalars().all()

    # For old notifications that have evidence_count but no evidence array,
    # fetch files from DB and patch extra_data in-place (not persisted).
    legacy = [
        n for n in notifications
        if n.extra_data
        and n.extra_data.get("evidence_count", 0) > 0
        and not n.extra_data.get("evidence")
        and n.extra_data.get("review_id")
    ]
    if legacy:
        review_ids = [uuid.UUID(n.extra_data["review_id"]) for n in legacy]
        ev_rows = (await db.execute(
            select(ReviewEvidence).where(ReviewEvidence.review_id.in_(review_ids))
        )).scalars().all()
        ev_by_review: dict[str, list] = {}
        for ev in ev_rows:
            ev_by_review.setdefault(str(ev.review_id), []).append({
                "id": str(ev.id),
                "type": ev.type,
                "filename": ev.file_key.rsplit("/", 1)[-1],
                "file_key": ev.file_key,
            })
        for n in legacy:
            rid = n.extra_data["review_id"]
            if rid in ev_by_review:
                n.extra_data = {**n.extra_data, "evidence": ev_by_review[rid]}

    # next_cursor is the created_at of the last item if a full page was returned
    next_cursor = notifications[-1].created_at.isoformat() if len(notifications) == limit else None

    # Unread count (cached)
    cached = await cache_get(_unread_key(str(user_id)))
    if cached is not None:
        unread_count = cached
    else:
        count_result = await db.execute(
            select(func.count()).where(
                Notification.user_id == user_id,
                Notification.is_read == False,  # noqa: E712
            )
        )
        unread_count = count_result.scalar_one()
        await cache_set(_unread_key(str(user_id)), unread_count, _UNREAD_COUNT_TTL)

    return {
        "success": True,
        "message": "Notifications fetched.",
        "data": {
            "notifications": [_notif_serialise(n) for n in notifications],
            "unread_count": unread_count,
            "next_cursor": next_cursor,
            "has_more": next_cursor is not None,
        },
    }


# ---------------------------------------------------------------------------
# GET /notifications/unread-count
# ---------------------------------------------------------------------------

@router.get("/unread-count")
async def get_unread_count(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
) -> dict:
    user_id = str(current_user["id"])

    cached = await cache_get(_unread_key(user_id))
    if cached is not None:
        return {"success": True, "message": "OK", "data": {"unread_count": cached}}

    result = await db.execute(
        select(func.count()).where(
            Notification.user_id == uuid.UUID(user_id),
            Notification.is_read == False,  # noqa: E712
        )
    )
    count = result.scalar_one()
    await cache_set(_unread_key(user_id), count, _UNREAD_COUNT_TTL)

    return {"success": True, "message": "OK", "data": {"unread_count": count}}


# ---------------------------------------------------------------------------
# PATCH /notifications/{notification_id}/read
# ---------------------------------------------------------------------------

@router.patch("/{notification_id}/read")
async def mark_read(
    notification_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
) -> dict:
    user_id = uuid.UUID(current_user["id"])

    notification = await db.get(Notification, notification_id)
    if not notification or notification.user_id != user_id:
        raise HTTPException(status_code=404, detail="Notification not found")

    if not notification.is_read:
        notification.is_read = True
        notification.read_at = datetime.now(UTC)
        await db.commit()
        await cache_delete(_unread_key(str(user_id)))

    return {
        "success": True,
        "message": "Marked as read.",
        "data": _notif_serialise(notification),
    }


# ---------------------------------------------------------------------------
# POST /notifications/read-all
# ---------------------------------------------------------------------------

@router.post("/read-all")
async def mark_all_read(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
) -> dict:
    user_id = uuid.UUID(current_user["id"])
    now = datetime.now(UTC)

    await db.execute(
        update(Notification)
        .where(Notification.user_id == user_id, Notification.is_read == False)  # noqa: E712
        .values(is_read=True, read_at=now)
    )
    await db.commit()
    await cache_delete(_unread_key(str(user_id)))

    return {"success": True, "message": "All notifications marked as read.", "data": None}
