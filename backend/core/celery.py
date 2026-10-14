import os

from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')

app = Celery('chatly')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()

# beat roda embutido no worker (-B): uma réplica só, então não duplica
app.conf.beat_schedule = {
    'dispatch-scheduled-campaigns': {'task': 'apps.chats.tasks.dispatch_scheduled_campaigns', 'schedule': 60.0},
}
