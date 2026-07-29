from celery import shared_task

from apps.automations import services


@shared_task
def resume_flow(run_id: int) -> None:
    services.resume(run_id)
