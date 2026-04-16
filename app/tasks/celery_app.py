from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "credfluence",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "app.tasks.notifications",
        "app.tasks.score",
        "app.tasks.ai",
        "app.tasks.badges",
        "app.tasks.events",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    beat_schedule={
        "check-dispute-windows-every-5-min": {
            "task": "app.tasks.score.check_dispute_windows",
            "schedule": 300.0,  # 5 minutes
        },
        "refresh-leaderboard-every-5-min": {
            "task": "app.tasks.score.refresh_leaderboard_cache",
            "schedule": 300.0,
        },
        "compute-badges-nightly": {
            "task": "app.tasks.badges.compute_badges",
            "schedule": 86400.0,  # 24 hours
        },
    },
)
