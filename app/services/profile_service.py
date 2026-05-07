from app.models.profile import Profile
from app.models.social_account import SocialAccount


def primary_followers(stats: dict | None) -> int:
    if not stats:
        return 0
    return max(
        int(stats.get("subscribers") or 0),
        int(stats.get("followers") or 0),
        int(stats.get("connections") or 0),
    )


def safe_round(value: float | None) -> float | None:
    return round(float(value), 2) if value is not None else None


def serialize_social_account(sa: SocialAccount) -> dict:
    return {
        "platform": sa.platform,
        "username": sa.username,
        "display_name": sa.display_name,
        "avatar_url": sa.avatar_url,
        "stats": sa.stats,
    }


def _ig_link(profile: Profile) -> dict | None:
    return next((l for l in (profile.social_links or []) if l.get("platform") == "instagram"), None)


def _creator_platforms(profile: Profile, sa_list: list) -> list:
    if profile.profile_type != "creator":
        return []
    if sa_list:
        return list({sa.platform for sa in sa_list})
    ig = _ig_link(profile)
    return ["instagram"] if ig and ig.get("followers", 0) > 0 else []


def _creator_followers(profile: Profile, sa_list: list) -> int:
    if profile.profile_type != "creator":
        return 0
    if sa_list:
        return max((primary_followers(sa.stats) for sa in sa_list), default=0)
    ig = _ig_link(profile)
    return ig.get("followers", 0) if ig else 0


def build_profile_list_item(
    profile: Profile,
    org,
    sa_list: list[SocialAccount],
    avg_rating: float | None,
    top_tags: list[dict],
) -> dict:
    return {
        "handle": profile.handle,
        "display_name": profile.display_name,
        "profile_type": profile.profile_type,
        "avatar_url": profile.avatar_url,
        "bio": profile.bio,
        "location": profile.location,
        "category": profile.category,
        "niches": profile.niches or [],
        "languages": profile.languages or [],
        "trust_score": profile.trust_score,
        "review_count": profile.review_count,
        "avg_rating": safe_round(avg_rating),
        "verified": org.verification_status == "verified" if org else False,
        "platforms": _creator_platforms(profile, sa_list),
        "primary_followers": _creator_followers(profile, sa_list),
        "top_tags": top_tags,
        "is_claimed": profile.is_claimed,
        "access_level": profile.access_level,
        "social_links": profile.social_links or [],
    }


def build_profile_detail(
    profile: Profile,
    org,
    social_accounts: list[SocialAccount],
    rating_row,
    top_tags: list[dict],
) -> dict:
    return {
        "handle": profile.handle,
        "display_name": profile.display_name,
        "profile_type": profile.profile_type,
        "avatar_url": profile.avatar_url,
        "bio": profile.bio,
        "location": profile.location,
        "category": profile.category,
        "niches": profile.niches or [],
        "languages": profile.languages or [],
        "trust_score": profile.trust_score,
        "review_count": profile.review_count,
        "avg_rating": safe_round(rating_row.avg_rating),
        "verified": org.verification_status == "verified" if org else False,
        "is_claimed": profile.is_claimed,
        "access_level": profile.access_level,
        "org_id": str(org.id) if org else None,
        "platforms": _creator_platforms(profile, social_accounts),
        "primary_followers": _creator_followers(profile, social_accounts),
        "social_accounts": [serialize_social_account(sa) for sa in social_accounts] if profile.profile_type == "creator" else [],
        "badges": [
            {
                "badge_type": b.badge_type,
                "label": b.label,
                "description": b.description,
                "icon_url": b.icon_url,
                "earned_at": b.earned_at.isoformat(),
            }
            for b in profile.badges
        ],
        "top_tags": top_tags,
        "social_links": profile.social_links or [],
        "website": (org.verification_docs or {}).get("website") if org else None,
        "stats_breakdown": {
            "avg_communication":   safe_round(rating_row.avg_communication),
            "avg_professionalism": safe_round(rating_row.avg_professionalism),
            "avg_quality":         safe_round(rating_row.avg_quality),
            "avg_reliability":     safe_round(rating_row.avg_reliability),
        },
    }


def build_leaderboard_item(
    profile: Profile,
    org,
    sa_list: list[SocialAccount],
) -> dict:
    return {
        "handle": profile.handle,
        "display_name": profile.display_name,
        "profile_type": profile.profile_type,
        "avatar_url": profile.avatar_url,
        "trust_score": profile.trust_score,
        "verified": org.verification_status == "verified" if org else False,
        "platforms": _creator_platforms(profile, sa_list),
        "primary_followers": _creator_followers(profile, sa_list),
        "category": profile.category,
    }


def _reviewer_primary_social(social_accounts: list) -> dict:
    """Pick the social account with the highest follower count."""
    candidates = []
    for acct in (social_accounts or []):
        if not acct.username:
            continue
        stats = acct.stats or {}
        if acct.platform == "instagram":
            followers = int(stats.get("followers_count") or 0)
            candidates.append((followers, "instagram", acct.username))
        elif acct.platform == "youtube":
            followers = int(stats.get("subscribers") or 0)
            handle = stats.get("youtube_handle") or acct.username
            candidates.append((followers, "youtube", handle))
    if not candidates:
        return {}
    candidates.sort(key=lambda x: x[0], reverse=True)
    followers, platform, handle = candidates[0]
    return {"platform": platform, "handle": handle, "followers": followers}


def build_review_item(review, current_user_id: str | None = None) -> dict:
    reviewer_org = review.reviewer.organization if review.reviewer else None
    scores = [r.score for r in review.ratings] if review.ratings else []
    avg = round(sum(scores) / len(scores), 2) if scores else None
    social = _reviewer_primary_social(
        getattr(review.reviewer, "social_accounts", []) or []
    ) if review.reviewer else {}

    likes = getattr(review, "likes", []) or []
    top_comments = [c for c in (getattr(review, "comments", []) or []) if c.parent_comment_id is None and c.status == "active"]
    official_reply = getattr(review, "reply", None)

    return {
        "id": str(review.id),
        "relationship_type": review.relationship_type,
        "body": review.body,
        "avg_rating": avg,
        "ratings": [
            {"category": r.category, "score": r.score}
            for r in (review.ratings or [])
        ],
        "tags": [t.tag for t in (review.tags or [])],
        "status": review.status,
        "created_at": review.created_at.isoformat(),
        "like_count": len(likes),
        "liked_by_me": any(str(lk.user_id) == current_user_id for lk in likes) if current_user_id else False,
        "comment_count": len(top_comments),
        "official_reply": {
            "body": official_reply.body,
            "updated_at": official_reply.updated_at.isoformat(),
        } if official_reply else None,
        "reviewer": {
            "org_name": reviewer_org.name if reviewer_org else None,
            "org_type": reviewer_org.org_type if reviewer_org else None,
            "handle": reviewer_org.profile.handle if reviewer_org and reviewer_org.profile else None,
            "avatar_url": None,
            "social": social or None,
        } if review.reviewer else None,
    }
