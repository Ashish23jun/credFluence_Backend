"""Notification tasks"""
from app.tasks.celery_app import celery_app


@celery_app.task(name="app.tasks.notifications.send_review_notification")
def send_review_notification(review_id: str) -> None:
    """Send email + WhatsApp notification when a review is submitted."""
    pass
