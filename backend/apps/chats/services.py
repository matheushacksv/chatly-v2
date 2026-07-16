import hashlib
import hmac
import re
from datetime import timedelta

import httpx
from django.core.files.base import ContentFile
from django.db import IntegrityError
from django.db.models import Count, F, OuterRef, Q, QuerySet, Subquery
from django.utils import timezone

from apps.agents.services import get_agent
from apps.chats import events, whatsapp
from apps.chats.models import Campaign, Contact, Message, Sticker, WhatsAppAccount
from apps.companies.models import Company
from core.exceptions import Conflict, NotFound, ValidationError

MEDIA_SPECS = {
    'image': ({'image/jpeg', 'image/png'}, 5 * 2**20),
    'audio': ({'audio/aac', 'audio/mp4', 'audio/mpeg', 'audio/amr', 'audio/ogg'}, 16 * 2**20),
    'video': ({'video/mp4', 'video/3gpp'}, 16 * 2**20),
    'sticker': ({'image/webp'}, 500 * 2**10),
    'document': (None, 100 * 2**20),
}
PLACEHOLDERS = {'image': '[imagem]', 'audio': '[áudio]', 'video': '[vídeo]', 'sticker': '[figurinha]'}
TEMPLATE_PARAM_RE = re.compile(r'\{\{(\d+)\}\}')
NAME_TOKEN = '{{nome}}'
SERVICE_WINDOW = timedelta(hours=24)

# * ------ Helpers ------


def normalize_number(number: str) -> str:
    wa_id = re.sub(r'\D', '', number)
    if not 10 <= len(wa_id) <= 15:
        raise ValidationError(f'invalid number: {number}')
    return wa_id


def check_service_window(contact: Contact) -> None:
    last_in = Message.objects.filter(contact=contact, direction='in').order_by('-created_at').first()
    if last_in is None or timezone.now() - last_in.created_at > SERVICE_WINDOW:
        raise ValidationError('service window closed. Only templates allowed')


# * ------ WhatsApp Accounts -----


def create_account(
    company: Company, agent_id: int, phone_number_id: str, waba_id: str, access_token: str, app_secret: str, verify_token: str
) -> WhatsAppAccount:
    agent = get_agent(company, agent_id)
    try:
        whatsapp = WhatsAppAccount.objects.create(
            company=company,
            agent=agent,
            phone_number_id=phone_number_id,
            waba_id=waba_id,
            access_token=access_token,
            app_secret=app_secret,
            verify_token=verify_token,
        )
    except IntegrityError:
        raise Conflict('already exists whatsapp created')
    return whatsapp


def get_account(company: Company, account_id: int) -> WhatsAppAccount:
    account = WhatsAppAccount.objects.filter(company=company, pk=account_id).first()
    if account is None:
        raise NotFound('whatsapp account not found')
    return account


def list_accounts(company: Company) -> QuerySet[WhatsAppAccount]:
    return WhatsAppAccount.objects.filter(company=company)


def update_account(company: Company, account_id: int, data: dict) -> WhatsAppAccount:
    account = get_account(company, account_id)

    agent_id = data.pop('agent_id', None)
    if agent_id is not None:
        account.agent = get_agent(company, agent_id)

    for field, value in data.items():
        setattr(account, field, value)
    account.save()
    return account


def delete_account(company: Company, account_id: int) -> None:
    get_account(company, account_id).delete()


# * ------ Contacts ------


def get_contact(company: Company, contact_id: int) -> Contact:
    contact = Contact.objects.filter(account__company=company, pk=contact_id).first()
    if contact is None:
        raise NotFound('contact not found')
    return contact


def create_contact(company: Company, account_id: int, number: str, name: str = '') -> Contact:
    account = get_account(company, account_id)
    return get_or_create_contact(account, normalize_number(number), name)


