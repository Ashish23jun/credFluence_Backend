"""Badge computation tasks"""
from app.tasks.celery_app import celery_app


@celery_app.task(name="app.tasks.badges.compute_badges")
def compute_badges() -> None:
    """Nightly: compute and assign badges to profiles."""
    pass
