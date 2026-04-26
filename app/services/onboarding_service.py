def build_onboarding_context(user, org, profile, membership, social_accounts: list) -> dict:
    return {
        "user": {
            "id": str(user.id),
            "email": user.email,
            "role": user.role,
            "subscription_tier": user.subscription_tier,
            "onboarding_completed_at": (
                user.onboarding_completed_at.isoformat()
                if user.onboarding_completed_at else None
            ),
        },
        "org": {
            "id": str(org.id),
            "name": org.name,
            "slug": org.slug,
            "org_type": org.org_type,
            "verification_status": org.verification_status,
            "is_personal_creator_org": org.is_personal_creator_org,
            "rejected_reason": org.rejected_reason,
            "verification_docs": org.verification_docs,
        } if org else None,
        "profile": {
            "display_name": profile.display_name if profile else None,
            "bio": profile.bio if profile else None,
            "category": profile.category if profile else None,
            "location": profile.location if profile else None,
            "avatar_url": profile.avatar_url if profile else None,
            "trust_score": profile.trust_score if profile else None,
            "access_level": profile.access_level if profile else None,
        } if profile else None,
        "membership": {
            "role": membership.role,
            "status": membership.status,
        } if membership else None,
        "connected_platforms": [
            {
                "platform": sa.platform,
                "username": sa.username,
                "display_name": sa.display_name,
                "avatar_url": sa.avatar_url,
                "stats": sa.stats,
            }
            for sa in social_accounts
        ],
    }


def build_docs_dict(payload) -> dict:
    return {
        "website": payload.website,
        "gst": {
            "number": payload.gst_number or None,
            "file_key": payload.gst_file_url or None,
        },
        "cin": {
            "number": payload.cin_number or None,
            "file_key": payload.cin_file_url or None,
        },
        "trademark": {
            "file_key": payload.trademark_file_url or None,
        },
    }
