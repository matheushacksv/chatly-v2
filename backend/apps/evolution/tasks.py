from datetime import timedelta

from celery import shared_task
from django.db.models import Q
from django.utils import timezone

from apps.chats import events
from apps.evolution import evolution
from apps.evolution.models import EvolutionAccount, EvolutionContact

AVATAR_TTL = timedelta(days=7)


def stale_avatars(account: EvolutionAccount):
    return EvolutionContact.objects.filter(account=account).filter(
        Q(avatar_synced_at__isnull=True) | Q(avatar_synced_at__lt=timezone.now() - AVATAR_TTL)
    )


@shared_task
def sync_avatars(account_id: int):
    account = EvolutionAccount.objects.filter(pk=account_id).first()
    if account is None:
        return
    contacts = list(stale_avatars(account))
    if not contacts:
        return
    EvolutionContact.objects.filter(pk__in=[c.pk for c in contacts]).update(avatar_synced_at=timezone.now())
    changed = []
    for contact in contacts:
        contact.avatar_url = evolution.get_user_avatar(account.instance_api_key, contact.wa_id)
        changed.append(contact)
    EvolutionContact.objects.bulk_update(changed, ['avatar_url'])
    events.publish(account.company.pk, {'type': 'evolution_contacts', 'account_id': account.pk})
