# Import all models so Alembic can detect them
from app.models.activity_event import ActivityEvent
from app.models.badge import Badge
from app.models.dispute import Dispute
from app.models.fraud_alert import FraudAlert
from app.models.notification import Notification
from app.models.profile import Profile
from app.models.review import Review
from app.models.social_account import SocialAccount
from app.models.tag_aggregation import TagAggregation
from app.models.user import User

__all__ = [
    "User",
    "Profile",
    "SocialAccount",
    "Review",
    "Dispute",
    "TagAggregation",
    "Badge",
    "Notification",
    "ActivityEvent",
    "FraudAlert",
]
