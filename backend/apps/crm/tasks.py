from celery import shared_task

from apps.crm import services


@shared_task
def dispatch_deal_openings():
    services.dispatch_deal_openings()
