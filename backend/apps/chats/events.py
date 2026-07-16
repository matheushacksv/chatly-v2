import json

import redis
from django.conf import settings

_client = None


def get_client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(settings.CELERY_BROKER_URL)
    return _client


def publish(company_id: int, event: dict) -> None:
    get_client().publish(f'chatly:events:{company_id}', json.dumps(event))
