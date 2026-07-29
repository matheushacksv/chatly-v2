import mimetypes
import random

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.db.models import F, OuterRef, Subquery
from django.db.models.functions import Coalesce

from apps.chats import events
from apps.chats.models import Contact, Sticker
from apps.chats.services import MEDIA_SPECS, normalize_number
from apps.companies.models import Company
from apps.evolution import evolution
from apps.evolution.models import EvolutionAccount, EvolutionContact, EvolutionMessage
from core.exceptions import Conflict, EvolutionError, Forbidden, NotFound, ValidationError

User = get_user_model()

EVENTS = ['MESSAGE', 'CONNECTION']
EVOLUTION_EXTRA_AUDIO = {'audio/webm'}

# * ----- Connections ------


def _sync_status(account: EvolutionAccount) -> None:
    """Reflete o estado real do evogo no model"""
    try:
        state = evolution.get_connection_status(account.instance_api_key)
    except EvolutionError:
        return
    fields: list[str] = []
    if state.get('LoggedIn'):
        if account.status != EvolutionAccount.Status.CONNECTED:
            account.status = EvolutionAccount.Status.CONNECTED
            fields.append('status')
        if not account.phone_number:
            phone = evolution.get_instance_phone(account.instance_name)
            if phone:
                account.phone_number = phone
                fields.append('phone_number')
    elif account.status == EvolutionAccount.Status.CONNECTED:
        if state.get('Connected'):
            try:
                evolution.reconnect_instance(account.instance_api_key)
            except EvolutionError:
                pass
        else:
            account.status = EvolutionAccount.Status.DISCONNECTED
            fields.append('status')
    if fields:
        account.save(update_fields=fields)


def _get_own_or_owned_account(company: Company, user: User, account_id: int) -> EvolutionAccount:
    account = EvolutionAccount.objects.filter(company=company, pk=account_id).first()
    if account is None:
        raise NotFound('connection not found')
    if account.user.pk != user.pk and company.owner.pk != user.pk:
        raise Forbidden('not your connection')
    return account


def create_and_connect_evolution_instance(company: Company, user: User, instance_name: str, phone: str | None = None):

    if EvolutionAccount.objects.filter(company=company, user=user).exists():
        raise Conflict('connection already exists')
    if EvolutionAccount.objects.filter(instance_name=instance_name).exists():
        instance_name = instance_name + f'{random.randint(0, 9999):04d}'

    instance_api_key = evolution.create_instance(instance_name)
    account = EvolutionAccount.objects.create(
        company=company, user=user, instance_name=instance_name, status=EvolutionAccount.Status.CONNECTING, instance_api_key=instance_api_key
    )
    try:
        connection = evolution.connect_instance(instance_api_key, settings.EVOLUTION_WEBHOOK_URL, EVENTS)
        if connection != 'success':
            raise EvolutionError('connection error')
    except Exception:
        account.delete()
        try:
            evolution.delete_instance(instance_name)
        except EvolutionError:
            pass
        raise EvolutionError('connection error')
    try:
        if phone:
            return evolution.get_pair_code(instance_api_key, phone)
        return evolution.get_qr_code(instance_api_key)
    except EvolutionError:
        return ''


def get_my_connection(company: Company, user: User) -> EvolutionAccount | None:
    account = EvolutionAccount.objects.filter(company=company, user=user).first()
    if account is not None:
        _sync_status(account)
    return account


def list_connections(company: Company) -> list[EvolutionAccount]:
    return list(EvolutionAccount.objects.filter(company=company).select_related('user'))


def get_qrcode(company: Company, user: User, account_id: int) -> str:
    account = _get_own_or_owned_account(company, user, account_id)
    return evolution.get_qr_code(account.instance_api_key)


def request_pairing_code(company: Company, user: User, account_id: int, phone: str) -> str:
    account = _get_own_or_owned_account(company, user, account_id)
    return evolution.get_pair_code(account.instance_api_key, phone)


def disconnect(company: Company, user: User, account_id: int) -> None:
    account = _get_own_or_owned_account(company, user, account_id)
    evolution.delete_instance(account.instance_name)
    account.delete()


# * ----- Webhooks ------

EVENT_HANDLERS = {}


def handle_webhook(payload: dict) -> None:
    handler = EVENT_HANDLERS.get(payload.get('event'))
    if handler:
        handler(payload)


