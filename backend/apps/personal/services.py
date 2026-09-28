import hashlib
import hmac
import logging
import time

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.db.models import F, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce

from apps.chats import events
from apps.chats.models import Contact, Sticker
from apps.chats.services import MEDIA_SPECS, PLACEHOLDERS, normalize_number, saved_sticker, sticker_saved_annotation
from apps.companies.models import Company
from apps.personal import gateway
from apps.personal.models import PersonalAccount, PersonalContact, PersonalMessage
from core.exceptions import Conflict, Forbidden, GatewayError, NotFound, ValidationError

logger = logging.getLogger(__name__)
User = get_user_model()

SUBSCRIBE = ['message.received', 'message.sent', 'session.status', 'session.authenticated', 'session.disconnected']
# navegador grava áudio em webm; o gateway converte pra nota de voz
EXTRA_AUDIO = {'audio/webm'}
# a mídia vai em base64 no JSON (~1.37x) e fica inteira em memória dos dois lados
MAX_MEDIA_BYTES = 16 * 2**20

# status do gateway
LINKED = {'ready', 'connected'}
LINKABLE = {'qr_ready', 'authenticated', *LINKED}
RUNNING = {'starting', 'initializing', *LINKABLE}
PAIRING = {'qr_ready', 'authenticated', 'starting', 'initializing', 'created'}
CLIENT_READY_TIMEOUT = 25
CLIENT_READY_INTERVAL = 0.25


def _publish(account: PersonalAccount, payload: dict) -> None:
    # canal próprio: ids do pessoal não podem colidir com os da tabela oficial no SSE
    events.publish(account.company_id, {**payload, 'channel': 'personal', 'account_id': account.pk})


def _publish_message(account: PersonalAccount, contact: PersonalContact) -> None:
    _publish(account, {'type': 'message', 'contact_id': contact.pk})


def _publish_status(account: PersonalAccount) -> None:
    _publish(account, {'type': 'personal_status', 'status': account.status})


# * ----- Conexão ------


def _session_status(account: PersonalAccount) -> str | None:
    """Status da sessão, ou None quando o gateway não respondeu ('ainda subindo' != 'fora')."""
    try:
        return str(gateway.session_state(account.session_id).get('status') or '')
    except (GatewayError, NotFound):
        return None


def _wait_for_client(account: PersonalAccount) -> None:
    # pedir QR antes do cliente subir devolve vazio
    deadline = time.monotonic() + CLIENT_READY_TIMEOUT
    while True:
        status = _session_status(account)
        if status is None or status in LINKABLE or time.monotonic() >= deadline:
            return
        time.sleep(CLIENT_READY_INTERVAL)


def _ensure_session(account: PersonalAccount) -> None:
    """Garante sessão viva no gateway: adota pelo nome, recria se o id sumiu, não reinicia o que já está de pé."""
    status = _session_status(account) if account.session_id else None
    if status is None:
        account.session_id = gateway.find_session_id(account.session_name) or gateway.create_session(account.session_name)
        account.save(update_fields=['session_id'])
        status = _session_status(account)
    if status not in RUNNING:
        try:
            gateway.start_session(account.session_id)
        except GatewayError:
            # corrida: subiu entre a pergunta e o start ('Session is already started')
            if _session_status(account) not in RUNNING:
                raise
    gateway.register_webhook(account.session_id, settings.OPENWA_WEBHOOK_URL, SUBSCRIBE, settings.OPENWA_WEBHOOK_SECRET)


def _apply_connected(account: PersonalAccount, phone: str) -> None:
    account.status = PersonalAccount.Status.CONNECTED
    account.phone_number = gateway.number_from_chat_id(phone) or account.phone_number
    account.save(update_fields=['status', 'phone_number'])