def import_contacts(company: Company, account_id: int, numbers: list[dict]) -> dict:
    account = get_account(company, account_id)
    if not numbers:
        raise ValidationError('no contacts to import')
    entries = [(normalize_number(e['number']), e.get('name', '')) for e in numbers]
    created = 0
    for wa_id, name in entries:
        _, was_created = Contact.objects.get_or_create(account=account, wa_id=wa_id, defaults={'name': name})
        created += was_created
    return {'created': created, 'existing': len(entries) - created}


def list_contacts(company: Company, account_id: int, search: str = '') -> QuerySet[Contact]:
    account = get_account(company, account_id)
    last = Message.objects.filter(contact=OuterRef('pk')).order_by('-id')
    last_in = Message.objects.filter(contact=OuterRef('pk'), direction='in').order_by('-id')
    qs = Contact.objects.filter(account=account)
    if search:
        qs = qs.filter(Q(name__icontains=search) | Q(wa_id__contains=search))
    return qs.annotate(
        last_message=Subquery(last.values('content')[:1]),
        last_message_at=Subquery(last.values('created_at')[:1]),
        last_inbound_at=Subquery(
            last_in.values('created_at')[:1],
        ),
    ).order_by(F('last_message_at').desc(nulls_last=True))


def update_contact(company: Company, contact_id: int, data: dict) -> Contact:
    contact = get_contact(company, contact_id)
    for field, value in data.items():
        setattr(contact, field, value)
    contact.save()
    events.publish(contact.account.company_id, {'type': 'contact', 'contact_id': contact.pk})
    return contact


def delete_contact(company: Company, contact_id: int) -> None:
    get_contact(company, contact_id).delete()


# * ------ Messages ------


def list_messages(company: Company, contact_id: int) -> QuerySet[Message]:
    contact = get_contact(company, contact_id)
    return Message.objects.filter(contact=contact).order_by('-id')


def send_manual_message(company: Company, contact_id: int, content: str) -> Message:
    contact = get_contact(company, contact_id)
    check_service_window(contact)
    try:
        wamid = whatsapp.send_text(contact.account, contact.wa_id, content)
    except httpx.HTTPStatusError:
        raise ValidationError('whatsapp invalid send')
    return record_outgoing_message(contact, wamid, content, from_human=True)


def send_manual_media(company: Company, contact_id: int, file, caption: str = '') -> Message:
    contact = get_contact(company, contact_id)
    check_service_window(contact)
    kind = file.content_type.split('/')[0]
    if file.content_type == 'image/webp':
        kind = 'sticker'
    elif kind not in ('image', 'audio', 'video'):
        kind = 'document'
    allowed_mimes, max_size = MEDIA_SPECS[kind]
    if allowed_mimes is not None and file.content_type not in allowed_mimes:
        raise ValidationError(f'unsupported {kind} type: {file.content_type}')
    if file.size > max_size:
        limit = f'{max_size // 2**20}MB' if max_size >= 2**20 else f'{max_size // 2**10}KB'
        raise ValidationError(f'{kind} exceeds {limit} limit')
    content_bytes = file.read()
    try:
        media_id = whatsapp.upload_media(contact.account, content_bytes, file.content_type, file.name)
        waimd = whatsapp.send_media(contact.account, contact.wa_id, kind, media_id, caption, file.name)
    except httpx.HTTPStatusError:
        raise ValidationError('whatsapp invalid send')
    file.seek(0)
    content = caption or PLACEHOLDERS.get(kind) or f'[documento: {file.name}]'
    return record_outgoing_message(contact, waimd, content, from_human=True, message_type=kind, media_file=file)


def get_message_media(company: Company, message_id: int) -> tuple[bytes, str]:
    message = Message.objects.select_related('contact__account').filter(contact__account__company=company, pk=message_id).first()
    if message is None or not message.media_id:
        raise NotFound('media not found')
    try:
        return whatsapp.download_media(message.contact.account, message.media_id)
    except httpx.HTTPStatusError:
        raise NotFound('media expired')


