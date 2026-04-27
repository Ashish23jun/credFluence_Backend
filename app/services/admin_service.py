from app.services.storage_service import presign_get


def serialize_docs(docs: dict | None) -> dict | None:
    if not docs:
        return None
    gst = docs.get("gst", {})
    cin = docs.get("cin", {})
    trademark = docs.get("trademark", {})
    return {
        "website": docs.get("website"),
        "gst": {
            "number": gst.get("number"),
            "file_url": presign_get(gst.get("file_key")),
        },
        "cin": {
            "number": cin.get("number"),
            "file_url": presign_get(cin.get("file_key")),
        },
        "trademark": {
            "file_url": presign_get(trademark.get("file_key")),
        },
    }


def serialize_org_list_item(org) -> dict:
    admin_member = next(
        (m for m in org.memberships if m.role == "admin" and m.status == "active"), None
    )
    return {
        "id": str(org.id),
        "name": org.name,
        "slug": org.slug,
        "org_type": org.org_type,
        "verification_status": org.verification_status,
        "rejected_reason": org.rejected_reason,
        "created_at": org.created_at.isoformat(),
        "verified_at": org.verified_at.isoformat() if org.verified_at else None,
        "domains": [d.domain for d in org.domains],
        "member_count": len(org.memberships),
        "admin_email": admin_member.user.email if admin_member and admin_member.user else None,
    }


def serialize_org_detail(org, social_accounts: list) -> dict:
    profile = org.profile
    return {
        "id": str(org.id),
        "name": org.name,
        "slug": org.slug,
        "org_type": org.org_type,
        "verification_status": org.verification_status,
        "verification_notes": org.verification_notes,
        "rejected_reason": org.rejected_reason,
        "verified_at": org.verified_at.isoformat() if org.verified_at else None,
        "created_at": org.created_at.isoformat(),
        "domains": [d.domain for d in org.domains],
        "profile": {
            "display_name": profile.display_name if profile else None,
            "bio": profile.bio if profile else None,
            "category": profile.category if profile else None,
            "location": profile.location if profile else None,
            "avatar_url": profile.avatar_url if profile else None,
            "trust_score": profile.trust_score if profile else None,
            "access_level": profile.access_level if profile else None,
            "niches": profile.niches or [] if profile else [],
            "languages": profile.languages or [] if profile else [],
            "social_links": profile.social_links or [] if profile else [],
        } if profile else None,
        "members": [
            {
                "user_id": str(m.user_id),
                "email": m.user.email if m.user else None,
                "role": m.role,
                "status": m.status,
                "joined_at": m.created_at.isoformat(),
            }
            for m in org.memberships
        ],
        "social_accounts": [
            {
                "user_id": str(sa.user_id),
                "platform": sa.platform,
                "username": sa.username,
                "display_name": sa.display_name,
                "stats": sa.stats,
                "connected_at": sa.connected_at.isoformat() if sa.connected_at else None,
            }
            for sa in social_accounts
        ],
        "verification_docs": serialize_docs(org.verification_docs),
    }