def _sync_status(account: PersonalAccount) -> None:
    """Pergunta pro gateway em vez de depender só do webhook (webhook perdido deixaria o painel pedindo QR)."""
    try:
        state = gateway.session_state(account.session_id)
    except (GatewayError, NotFound):
        return
    status = str(state.get('status') or '')
    if status in LINKED:
        if account.status != PersonalAccount.Status.CONNECTED or not account.phone_number:
            _apply_connected(account, state.get('phone') or '')
    elif account.status == PersonalAccount.Status.CONNECTED and status not in PAIRING:
        account.status = PersonalAccount.Status.DISCONNECTED
        account.save(update_fields=['status'])


def _get_own_or_owned_account(company: Company, user: User, account_id: int) -> PersonalAccount:
    account = PersonalAccount.objects.filter(company=company, pk=account_id).first()
    if account is None:
        raise NotFound('connection not found')
    if account.user_id != user.pk and company.owner_id != user.pk:
        raise Forbidden('not your connection')
    return account


def _pairing_answer(account: PersonalAccount, phone: str | None) -> dict:
    _wait_for_client(account)
    if phone:
        return {'pair_code': gateway.get_pair_code(account.session_id, normalize_number(phone))}
    return {'qrcode': gateway.get_qr_code(account.session_id)}


def connect(company: Company, user: User, phone: str | None = None) -> dict:
    account = PersonalAccount.objects.filter(company=company, user=user).first()
    if account is None:
        account = PersonalAccount.objects.create(company=company, user=user, session_name=f'c{company.pk}u{user.pk}')
    else:
        _sync_status(account)
        if account.status == PersonalAccount.Status.CONNECTED:
            raise Conflict('connection already exists')
        account.status = PersonalAccount.Status.CONNECTING
        account.save(update_fields=['status'])
    _ensure_session(account)
    return _pairing_answer(account, phone)


def get_my_connection(company: Company, user: User) -> PersonalAccount | None:
    account = PersonalAccount.objects.filter(company=company, user=user).first()
    if account is not None and account.session_id:
        _sync_status(account)
    return account


def list_connections(company: Company) -> list[PersonalAccount]:
    return list(PersonalAccount.objects.filter(company=company).select_related('user'))


def get_qrcode(company: Company, user: User, account_id: int) -> str:
    account = _get_own_or_owned_account(company, user, account_id)
    _ensure_session(account)
    return _pairing_answer(account, None)['qrcode']


def request_pairing_code(company: Company, user: User, account_id: int, phone: str) -> str:
    account = _get_own_or_owned_account(company, user, account_id)
    _ensure_session(account)
    return _pairing_answer(account, phone)['pair_code']


def disconnect(company: Company, user: User, account_id: int) -> None:
    account = _get_own_or_owned_account(company, user, account_id)
    if account.session_id:
        try:
            gateway.logout_session(account.session_id)
        except (GatewayError, NotFound):
            logger.warning('logout da sessão %s não completou no gateway', account.session_name)
        try:
            gateway.delete_session(account.session_id)
        except (GatewayError, NotFound):
            logger.warning('sessão %s não foi apagada no gateway', account.session_name)
    account.delete()


# * ----- Webhook ------


