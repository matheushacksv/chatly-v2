from celery import shared_task

from apps.crm import services


@shared_task(bind=True, max_retries=3)
def note_ai_conversation(self, contact_id: int):
    try:
        later = services.note_ai_conversation(contact_id)
    except Exception as exc:
        raise self.retry(exc=exc, countdown=60 * 2**self.request.retries)
    if later:
        note_ai_conversation.apply_async((contact_id,), eta=later)


@shared_task
def dispatch_deal_openings():
    services.dispatch_deal_openings()