def handle_message_webhook(payload: dict):
    account = EvolutionAccount.objects.filter(instance_api_key=payload.get('instanceToken')).first()
    if account is None:
        return
    data = payload.get('data', {})
    info = data.get('Info', {})

    if info.get('IsFromMe') is True:  # ignore from me
        return
    if info.get('IsGroup') is True:  # ignore group
        return

    sender = info.get('Sender')
    wa_id = sender.split('@')[0].split(':')[0]

    push_name = info.get('PushName') or ''
    contact, created = EvolutionContact.objects.get_or_create(account=account, wa_id=wa_id, defaults={'name': push_name})
    if not created and not contact.name and push_name:
        contact.name = push_name
        contact.save(update_fields=['name'])

    message_type = info.get('Type')

    if message_type == 'text':
        content = data.get('Message', {}).get('conversation')
        EvolutionMessage.objects.create(
            contact=contact,
            direction=EvolutionMessage.Direction.IN,
            content=content,
            message_type=EvolutionMessage.Type.TEXT,
            status=EvolutionMessage.Status.RECEIVED,
            wa_message_id=data['Info'].get('ID', ''),
        )
        events.publish(account.company.pk, {'type': 'message', 'contact_id': contact.pk, 'channel': 'evolution', 'account_id': account.pk})
        return

    if message_type == 'media':
        media_type = info.get('MediaType')
        submsg = data.get('Message', {}).get(f'{media_type}Message', {})
        try:
            raw, mime = evolution.download_media(account.instance_api_key, data['Message'])
            ext = mimetypes.guess_extension(mime) or ''
            media_file = ContentFile(raw, name=submsg.get('fileName') or f'{media_type}{ext}')
        except (EvolutionError, NotFound, KeyError):
            media_file, mime = None, ''
        placeholders = {
            'video': '[vídeo]',
            'audio': '[áudio]',
            'image': '[imagem]',
            'document': '[documento]',
            'sticker': '[figurinha]',
        }
        EvolutionMessage.objects.create(
            contact=contact,
            direction=EvolutionMessage.Direction.IN,
            content=placeholders.get(media_type, '[mídia]'),
            message_type=media_type,
            media_file=media_file,
            mime_type=mime,
            status=EvolutionMessage.Status.RECEIVED,
            wa_message_id=data['Info'].get('ID', ''),
        )
        events.publish(account.company.pk, {'type': 'message', 'contact_id': contact.pk, 'channel': 'evolution', 'account_id': account.pk})


def handle_connection_webhook(payload: dict) -> None:
    account = EvolutionAccount.objects.filter(instance_api_key=payload.get('instanceToken')).first()
    if account is None:
        return
    data = payload.get('data', {})
    status = data.get('status')
    if status == 'open':
        jid = data.get('jid', '')
        account.status = EvolutionAccount.Status.CONNECTED
        account.phone_number = jid.split('@')[0].split(':')[0]
        account.save(update_fields=['status', 'phone_number'])
    else:
        account.status = EvolutionAccount.Status.DISCONNECTED
        account.save(update_fields=['status'])
    events.publish(account.company.pk, {'type': 'evolution_status', 'account_id': account.pk, 'status': account.status})


def handle_disconnection_webhook(payload: dict) -> None:
    account = EvolutionAccount.objects.filter(instance_api_key=payload.get('instanceToken')).first()
    if account is None:
        return
    account.status = EvolutionAccount.Status.DISCONNECTED
    account.last_logged_out_reason = payload.get('data', {}).get('Reason')
    account.save(update_fields=['status', 'last_logged_out_reason'])
    events.publish(account.company.pk, {'type': 'evolution_status', 'account_id': account.pk, 'status': account.status})


EVENT_HANDLERS.update(
    {
        'PairSuccess': handle_connection_webhook,
        'Connected': handle_connection_webhook,
        'LoggedOut': handle_disconnection_webhook,
        'Message': handle_message_webhook,
    }
)

# * ----- Contacts ------


def _queue_avatar_sync(account: EvolutionAccount) -> None:
    from apps.evolution.tasks import stale_avatars, sync_avatars

    if stale_avatars(account).exists():
        sync_avatars.delay(account.pk)


def _fill_missing_names(account: EvolutionAccount, contacts: list[EvolutionContact]) -> None:
    missing = [c for c in contacts if not c.name]
    if not missing:
        return
    try:
        agenda = evolution.get_device_contacts(account.instance_api_key)
    except EvolutionError:
        return
    changed = []
    for contact in missing:
        name = agenda.get(contact.wa_id)
        if name:
            contact.name = name
            changed.append(contact)
    EvolutionContact.objects.bulk_update(changed, ['name'])


def _get_own_contact(company: Company, user: User, account_id: int, contact_id: int) -> EvolutionContact:
    account = _get_own_or_owned_account(company, user, account_id)
    contact = EvolutionContact.objects.filter(account=account, pk=contact_id).first()
    if contact is None:
        raise NotFound('contact not found')
    return contact


def list_contacts(company: Company, user: User, account_id: int) -> list[EvolutionContact]:
    account = _get_own_or_owned_account(company, user, account_id)
    last = EvolutionMessage.objects.filter(contact=OuterRef('pk')).order_by('-id')
    contacts = list(
        EvolutionContact.objects.filter(account=account)
        .annotate(activity=Coalesce(Subquery(last.values('created_at')[:1]), F('created_at')))
        .order_by('-activity')
    )
    _fill_missing_names(account, contacts)
    _queue_avatar_sync(account)
    return contacts