def save_sticker(company: Company, message_id: int) -> Sticker:
    message = (
        Message.objects.select_related('contact__account').filter(contact__account__company=company, pk=message_id, message_type='sticker').first()
    )
    if message is None:
        raise NotFound('sticker not found')
    if message.media_file:
        content = message.media_file.read()
    else:
        try:
            content, _ = whatsapp.download_media(message.contact.account, message.media_id)
        except httpx.HTTPStatusError:
            raise NotFound('media expired')
    return Sticker.objects.create(company=company, file=ContentFile(content, name='sticker.webp'))


def list_stickers(company: Company) -> QuerySet[Sticker]:
    return Sticker.objects.filter(company=company).order_by('-id')


def delete_sticker(company: Company, sticker_id: int) -> None:
    sticker = Sticker.objects.filter(company=company, pk=sticker_id).first()
    if sticker is None:
        raise NotFound('sticker not found')
    sticker.delete()


def send_saved_sticker(company: Company, contact_id: int, sticker_id: int) -> Message:
    contact = get_contact(company, contact_id)
    check_service_window(contact)
    sticker = Sticker.objects.filter(company=company, pk=sticker_id).first()
    if sticker is None:
        raise NotFound('sticker not found')
    content = sticker.file.read()
    try:
        media_id = whatsapp.upload_media(contact.account, content, 'image/webp', 'sticker.webp')
        wamid = whatsapp.send_media(contact.account, contact.wa_id, 'sticker', media_id)
    except httpx.HTTPStatusError:
        raise ValidationError('whatsapp invalid send')
    return record_outgoing_message(
        contact, wamid, '[figurinha]', from_human=True, message_type='sticker', media_file=ContentFile(content, name='sticker.webp')
    )


# * ----- Templates ------


def list_templates(company: Company, account_id: int) -> list[dict]:
    account = get_account(company, account_id)
    templates = []
    for template in whatsapp.list_templates(account):
        body = next((c.get('text', '') for c in template.get('components', []) if c.get('type') == 'BODY'), '')
        params = TEMPLATE_PARAM_RE.findall(body)
        templates.append(
            {
                'name': template['name'],
                'language': template['language'],
                'status': template['status'],
                'category': template.get('category', ''),
                'body_text': body,
                'param_count': max((int(p) for p in params), default=0),
            }
        )
    return templates


def get_template_body(account: WhatsAppAccount, name: str, language: str) -> str:
    for template in whatsapp.list_templates(account):
        if template['name'] == name and template['language'] == language and template['status'] == 'APPROVED':
            return next((c.get('text', '') for c in template.get('components', []) if c.get('type') == 'BODY'), '')
    raise ValidationError('template not found')


def resolve_params(params: list[str], contact: Contact) -> list[str]:
    return [contact.name or contact.wa_id if p == NAME_TOKEN else p for p in params]


def render_template(body: str, params: list[str], contact: Contact) -> str:
    resolved = resolve_params(params, contact)
    return TEMPLATE_PARAM_RE.sub(lambda m: resolved[int(m.group(1)) - 1], body)


def send_template_message(company: Company, contact_id: int, template_name: str, language: str, params: list[str]) -> Message:
    contact = get_contact(company, contact_id)
    body = get_template_body(contact.account, template_name, language)
    try:
        wamid = whatsapp.send_template(contact.account, contact.wa_id, template_name, language, resolve_params(params, contact))
    except httpx.HTTPStatusError:
        raise ValidationError('whatsapp invalid send')
    return record_outgoing_message(contact, wamid, render_template(body, params, contact), from_human=True)


# * ----- Campaigns ------


