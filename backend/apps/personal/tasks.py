import logging
import mimetypes
from datetime import timedelta

from celery import shared_task
from django.core.files.base import ContentFile
from django.db.models import Q
from django.utils import timezone

from apps.chats import events
from apps.personal import gateway
from apps.personal.models import PersonalAccount, PersonalContact, PersonalMessage
from core.exceptions import GatewayError, NotFound

logger = logging.getLogger(__name__)

AVATAR_TTL = timedelta(days=7)


def stale_avatars(account: PersonalAccount):
    return PersonalContact.objects.filter(account=account).filter(
        Q(avatar_synced_at__isnull=True) | Q(avatar_synced_at__lt=timezone.now() - AVATAR_TTL)
    )


@shared_task
def sync_avatars(account_id: int):
    account = PersonalAccount.objects.filter(pk=account_id).first()
    if account is None or not account.session_id:
        return
    contacts = list(stale_avatars(account))
    if not contacts:
        return
    # marca antes de buscar: outra chamada da lista não enfileira o mesmo lote de novo
    PersonalContact.objects.filter(pk__in=[c.pk for c in contacts]).update(avatar_synced_at=timezone.now())
    for contact in contacts:
        try:
            contact.avatar_url = gateway.get_profile_picture(account.session_id, contact.wa_id)
        except (GatewayError, NotFound):
            contact.avatar_url = ''
    PersonalContact.objects.bulk_update(contacts, ['avatar_url'])
    events.publish(account.company_id, {'type': 'personal_contacts', 'channel': 'personal', 'account_id': account.pk})


@shared_task
def fetch_message_media(message_id: int, filename: str = '') -> None:
    message = PersonalMessage.objects.filter(pk=message_id).select_related('contact__account').first()
    if message is None or message.media_file or not message.wa_message_id:
        return
    account = message.contact.account
    try:
        raw, mime = gateway.download_media(account.session_id, message.contact.wa_id, message.wa_message_id)
    except (GatewayError, NotFound):
        logger.warning('mídia da mensagem %s não veio do gateway', message_id)
        return
    if not raw:
        return
    ext = mimetypes.guess_extension(mime) or ''
    message.media_file = ContentFile(raw, name=filename or f'{message.message_type}{ext}')
    message.mime_type = mime
    message.save(update_fields=['media_file', 'mime_type'])
    events.publish(account.company_id, {'type': 'message', 'contact_id': message.contact_id, 'channel': 'personal', 'account_id': account.pk})