def verify_webhook_signature(body: bytes, signature: str) -> bool:
    """X-OpenWA-Signature: 'sha256=' + HMAC-SHA256 hex do corpo cru (conferido no payload real)."""
    expected = 'sha256=' + hmac.new(settings.OPENWA_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _find_account(payload: dict) -> PersonalAccount | None:
    key = str(payload.get('sessionId') or '')
    if not key:
        return None
    return PersonalAccount.objects.filter(Q(session_id=key) | Q(session_name=key)).select_related('company').first()


MEDIA_TYPES = {'image', 'video', 'audio', 'document', 'sticker'}
# o gateway chama nota de voz de voice/ptt; aqui é audio
MEDIA_ALIAS = {'voice': 'audio', 'ptt': 'audio'}
STORABLE_TYPES = {'text', 'chat', 'voice', 'ptt', *MEDIA_TYPES}
# @lid também é só dígitos: quem separa telefone de id oculto é o domínio
DIRECT_DOMAINS = {'s.whatsapp.net', 'c.us'}


def _split_jid(jid: str) -> tuple[str, str]:
    user, _, domain = (jid or '').partition('@')
    return user.split(':')[0], domain


def _direct_wa_id(data: dict) -> str | None:
    """Telefone do outro lado da conversa, ou None se não for conversa 1:1 com telefone.

    Em chat @lid, senderPhone resolve, mas é o telefone de quem MANDOU: em mensagem própria
    seríamos nós, então sem telefone do destino a mensagem é descartada.
    """
    if data.get('kind') != 'individual' or data.get('isGroup'):
        return None
    from_me = bool(data.get('fromMe'))
    user, domain = _split_jid(data.get('to') if from_me else data.get('from'))
    if domain not in DIRECT_DOMAINS and not from_me:
        user, domain = _split_jid(data.get('senderPhone'))
    if domain not in DIRECT_DOMAINS or not user.isdigit():
        return None
    return user


def _media_filename(data: dict) -> str:
    media = data.get('media') or (data.get('metadata') or {}).get('media') or {}
    return data.get('filename') or media.get('filename') or ''


def handle_message_webhook(payload: dict) -> None:
    account = _find_account(payload)
    if account is None:
        return
    data = payload.get('data') or {}
    raw_type = data.get('type') or ''
    # descarta antes de tocar no contato: contato sem mensagem vira fantasma na lista
    if raw_type not in STORABLE_TYPES:
        logger.info('mensagem do tipo %r ignorada (sessão %s)', raw_type, account.session_name)
        return
    wa_id = _direct_wa_id(data)
    if wa_id is None:
        return

    from_me = bool(data.get('fromMe'))
    # em mensagem própria o nome é o nosso, não o do contato
    push_name = '' if from_me else ((data.get('contact') or {}).get('name') or data.get('pushName') or '')
    contact, created = PersonalContact.objects.get_or_create(account=account, wa_id=wa_id, defaults={'name': push_name})
    if not created and not contact.name and push_name:
        contact.name = push_name
        contact.save(update_fields=['name'])

    media_type = MEDIA_ALIAS.get(raw_type, raw_type)
    filename = _media_filename(data)
    is_media = media_type in MEDIA_TYPES
    fields = {
        'direction': PersonalMessage.Direction.OUT if from_me else PersonalMessage.Direction.IN,
        'status': PersonalMessage.Status.SENT if from_me else PersonalMessage.Status.RECEIVED,
        'message_type': media_type if is_media else PersonalMessage.Type.TEXT,
        # legenda é o que a pessoa escreveu; sem ela, nome do arquivo diz mais que o placeholder
        'content': (data.get('caption') or data.get('body') or filename or PLACEHOLDERS.get(media_type, '[documento]'))
        if is_media
        else (data.get('body') or ''),
    }
    wa_message_id = data.get('id') or None
    if wa_message_id:
        # eco do que o painel mandou, ou reentrega do mesmo evento: já existe, não duplica
        message, created = PersonalMessage.objects.get_or_create(wa_message_id=wa_message_id, defaults={'contact': contact, **fields})
        if not created:
            return
    else:
        message = PersonalMessage.objects.create(contact=contact, **fields)
    _publish_message(account, contact)
    if is_media:
        from apps.personal.tasks import fetch_message_media

        fetch_message_media.delay(message.pk, filename)


def handle_connection_webhook(payload: dict) -> None:
    account = _find_account(payload)
    if account is None:
        return
    data = payload.get('data') or {}
    status = str(data.get('status') or '')
    if status in LINKED:
        phone = data.get('phone') or ''
        if not phone:
            # o aviso de pronto às vezes chega sem o telefone
            try:
                phone = gateway.session_state(account.session_id).get('phone') or ''
            except (GatewayError, NotFound):
                phone = ''
        _apply_connected(account, phone)
    else:
        pairing = status in PAIRING or account.status == PersonalAccount.Status.CONNECTING
        account.status = PersonalAccount.Status.CONNECTING if pairing else PersonalAccount.Status.DISCONNECTED
        account.save(update_fields=['status'])
    _publish_status(account)


def handle_disconnection_webhook(payload: dict) -> None:
    account = _find_account(payload)
    if account is None:
        return
    data = payload.get('data') or {}
    account.status = PersonalAccount.Status.DISCONNECTED
    account.last_logged_out_reason = str(data.get('reason') or data.get('lastError') or '')[:255]
    account.save(update_fields=['status', 'last_logged_out_reason'])
    _publish_status(account)


EVENT_HANDLERS = {
    'message.received': handle_message_webhook,
    'message.sent': handle_message_webhook,
    'session.status': handle_connection_webhook,
    'session.authenticated': handle_connection_webhook,
    'session.disconnected': handle_disconnection_webhook,
}


def handle_webhook(payload: dict) -> None:
    handler = EVENT_HANDLERS.get(payload.get('event'))
    if handler:
        handler(payload)


# * ----- Contatos ------


def _queue_avatar_sync(account: PersonalAccount) -> None:
    from apps.personal.tasks import stale_avatars, sync_avatars

    if stale_avatars(account).exists():
        sync_avatars.delay(account.pk)


def _agenda(account: PersonalAccount) -> dict[str, str]:
    if not account.session_id:
        return {}
    try:
        return gateway.list_contacts(account.session_id)
    except (GatewayError, NotFound):
        return {}


def _fill_missing_names(account: PersonalAccount, contacts: list[PersonalContact]) -> None:
    missing = [c for c in contacts if not c.name]
    if not missing:
        return
    agenda = _agenda(account)
    changed = []
    for contact in missing:
        if agenda.get(contact.wa_id):
            contact.name = agenda[contact.wa_id]
            changed.append(contact)
    PersonalContact.objects.bulk_update(changed, ['name'])


def _get_own_contact(company: Company, user: User, account_id: int, contact_id: int) -> PersonalContact:
    account = _get_own_or_owned_account(company, user, account_id)
    contact = PersonalContact.objects.filter(account=account, pk=contact_id).select_related('account').first()
    if contact is None:
        raise NotFound('contact not found')
    return contact


def list_contacts(company: Company, user: User, account_id: int) -> list[PersonalContact]:
    account = _get_own_or_owned_account(company, user, account_id)
    last = PersonalMessage.objects.filter(contact=OuterRef('pk')).order_by('-id')
    contacts = list(
        PersonalContact.objects.filter(account=account)
        .annotate(activity=Coalesce(Subquery(last.values('created_at')[:1]), F('created_at')))
        .order_by('-activity')
    )
    _fill_missing_names(account, contacts)
    _queue_avatar_sync(account)
    return contacts


def list_directory(company: Company, user: User, account_id: int) -> list[dict]:
    account = _get_own_or_owned_account(company, user, account_id)
    entries = {wa_id: {'wa_id': wa_id, 'name': name, 'source': 'agenda'} for wa_id, name in _agenda(account).items()}
    for wa_id, name in Contact.objects.filter(account__company=company).values_list('wa_id', 'name'):
        entries.setdefault(wa_id, {'wa_id': wa_id, 'name': name, 'source': 'oficial'})
    return sorted(entries.values(), key=lambda e: e['name'].lower())


def start_conversation(company: Company, user: User, account_id: int, phone: str, name: str = '') -> PersonalContact:
    account = _get_own_or_owned_account(company, user, account_id)
    wa_id = gateway.check_number(account.session_id, normalize_number(phone))
    if not wa_id:
        raise ValidationError('number not on whatsapp')
    contact, _ = PersonalContact.objects.get_or_create(account=account, wa_id=wa_id, defaults={'name': name})
    _queue_avatar_sync(account)
    return contact


def delete_conversation(company: Company, user: User, account_id: int, contact_id: int) -> None:
    _get_own_contact(company, user, account_id, contact_id).delete()


# * ----- Mensagens ------


def list_messages(company: Company, user: User, account_id: int, contact_id: int) -> list[PersonalMessage]:
    contact = _get_own_contact(company, user, account_id, contact_id)
    return list(PersonalMessage.objects.filter(contact=contact).annotate(sticker_saved=sticker_saved_annotation(company)))


def _record_outgoing(contact: PersonalContact, wa_message_id: str, **fields) -> PersonalMessage:
    """Grava o envio tolerando o eco ter chegado antes da resposta HTTP: quem chega primeiro cria, o outro atualiza."""
    defaults = {'contact': contact, 'direction': PersonalMessage.Direction.OUT, 'status': PersonalMessage.Status.SENT, **fields}
    if not wa_message_id:
        return PersonalMessage.objects.create(**defaults)
    message, _ = PersonalMessage.objects.update_or_create(wa_message_id=wa_message_id, defaults=defaults)
    return message


def send_text_message(company: Company, user: User, account_id: int, contact_id: int, content: str) -> PersonalMessage:
    contact = _get_own_contact(company, user, account_id, contact_id)
    wa_message_id = gateway.send_text(contact.account.session_id, contact.wa_id, content)
    return _record_outgoing(contact, wa_message_id, content=content, message_type=PersonalMessage.Type.TEXT)


def send_media_message(company: Company, user: User, account_id: int, contact_id: int, file, caption: str = '') -> PersonalMessage:
    contact = _get_own_contact(company, user, account_id, contact_id)
    kind = file.content_type.split('/')[0]
    if file.content_type == 'image/webp':
        kind = 'sticker'
    elif kind not in ('image', 'audio', 'video'):
        kind = 'document'
    allowed, max_size = MEDIA_SPECS[kind]
    if kind == 'audio':
        allowed = (allowed or set()) | EXTRA_AUDIO
    if allowed is not None and file.content_type not in allowed:
        raise ValidationError(f'unsupported {kind} type: {file.content_type}')
    if file.size > min(max_size, MAX_MEDIA_BYTES):
        raise ValidationError(f'{kind} too large')

    raw = file.read()
    file.seek(0)
    wa_message_id = gateway.send_media(contact.account.session_id, contact.wa_id, raw, kind, file.content_type, file.name, caption)
    return _record_outgoing(
        contact,
        wa_message_id,
        content=caption or PLACEHOLDERS.get(kind) or f'[documento: {file.name}]',
        message_type=kind,
        media_file=file,
        mime_type=file.content_type,
    )


# * ----- Figurinhas ------


def save_sticker(company: Company, user: User, account_id: int, message_id: int) -> Sticker:
    account = _get_own_or_owned_account(company, user, account_id)
    message = PersonalMessage.objects.filter(contact__account=account, pk=message_id, message_type=PersonalMessage.Type.STICKER).first()
    if message is None or not message.media_file:
        raise NotFound('message not found')
    return saved_sticker(company, message.wa_message_id) or Sticker.objects.create(
        company=company, file=ContentFile(message.media_file.read(), name='sticker.webp'), source_wa_message_id=message.wa_message_id or ''
    )


def send_saved_sticker(company: Company, user: User, account_id: int, contact_id: int, sticker_id: int) -> PersonalMessage:
    contact = _get_own_contact(company, user, account_id, contact_id)
    sticker = Sticker.objects.filter(company=company, pk=sticker_id).first()
    if sticker is None:
        raise NotFound('sticker not found')
    raw = sticker.file.read()
    wa_message_id = gateway.send_media(contact.account.session_id, contact.wa_id, raw, 'sticker', 'image/webp')
    return _record_outgoing(
        contact,
        wa_message_id,
        content='[figurinha]',
        message_type=PersonalMessage.Type.STICKER,
        media_file=ContentFile(raw, name='sticker.webp'),
        mime_type='image/webp',
    )
