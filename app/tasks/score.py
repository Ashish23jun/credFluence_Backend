"""Score and dispute window tasks"""
from app.tasks.celery_app import celery_app


@celery_app.task(name="app.tasks.score.check_dispute_windows")
def check_dispute_windows() -> None:
    """Check Redis for expired dispute windows and move reviews to verification queue."""
    pass


@celery_app.task(name="app.tasks.score.refresh_leaderboard_cache")
def refresh_leaderboard_cache() -> None:
    """Pre-compute and cache leaderboard in Redis."""
    pass


@celery_app.task(name="app.tasks.score.recalculate_trust_score")
def recalculate_trust_score(profile_id: str) -> None:
    """Recalculate trust score after a review is verified."""
    pass