def create_campaign(
    company: Company,
    account_id: int,
    name: str,
    template_name: str,
    template_language: str,
    body_params: list[str],
    contact_ids: list[int] | None = None,
    numbers: list[dict] | None = None,
) -> Campaign:
    from apps.chats.tasks import run_campaign

    account = get_account(company, account_id)
    body = get_template_body(account, template_name, template_language)
    param_count = max((int(p) for p in TEMPLATE_PARAM_RE.findall(body)), default=0)
    if len(body_params) != param_count:
        raise ValidationError(f'template expects {param_count} params, got {len(body_params)}')

    if contact_ids is None and not numbers:
        recipients = list(Contact.objects.filter(account=account))
    else:
        recipients = list(Contact.objects.filter(account=account, pk__in=contact_ids or []))
    for entry in numbers or []:
        recipients.append(get_or_create_contact(account, normalize_number(entry['number']), entry.get('name', '')))
    if not recipients:
        raise ValidationError('campaign has no recipients')

    campaign = Campaign.objects.create(
        account=account,
        name=name,
        template_name=template_name,
        template_language=template_language,
        body_params=body_params,
        total=len(recipients),
    )
    run_campaign.delay(campaign.pk, [c.pk for c in recipients])
    return campaign


def list_campaigns(company: Company) -> QuerySet[Campaign]:
    return Campaign.objects.filter(account__company=company).order_by('-id')


def get_campaign(company: Company, campaign_id: int) -> Campaign:
    campaign = (
        Campaign.objects.filter(account__company=company, pk=campaign_id)
        .annotate(
            delivered=Count('messages', filter=Q(messages__status='delivered')),
            read=Count('messages', filter=Q(messages__status='read')),
        )
        .first()
    )
    if campaign is None:
        raise NotFound('campaign not found')
    return campaign


# * ----- Webhooks ------


def verify_webhook_token(token: str) -> bool:
    return WhatsAppAccount.objects.filter(verify_token=token).exists()


def verify_webhook_signature(account: WhatsAppAccount, body: bytes, signature_header: str) -> bool:
    expected = hmac.new(account.app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f'sha256={expected}', signature_header)


def get_account_by_phone_number_id(phone_number_id: str) -> WhatsAppAccount | None:
    return WhatsAppAccount.objects.filter(phone_number_id=phone_number_id).first()


def get_or_create_contact(account: WhatsAppAccount, wa_id: str, name: str = '') -> Contact:
    contact, _ = Contact.objects.get_or_create(account=account, wa_id=wa_id, defaults={'name': name})
    return contact


def record_incoming_message(
    contact: Contact, wa_mesage_id: str, content: str, message_type: str = 'text', media_id: str = ''
) -> tuple[Message, bool]:
    message, created = Message.objects.get_or_create(
        wa_message_id=wa_mesage_id,
        defaults={'contact': contact, 'direction': 'in', 'content': content, 'message_type': message_type, 'media_id': media_id},
    )
    events.publish(contact.account.company_id, {'type': 'message', 'contact_id': contact.pk})
    return message, created


def update_message_status(wa_message_id: str, status: str) -> None:
    Message.objects.filter(wa_message_id=wa_message_id).update(status=status)


def record_outgoing_message(
    contact: Contact, wa_message_id: str, content: str, from_human=False, message_type='text', media_file=None, campaign=None
) -> Message:
    message = Message.objects.create(
        contact=contact,
        direction='out',
        content=content,
        wa_message_id=wa_message_id,
        status='sent',
        from_human=from_human,
        message_type=message_type,
        media_file=media_file,
        campaign=campaign,
    )
    events.publish(contact.account.company_id, {'type': 'message', 'contact_id': contact.pk})
    return message


# * ----- IA ------

ROLE_BY_DIRECTION = {'in': 'user', 'out': 'assistant'}


def build_history(contact: Contact, exclude_pk: int | None = None, limit: int = 20) -> list[dict]:
    queryset = Message.objects.filter(contact=contact).exclude(content='')
    if exclude_pk is not None:
        queryset = queryset.exclude(pk=exclude_pk)
    messages = reversed(queryset.order_by('-created_at')[:limit])
    return [{'role': ROLE_BY_DIRECTION[m.direction], 'content': m.content} for m in messages]


def clear_contact_memory(company: Company, contact_id: int) -> None:
    contact = get_contact(company, contact_id)
    contact.memory = ''
    contact.save(update_fields=['memory'])
