from celery import shared_task

from apps.accounts import emails
from apps.accounts.models import User


@shared_task(bind=True, max_retries=3)
def send_reset_email_task(self, user_id: int):
    user = User.objects.filter(pk=user_id).first()
    if user is None:
        return
    try:
        emails.send_password_reset(user)
    except Exception as exc:
        raise self.retry(exc=exc, countdown=2**self.request.retries)
