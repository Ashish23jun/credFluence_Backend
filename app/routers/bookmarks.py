"""
Bookmarks router — authenticated user bookmark management.

POST   /me/bookmarks/{handle}  — toggle bookmark (add/remove)
GET    /me/bookmarks           — list all bookmarked profiles
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.models.profile import Profile
from app.repositories import bookmark_repo, profile_repo
from app.repositories.social_account_repo import get_accounts_by_org_ids
from app.services import profile_service

router = APIRouter(prefix="/me/bookmarks", tags=["bookmarks"])


@router.post("/{handle}")
async def toggle_bookmark(
    handle: str,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    profile = (await db.execute(
        select(Profile).where(Profile.handle == handle, Profile.is_opted_out.is_(False))
    )).scalar_one_or_none()

    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    bookmarked = await bookmark_repo.toggle(db, current_user["id"], profile.id)
    return {"success": True, "message": "bookmarked" if bookmarked else "removed", "data": {"bookmarked": bookmarked}}


@router.get("")
async def list_bookmarks(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    profiles = await bookmark_repo.get_bookmarked_profiles(db, current_user["id"])
    handles = await bookmark_repo.get_bookmarked_handles(db, current_user["id"])

    items = []
    if profiles:
        profile_ids = [p.id for p in profiles]
        org_ids = [p.organization_id for p in profiles]
        ratings_map = await profile_repo.get_avg_ratings_for_profiles(db, profile_ids)
        org_sa_map = await get_accounts_by_org_ids(db, org_ids)
        tags_map = await profile_repo.get_tags_for_profiles(db, profile_ids)

        items = [
            profile_service.build_profile_list_item(
                p,
                p.organization,
                org_sa_map.get(str(p.organization_id), []),
                ratings_map.get(str(p.id)),
                tags_map.get(str(p.id), []),
            )
            for p in profiles
        ]

    return {"success": True, "message": "ok", "data": {"items": items, "bookmarked_handles": list(handles)}}
