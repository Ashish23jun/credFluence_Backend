# Import all models so Alembic can detect them
from app.models.activity_event import ActivityEvent
from app.models.badge import Badge
from app.models.dispute import Dispute
from app.models.dispute_recipient import DisputeRecipient
from app.models.fraud_alert import FraudAlert
from app.models.notification import Notification
from app.models.organization import Organization
from app.models.organization_domain import OrganizationDomain
from app.models.organization_membership import OrganizationMembership
from app.models.profile import Profile
from app.models.review import (
    Review, ReviewComment, ReviewEvidence, ReviewFlag,
    ReviewLike, ReviewPayment, ReviewRating, ReviewTag,
)
from app.models.social_account import SocialAccount
from app.models.tag_aggregation import TagAggregation
from app.models.user import User

__all__ = [
    "Organization",
    "OrganizationDomain",
    "OrganizationMembership",
    "User",
    "Profile",
    "SocialAccount",
    "Review",
    "ReviewPayment",
    "ReviewRating",
    "ReviewFlag",
    "ReviewEvidence",
    "ReviewTag",
    "ReviewLike",
    "ReviewComment",
    "Dispute",
    "DisputeRecipient",
    "TagAggregation",
    "Badge",
    "Notification",
    "ActivityEvent",
    "FraudAlert",
]