def list_directory(company: Company, user: User, account_id: int) -> list[dict]:
    account = _get_own_or_owned_account(company, user, account_id)
    entries: dict[str, dict] = {}
    try:
        for wa_id, name in evolution.get_device_contacts(account.instance_api_key).items():
            entries[wa_id] = {'wa_id': wa_id, 'name': name, 'source': 'agenda'}
    except EvolutionError:
        pass
    for wa_id, name in Contact.objects.filter(account__company=company).values_list('wa_id', 'name'):
        entries.setdefault(wa_id, {'wa_id': wa_id, 'name': name, 'source': 'oficial'})

    return sorted(entries.values(), key=lambda e: e['name'].lower())


def start_conversation(company: Company, user: User, account_id: int, phone: str, name: str = '') -> EvolutionContact:
    account = _get_own_or_owned_account(company, user, account_id)
    wa_id = evolution.check_number(account.instance_api_key, normalize_number(phone))
    if not wa_id:
        raise ValidationError('number not on whatsapp')
    contact, _ = EvolutionContact.objects.get_or_create(account=account, wa_id=wa_id, defaults={'name': name})
    _queue_avatar_sync(account)
    return contact

def delete_conversation(company: Company, user: User, account_id: int, contact_id: int) -> None:
    contact = _get_own_contact(company, user, account_id, contact_id)
    contact.delete()


# * ----- Messages ------


def list_messages(company: Company, user: User, account_id: int, contact_id: int) -> list[EvolutionMessage]:
    contact = _get_own_contact(company, user, account_id, contact_id)
    return list(EvolutionMessage.objects.filter(contact=contact))


def send_text_message(company: Company, user: User, account_id: int, contact_id: int, content: str) -> EvolutionMessage:
    account = _get_own_or_owned_account(company, user, account_id)
    contact = _get_own_contact(company, user, account_id, contact_id)
    evolution.send_text(account.instance_api_key, contact.wa_id, content)
    return EvolutionMessage.objects.create(
        contact=contact,
        direction=EvolutionMessage.Direction.OUT,
        content=content,
        message_type=EvolutionMessage.Type.TEXT,
        status=EvolutionMessage.Status.SENT,
    )


def send_media_message(company, user, account_id, contact_id, file, caption=''):
    account = _get_own_or_owned_account(company, user, account_id)
    contact = _get_own_contact(company, user, account_id, contact_id)

    kind = file.content_type.split('/')[0]
    if file.content_type == 'image/webp':
        kind = 'sticker'
    elif kind not in ('image', 'audio', 'video'):
        kind = 'document'

    allowed, max_size = MEDIA_SPECS[kind]
    if kind == 'audio':
        allowed = (allowed or set()) | EVOLUTION_EXTRA_AUDIO
    if allowed is not None and file.content_type not in allowed:
        raise ValidationError(f'unsupported {kind} type: {file.content_type}')
    if file.size > max_size:
        raise ValidationError(f'{kind} too large')

    content = file.read()
    file.seek(0)

    placeholders = {'image': '[imagem]', 'audio': '[áudio]', 'video': '[vídeo]', 'sticker': '[figurinha]'}
    message = EvolutionMessage.objects.create(
        contact=contact,
        direction=EvolutionMessage.Direction.OUT,
        content=caption or placeholders.get(kind) or f'[documento: {file.name}]',
        message_type=kind,
        media_file=file,
        mime_type=file.content_type,
        status=EvolutionMessage.Status.SENT,
    )
    try:
        if kind == 'sticker':
            evolution.send_sticker(account.instance_api_key, contact.wa_id, message.media_file.url)
        else:
            evolution.send_media_file(account.instance_api_key, contact.wa_id, kind, content, file.name, file.content_type, caption)
    except Exception:
        message.delete()
        raise
    return message


# * ----- Stickers ------


def save_sticker(company: Company, user: User, account_id: int, message_id: int) -> Sticker:
    account = _get_own_or_owned_account(company, user, account_id)
    message = EvolutionMessage.objects.filter(contact__account=account, pk=message_id, message_type=EvolutionMessage.Type.STICKER).first()
    if message is None or not message.media_file:
        raise NotFound('message not found')
    return Sticker.objects.create(company=company, file=ContentFile(message.media_file.read(), name='sticker.webp'))


def send_saved_sticker(company: Company, user: User, account_id: int, contact_id: int, sticker_id: int) -> EvolutionMessage:
    account = _get_own_or_owned_account(company, user, account_id)
    contact = _get_own_contact(company, user, account_id, contact_id)
    sticker = Sticker.objects.filter(company=company, pk=sticker_id).first()
    if sticker is None:
        raise NotFound('sticker not found')
    evolution.send_sticker(account.instance_api_key, contact.wa_id, sticker.file.url)
    return EvolutionMessage.objects.create(
        contact=contact,
        direction=EvolutionMessage.Direction.OUT,
        content='[figurinha]',
        message_type=EvolutionMessage.Type.STICKER,
        media_file=ContentFile(sticker.file.read(), name='sticker.webp'),
        mime_type='image/webp',
        status=EvolutionMessage.Status.SENT,
    )
