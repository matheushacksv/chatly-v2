import redis
from django.conf import settings
from django.http import StreamingHttpResponse
from ninja import Router

from apps.companies import services as companies_services

router = Router(tags=['Events'])


@router.get('/{company_id}/events')
def events_endpoint(request, company_id: int):
    companies_services.get_company(request.auth, company_id)

    def stream():
        pubsub = redis.Redis.from_url(settings.CELERY_BROKER_URL).pubsub()
        pubsub.subscribe(f'chatly:events:{company_id}')
        try:
            while True:
                message = pubsub.get_message(ignore_subscribe_messages=True, timeout=15)
                if message:
                    yield f'data: {message["data"].decode()}\n\n'
                else:
                    yield ':ping\n\n'
        finally:
            pubsub.close()

    response = StreamingHttpResponse(stream(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    return response
