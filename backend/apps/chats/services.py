import hashlib
import hmac
import re
from datetime import datetime, timedelta

import httpx
import phonenumbers
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import UploadedFile
from django.db import IntegrityError, transaction
from django.db.models import Count, Exists, F, OuterRef, Q, QuerySet, Subquery
from django.utils import timezone
from django.utils.text import slugify

from apps.agents.services import get_agent
from apps.chats import events, whatsapp
from apps.chats.models import Campaign, Contact, ContactField, FollowUp, Message, ToolCall, Sticker, Tag, TemplateHeaderMedia, WhatsAppAccount
from apps.companies.models import Company, Membership
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
VAR_RE = re.compile(r'\{\{\s*(\w+)\s*\}\}')
# variáveis embutidas: campo personalizado não pode ter esses nomes
RESERVED_VARS = {'nome', 'telefone', 'botao', 'campanha'}
TEMPLATE_NAME_RE = re.compile(r'^[a-z0-9_]+$')
SERVICE_WINDOW = timedelta(hours=24)

# * ------ Helpers ------


def normalize_number(number: str) -> str:
    # sem '+' assume BR (11999998888 -> 5511999998888); com '+' respeita o DDI.
    # is_possible e não is_valid: o formato antigo sem o 9 (12 dígitos) é "inválido", mas é o que a Meta usa
    try:
        parsed = phonenumbers.parse(number, 'BR')
    except phonenumbers.NumberParseException:
        raise ValidationError(f'invalid number: {number}')
    if not phonenumbers.is_possible_number(parsed):
        raise ValidationError(f'invalid number: {number}')
    return f'{parsed.country_code}{parsed.national_number}'


def number_variants(wa_id: str) -> list[str]:
    # celular BR com e sem o 9º dígito: a Meta manda 12 dígitos pra muitos números antigos
    if wa_id.startswith('55') and len(wa_id) == 12 and wa_id[4] in '6789':
        return [wa_id, f'{wa_id[:4]}9{wa_id[4:]}']
    if wa_id.startswith('55') and len(wa_id) == 13 and wa_id[4] == '9' and wa_id[5] in '6789':
        return [wa_id, wa_id[:4] + wa_id[5:]]
    return [wa_id]


def service_window_open(contact: Contact) -> bool:
    last_in = Message.objects.filter(contact=contact, direction='in').order_by('-created_at').first()
    return last_in is not None and timezone.now() - last_in.created_at <= SERVICE_WINDOW


def check_service_window(contact: Contact) -> None:
    if not service_window_open(contact):
        raise ValidationError('service window closed. Only templates allowed')


def upload_account_media(company: Company, account_id: int, file: UploadedFile) -> str:
    account = get_account(company, account_id)
    return whatsapp.upload_media(account, file.read(), file.content_type or '', file.name or 'arquivo')


def _save_header_media(account: WhatsAppAccount, name: str, language: str, header: dict | None) -> None:
    if not header or header.get('format') not in ('IMAGE', 'VIDEO', 'DOCUMENT') or not header.get('media_id'):
        return
    TemplateHeaderMedia.objects.update_or_create(
        account=account,
        template_name=name,
        template_language=language,
        defaults={'media_id': header['media_id'], 'media_type': header['format'].lower()},
    )


def _get_saved_header_media(account: WhatsAppAccount, name: str, language: str) -> str:
    saved = TemplateHeaderMedia.objects.filter(account=account, template_name=name, template_language=language).first()
    return saved.media_id if saved else ''


def _map_template_detail(account: WhatsAppAccount, template: dict) -> dict:
    components = template.get('components', [])
    header_c = next((c for c in components if c['type'] == 'HEADER'), None)
    body_c = next((c for c in components if c['type'] == 'BODY'), {})
    footer_c = next((c for c in components if c['type'] == 'FOOTER'), None)
    buttons_c = next((c for c in components if c['type'] == 'BUTTONS'), None)

    header = None
    if header_c:
        header = {
            'format': header_c.get('format', 'TEXT'),
            'text': header_c.get('text', ''),
            'example': (header_c.get('example', {}).get('header_text') or [''])[0],
        }

    has_header_media = TemplateHeaderMedia.objects.filter(
        account=account, template_name=template['name'], template_language=template['language']
    ).exists()

    return {
        'id': template['id'],
        'name': template['name'],
        'language': template['language'],
        'category': template['category'],
        'status': template['status'],
        'header': header,
        'has_header_media': has_header_media,
        'body_text': body_c.get('text', ''),
        'body_examples': (body_c.get('example', {}).get('body_text') or [[]])[0],
        'footer_text': footer_c.get('text', '') if footer_c else '',
        'buttons': [
            {
                'type': btn['type'],
                'text': btn.get('text', ''),
                'url': btn.get('url', ''),
                'phone_number': btn.get('phone_number', ''),
            }
            for btn in (buttons_c or {}).get('buttons', [])
        ],
    }


# * ------ WhatsApp Accounts -----


def create_account(
    company: Company, agent_id: int, phone_number_id: str, waba_id: str, access_token: str, app_secret: str, verify_token: str, app_id: str = ''
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
            app_id=app_id,
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


def register_account(company: Company, account_id: int, pin: str) -> None:
    account = get_account(company, account_id)
    whatsapp.register_number(account, pin)


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
    entries = [(normalize_number(e['number']), e.get('name', ''), e.get('fields') or {}) for e in numbers]
    known = _field_keys(company)
    created = 0
    for wa_id, name, fields in entries:
        if find_contact(account, wa_id) is None:
            created += 1
        _merge_import_fields(get_or_create_contact(account, wa_id, name), fields, known)
    return {'created': created, 'existing': len(entries) - created}


def _field_keys(company: Company) -> set[str]:
    return set(ContactField.objects.filter(company=company).values_list('key', flat=True))


def _merge_import_fields(contact: Contact, fields: dict, known: set[str]) -> None:
    """Valores do CSV sobrescrevem os do contato; coluna vazia ou que não é campo cadastrado é ignorada."""
    values = {k: v.strip() for k, v in fields.items() if k in known and v.strip()}
    if values:
        contact.custom_fields |= values
        contact.save(update_fields=['custom_fields'])


def list_contacts(company: Company, account_id: int, search: str = '', queue: str = '', user=None, tag: int | None = None) -> QuerySet[Contact]:
    account = get_account(company, account_id)
    last = Message.objects.filter(contact=OuterRef('pk')).order_by('-id')
    last_in = Message.objects.filter(contact=OuterRef('pk'), direction='in').order_by('-id')
    qs = Contact.objects.filter(account=account).select_related('account__agent').prefetch_related('tags')
    if tag:
        qs = qs.filter(tags=tag)
    if search:
        qs = qs.filter(Q(name__icontains=search) | Q(wa_id__contains=search))
    if queue == 'waiting':
        qs = qs.filter(ai_paused=True, assigned_to__isnull=True)
    elif queue == 'mine':
        qs = qs.filter(assigned_to=user)
    return qs.annotate(
        last_message=Subquery(last.values('content')[:1]),
        last_message_at=Subquery(last.values('created_at')[:1]),
        last_inbound_at=Subquery(
            last_in.values('created_at')[:1],
        ),
    ).order_by(F('last_message_at').desc(nulls_last=True))


def update_contact(company: Company, contact_id: int, data: dict) -> Contact:
    contact = get_contact(company, contact_id)
    if 'assigned_to' in data:
        user_id = data.pop('assigned_to')
        if user_id is not None and not is_member(company, user_id):
            raise ValidationError('user is not a member of this company')
        contact.assigned_to_id = user_id
    if 'ai_paused' in data:
        paused = data.pop('ai_paused')
        contact.ai_paused = paused
        contact.ai_resume_at = resume_deadline(contact) if paused else None
    if data.get('custom_fields') is not None:
        values = data.pop('custom_fields')
        if unknown := set(values) - _field_keys(company):
            raise ValidationError(f'campo desconhecido: {", ".join(sorted(unknown))}')
        for key, value in values.items():
            # valor vazio apaga o campo do contato
            if value.strip():
                contact.custom_fields[key] = value.strip()
            else:
                contact.custom_fields.pop(key, None)
    data.pop('custom_fields', None)
    for field, value in data.items():
        setattr(contact, field, value)
    contact.save()
    events.publish(contact.account.company_id, {'type': 'contact', 'contact_id': contact.pk})
    return contact


# * ----- Etiquetas ------


def list_tags(company: Company) -> QuerySet[Tag]:
    return Tag.objects.filter(company=company).order_by('name')


def get_tag(company: Company, tag_id: int) -> Tag:
    tag = Tag.objects.filter(company=company, pk=tag_id).first()
    if tag is None:
        raise NotFound('tag not found')
    return tag


def create_tag(company: Company, name: str, color: str = 'neutral') -> Tag:
    name = name.strip()
    if not name:
        raise ValidationError('nome da etiqueta vazio')
    # mesma etiqueta com outra caixa: devolve a existente (o fluxo e o painel podem criar ao mesmo tempo)
    existing = Tag.objects.filter(company=company, name__iexact=name).first()
    if existing:
        return existing
    try:
        return Tag.objects.create(company=company, name=name, color=color)
    except IntegrityError:
        return Tag.objects.get(company=company, name__iexact=name)


def delete_tag(company: Company, tag_id: int) -> None:
    get_tag(company, tag_id).delete()


# * ----- Campos personalizados ------


def list_contact_fields(company: Company) -> QuerySet[ContactField]:
    return ContactField.objects.filter(company=company).order_by('label')


def create_contact_field(company: Company, label: str) -> ContactField:
    label = label.strip()
    key = slugify(label).replace('-', '_')[:40]
    if not key:
        raise ValidationError('nome do campo inválido')
    if key in RESERVED_VARS:
        raise ValidationError(f'{{{{{key}}}}} já é uma variável do sistema, escolha outro nome')
    try:
        with transaction.atomic():
            return ContactField.objects.create(company=company, key=key, label=label)
    except IntegrityError:
        raise Conflict(f'já existe um campo com a variável {{{{{key}}}}}')


def delete_contact_field(company: Company, field_id: int) -> None:
    # ponytail: o valor fica órfão no JSON dos contatos (invisível e ignorado); limpar em lote se o volume pesar
    deleted, _ = ContactField.objects.filter(company=company, pk=field_id).delete()
    if not deleted:
        raise NotFound('field not found')


def contact_variables(contact: Contact) -> dict[str, str]:
    """Valores das variáveis {{...}} de um contato: nome, telefone e os campos personalizados."""
    return {**contact.custom_fields, 'nome': contact.name or contact.wa_id, 'telefone': contact.wa_id}


def _publish_contact(contact: Contact) -> None:
    events.publish(contact.account.company_id, {'type': 'contact', 'contact_id': contact.pk})


def add_contact_tag(company: Company, contact_id: int, tag_id: int) -> Contact:
    contact = get_contact(company, contact_id)
    contact.tags.add(get_tag(company, tag_id))
    _publish_contact(contact)
    return contact


def remove_contact_tag(company: Company, contact_id: int, tag_id: int) -> Contact:
    contact = get_contact(company, contact_id)
    contact.tags.remove(get_tag(company, tag_id))
    _publish_contact(contact)
    return contact


def is_member(company: Company, user_id: int) -> bool:
    return company.owner_id == user_id or Membership.objects.filter(company=company, user_id=user_id).exists()


def resume_deadline(contact: Contact):
    hours = contact.account.agent.handoff_resume_hours
    return timezone.now() + timedelta(hours=hours) if hours else None


def pause_ai(contact: Contact) -> None:
    """Pausa a IA até o prazo do agente (renova a cada chamada: conta da última ação humana)."""
    contact.ai_paused = True
    contact.ai_resume_at = resume_deadline(contact)
    contact.save(update_fields=['ai_paused', 'ai_resume_at'])
    events.publish(contact.account.company_id, {'type': 'contact', 'contact_id': contact.pk})


def resume_ai(contact: Contact) -> None:
    contact.ai_paused = False
    contact.ai_resume_at = None
    contact.save(update_fields=['ai_paused', 'ai_resume_at'])
    events.publish(contact.account.company_id, {'type': 'contact', 'contact_id': contact.pk})


def take_over(contact: Contact, user) -> None:
    """Atendente escreveu: IA pausa e, se ninguém tinha pegado, a conversa fica com ele."""
    if contact.assigned_to_id is None and user is not None:
        contact.assigned_to = user
        contact.save(update_fields=['assigned_to'])
    pause_ai(contact)


def delete_contact(company: Company, contact_id: int) -> None:
    get_contact(company, contact_id).delete()


# * ------ Messages ------


def list_messages(company: Company, contact_id: int) -> QuerySet[Message]:
    contact = get_contact(company, contact_id)
    return Message.objects.filter(contact=contact).annotate(sticker_saved=sticker_saved_annotation(company)).order_by('-id')


def send_manual_message(company: Company, contact_id: int, content: str, user=None) -> Message:
    contact = get_contact(company, contact_id)
    check_service_window(contact)
    try:
        wamid = whatsapp.send_text(contact.account, contact.wa_id, content)
    except httpx.HTTPStatusError as e:
        raise ValidationError(f'whatsapp invalid send: {whatsapp.graph_error_message(e.response)}')
    take_over(contact, user)
    return record_outgoing_message(contact, wamid, content, from_human=True)


def send_manual_media(company: Company, contact_id: int, file, caption: str = '', user=None) -> Message:
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
    except httpx.HTTPStatusError as e:
        raise ValidationError(f'whatsapp invalid send: {whatsapp.graph_error_message(e.response)}')
    file.seek(0)
    take_over(contact, user)
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
    existing = saved_sticker(company, message.wa_message_id)
    if existing:
        return existing
    if message.media_file:
        content = message.media_file.read()
    else:
        try:
            content, _ = whatsapp.download_media(message.contact.account, message.media_id)
        except httpx.HTTPStatusError:
            raise NotFound('media expired')
    return Sticker.objects.create(company=company, file=ContentFile(content, name='sticker.webp'), source_wa_message_id=message.wa_message_id or '')


def saved_sticker(company: Company, wa_message_id: str | None) -> Sticker | None:
    return Sticker.objects.filter(company=company, source_wa_message_id=wa_message_id).first() if wa_message_id else None


def sticker_saved_annotation(company: Company):
    """Exists pra anotar mensagens (de qualquer canal) com 'essa figurinha já está na galeria'."""
    return Exists(Sticker.objects.filter(company=company, source_wa_message_id=OuterRef('wa_message_id')).exclude(source_wa_message_id=''))


def list_stickers(company: Company) -> QuerySet[Sticker]:
    return Sticker.objects.filter(company=company).order_by('-id')


def delete_sticker(company: Company, sticker_id: int) -> None:
    sticker = Sticker.objects.filter(company=company, pk=sticker_id).first()
    if sticker is None:
        raise NotFound('sticker not found')
    sticker.delete()


def send_saved_sticker(company: Company, contact_id: int, sticker_id: int, user=None) -> Message:
    contact = get_contact(company, contact_id)
    check_service_window(contact)
    sticker = Sticker.objects.filter(company=company, pk=sticker_id).first()
    if sticker is None:
        raise NotFound('sticker not found')
    content = sticker.file.read()
    try:
        media_id = whatsapp.upload_media(contact.account, content, 'image/webp', 'sticker.webp')
        wamid = whatsapp.send_media(contact.account, contact.wa_id, 'sticker', media_id)
    except httpx.HTTPStatusError as e:
        raise ValidationError(f'whatsapp invalid send: {whatsapp.graph_error_message(e.response)}')
    take_over(contact, user)
    return record_outgoing_message(
        contact, wamid, '[figurinha]', from_human=True, message_type='sticker', media_file=ContentFile(content, name='sticker.webp')
    )


# * ----- Templates ------


def list_templates(company: Company, account_id: int) -> list[dict]:
    account = get_account(company, account_id)
    saved_names = set(TemplateHeaderMedia.objects.filter(account=account).values_list('template_name', 'template_language'))
    templates = []
    for template in whatsapp.list_templates(account):
        body = next((c.get('text', '') for c in template.get('components', []) if c.get('type') == 'BODY'), '')
        params = TEMPLATE_PARAM_RE.findall(body)
        header = next((c for c in template.get('components', []) if c.get('type') == 'HEADER'), None)
        templates.append(
            {
                'id': template.get('id', ''),
                'name': template['name'],
                'language': template['language'],
                'status': template['status'],
                'category': template.get('category', ''),
                'header_format': header.get('format', 'NONE') if header else 'NONE',
                'has_header_media': (template['name'], template['language']) in saved_names,
                'body_text': body,
                'param_count': max((int(p) for p in params), default=0),
            }
        )
    return templates


def get_template_send_info(account: WhatsAppAccount, name: str, language: str) -> tuple[str, str]:
    for template in whatsapp.list_templates(account):
        if template['name'] == name and template['language'] == language and template['status'] == 'APPROVED':
            body = next((c.get('text', '') for c in template.get('components', []) if c.get('type') == 'BODY'), '')
            header = next((c for c in template.get('components', []) if c.get('type') == 'HEADER'), None)
            header_format = header.get('format', 'NONE') if header else 'NONE'
            return body, header_format
    raise ValidationError('template not found')


def resolve_params(params: list[str], contact: Contact) -> list[str]:
    """Troca {{nome}}, {{telefone}} e {{campo}} em cada variável. Campo sem valor no contato vira vazio e,
    se a variável inteira ficar vazia, recusa (a Meta não aceita parâmetro em branco)."""
    values = contact_variables(contact)
    resolved = []
    for p in params:
        value = VAR_RE.sub(lambda m: values.get(m.group(1), m.group(0) if m.group(1) in RESERVED_VARS else ''), p)
        if not value.strip():
            raise ValidationError(f'{contact.name or contact.wa_id} está sem valor para {p}')
        resolved.append(value)
    return resolved


def render_template(body: str, params: list[str], contact: Contact) -> str:
    resolved = resolve_params(params, contact)
    return TEMPLATE_PARAM_RE.sub(lambda m: resolved[int(m.group(1)) - 1], body)


def send_template_message(
    company: Company, contact_id: int, template_name: str, language: str, params: list[str], header_media_id: str = ''
) -> Message:
    contact = get_contact(company, contact_id)
    body, header_format = get_template_send_info(contact.account, template_name, language)
    if header_format in ('IMAGE', 'VIDEO', 'DOCUMENT') and not header_media_id:
        header_media_id = _get_saved_header_media(contact.account, template_name, language)
    if header_format in ('IMAGE', 'VIDEO', 'DOCUMENT') and not header_media_id:
        raise ValidationError('esse template exige um arquivo no cabeçalho, envie antes de mandar')
    header_media_type = header_format.lower() if header_media_id else ''
    try:
        wamid = whatsapp.send_template(
            contact.account, contact.wa_id, template_name, language, resolve_params(params, contact), header_media_id, header_media_type
        )
    except httpx.HTTPStatusError as e:
        raise ValidationError(f'whatsapp invalid send: {whatsapp.graph_error_message(e.response)}')
    return record_outgoing_message(contact, wamid, render_template(body, params, contact), from_human=True)


def build_components(data: dict) -> list[dict]:
    body_text = data['body_text']
    body_examples = data.get('body_examples', [])
    footer_text = data.get('footer_text', '')
    header = data.get('header')
    buttons = data.get('buttons') or []

    param_count = max((int(p) for p in TEMPLATE_PARAM_RE.findall(body_text)), default=0)
    if len(body_examples) != param_count:
        raise ValidationError(f'body espera {param_count} exemplo(s), recebeu {len(body_examples)}')
    if len(footer_text) > 60:
        raise ValidationError('footer deve ter no máximo 60 caracteres')

    components = []

    if header and header.get('format', 'NONE') != 'NONE':
        header_component: dict = {'type': 'HEADER', 'format': header['format']}
        if header['format'] == 'TEXT':
            header_component['text'] = header.get('text', '')
            if header.get('example'):
                header_component['example'] = {'header_text': [header['example']]}
        elif header['format'] in ('IMAGE', 'VIDEO', 'DOCUMENT'):
            if not header.get('handle'):
                raise ValidationError('cabeçalho de mídia exige o upload do arquivo de exemplo')
            header_component['example'] = {'header_handle': [header['handle']]}
        components.append(header_component)

    body: dict = {'type': 'BODY', 'text': body_text}
    if body_examples:
        body['example'] = {'body_text': [body_examples]}
    components.append(body)

    if footer_text:
        components.append({'type': 'FOOTER', 'text': footer_text})

    if buttons:
        button_components = []
        for btn in buttons:
            button: dict = {'type': btn['type'], 'text': btn['text']}
            if btn['type'] == 'URL':
                if not btn.get('url'):
                    raise ValidationError('botão URL exige url')
                button['url'] = btn['url']
            elif btn['type'] == 'PHONE_NUMBER':
                if not btn.get('phone_number'):
                    raise ValidationError('botão de telefone exige phone_number')
                button['phone_number'] = btn['phone_number']
            button_components.append(button)
        components.append({'type': 'BUTTONS', 'buttons': button_components})

    return components


def upload_template_media(company: Company, account_id: int, file: UploadedFile) -> dict:
    account = get_account(company, account_id)
    content = file.read()
    mime = file.content_type or ''
    if mime in MEDIA_SPECS['image'][0]:
        max_size = MEDIA_SPECS['image'][1]
    elif mime in MEDIA_SPECS['video'][0]:
        max_size = MEDIA_SPECS['video'][1]
    elif mime == 'application/pdf':
        max_size = MEDIA_SPECS['document'][1]
    else:
        raise ValidationError('cabeçalho aceita imagem (jpeg/png), vídeo (mp4/3gpp) ou PDF')
    if len(content) > max_size:
        raise ValidationError('arquivo excede o tamanho máximo permitido')
    handle = whatsapp.upload_sample_media(account, content, mime, file.name or 'arquivo')
    media_id = whatsapp.upload_media(account, content, mime, file.name or 'arquivo')
    return {'handle': handle, 'media_id': media_id}


def create_template(company: Company, account_id: int, data: dict) -> dict:
    if not TEMPLATE_NAME_RE.match(data['name']):
        raise ValidationError('nome deve conter só letras minúsculas, números e underscore')
    account = get_account(company, account_id)
    payload = {'name': data['name'], 'language': data['language'], 'category': data['category'], 'components': build_components(data)}
    result = whatsapp.create_template(account, payload)
    _save_header_media(account, data['name'], data['language'], data.get('header'))
    return result


def edit_template(company: Company, account_id: int, template_id: str, data: dict) -> dict:
    account = get_account(company, account_id)
    result = whatsapp.edit_template(account, template_id, build_components(data), data.get('category'))
    _save_header_media(account, data['name'], data['language'], data.get('header'))
    return result


def delete_template(company: Company, account_id: int, name: str, template_id: str | None = None) -> None:
    account = get_account(company, account_id)
    whatsapp.delete_template(account, name, template_id)


def get_template_detail(company: Company, account_id: int, template_id: str) -> dict:
    account = get_account(company, account_id)
    for template in whatsapp.list_templates(account):
        if template.get('id') == template_id:
            return _map_template_detail(account, template)
    raise NotFound('template not found')


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
    header_media_id: str = '',
    flow_id: int | None = None,
    tag_groups: list[list[int]] | None = None,
    exclude_tag_ids: list[int] | None = None,
    scheduled_at: datetime | None = None,
) -> Campaign:
    from apps.automations.models import Flow
    from apps.chats.tasks import run_campaign

    account = get_account(company, account_id)
    if scheduled_at and timezone.is_naive(scheduled_at):
        scheduled_at = timezone.make_aware(scheduled_at)  # sem fuso = horário de Brasília (TIME_ZONE)
    if scheduled_at and scheduled_at <= timezone.now():
        raise ValidationError('o horário agendado já passou')
    flow = None
    if flow_id is not None:
        flow = Flow.objects.filter(company=company, account=account, pk=flow_id, trigger=Flow.Trigger.CAMPAIGN).first()
        if flow is None:
            raise ValidationError('fluxo inválido: precisa ser da mesma conta e ter o gatilho de clique em campanha')
    body, header_format = get_template_send_info(account, template_name, template_language)
    param_count = max((int(p) for p in TEMPLATE_PARAM_RE.findall(body)), default=0)
    if len(body_params) != param_count:
        raise ValidationError(f'template expects {param_count} params, got {len(body_params)}')
    if header_format in ('IMAGE', 'VIDEO', 'DOCUMENT') and not header_media_id:
        header_media_id = _get_saved_header_media(account, template_name, template_language)
    if header_format in ('IMAGE', 'VIDEO', 'DOCUMENT') and not header_media_id:
        raise ValidationError('esse template exige um arquivo no cabeçalho, envie antes de criar a campanha')

    header_media_type = header_format.lower() if header_media_id else ''
    known = {'nome', 'telefone', *_field_keys(company)}
    if unknown := {v for p in body_params for v in VAR_RE.findall(p)} - known:
        raise ValidationError(f'variável desconhecida: {", ".join("{{" + v + "}}" for v in sorted(unknown))}')

    if contact_ids is None and not numbers:
        recipients = list(campaign_audience(account, tag_groups or []))
    else:
        recipients = list(Contact.objects.filter(account=account, pk__in=contact_ids or []))
    known_keys = _field_keys(company)
    for entry in numbers or []:
        contact = get_or_create_contact(account, normalize_number(entry['number']), entry.get('name', ''))
        _merge_import_fields(contact, entry.get('fields') or {}, known_keys)
        recipients.append(contact)
    if exclude_tag_ids:
        # vale pra qualquer modo (CSV inclusive): quem tem a etiqueta, tipo "bloqueado", nunca recebe
        blocked = set(Contact.tags.through.objects.filter(tag_id__in=exclude_tag_ids).values_list('contact_id', flat=True))
        recipients = [c for c in recipients if c.pk not in blocked]
    if not recipients:
        raise ValidationError('campaign has no recipients')

    campaign = Campaign.objects.create(
        account=account,
        name=name,
        template_name=template_name,
        template_language=template_language,
        body_params=body_params,
        header_media_id=header_media_id,
        header_media_type=header_media_type,
        flow=flow,
        total=len(recipients),
        # ponytail: público congelado na criação; quem ganhar etiqueta de exclusão até o disparo ainda recebe
        recipient_ids=[c.pk for c in recipients],
        scheduled_at=scheduled_at,
        status=Campaign.Status.SCHEDULED if scheduled_at else Campaign.Status.SENDING,
    )
    if not scheduled_at:
        run_campaign.delay(campaign.pk, campaign.recipient_ids)
    return campaign


def cancel_campaign(company: Company, campaign_id: int) -> Campaign:
    campaign = get_campaign(company, campaign_id)
    if not Campaign.objects.filter(pk=campaign.pk, status=Campaign.Status.SCHEDULED).update(status=Campaign.Status.CANCELED):
        raise Conflict('só dá pra cancelar campanha agendada que ainda não começou')
    return get_campaign(company, campaign_id)


def campaign_audience(account: WhatsAppAccount, tag_groups: list[list[int]], exclude_tag_ids: list[int] | None = None) -> QuerySet[Contact]:
    """Contatos da conta que batem com algum grupo (OU), tendo todas as etiquetas do grupo (E).
    Sem grupo = todos. Ex.: [[x, y], [z], [p]] = (x e y) ou z ou p."""
    qs = Contact.objects.filter(account=account)
    groups = [g for g in tag_groups if g]
    if groups:
        match = Q()
        for group in groups:
            has_all = Contact.objects.filter(account=account)
            for tag_id in group:
                has_all = has_all.filter(tags=tag_id)
            match |= Q(pk__in=has_all.values('pk'))
        qs = qs.filter(match)
    if exclude_tag_ids:
        qs = qs.exclude(tags__in=exclude_tag_ids)
    return qs


def preview_campaign_audience(company: Company, account_id: int, tag_groups: list[list[int]], exclude_tag_ids: list[int]) -> dict:
    qs = campaign_audience(get_account(company, account_id), tag_groups, exclude_tag_ids)
    return {'count': qs.count(), 'sample': [c.name or c.wa_id for c in qs.order_by('-id')[:5]]}


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


def find_contact(account: WhatsAppAccount, wa_id: str) -> Contact | None:
    return Contact.objects.filter(account=account, wa_id__in=number_variants(wa_id)).first()


def get_or_create_contact(account: WhatsAppAccount, wa_id: str, name: str = '') -> Contact:
    contact = find_contact(account, wa_id)
    if contact:
        return contact
    try:
        return Contact.objects.create(account=account, wa_id=wa_id, name=name)
    except IntegrityError:
        # corrida entre dois webhooks do mesmo número: o outro criou primeiro
        return Contact.objects.get(account=account, wa_id=wa_id)


def record_incoming_message(
    contact: Contact,
    wa_mesage_id: str,
    content: str,
    message_type: str = 'text',
    media_id: str = '',
    button_id: str = '',
    reply_to_wamid: str = '',
) -> tuple[Message, bool]:
    # resposta a uma mensagem de campanha herda a campanha: o clique no botão sabe de onde veio
    campaign_id = None
    if reply_to_wamid:
        campaign_id = Message.objects.filter(wa_message_id=reply_to_wamid, contact=contact).values_list('campaign_id', flat=True).first()
    message, created = Message.objects.get_or_create(
        wa_message_id=wa_mesage_id,
        defaults={
            'contact': contact,
            'direction': 'in',
            'content': content,
            'message_type': message_type,
            'media_id': media_id,
            'button_id': button_id,
            'campaign_id': campaign_id,
        },
    )
    events.publish(contact.account.company_id, {'type': 'message', 'contact_id': contact.pk})
    return message, created


STATUS_ORDER = ['sent', 'delivered', 'read']


def update_message_status(wa_message_id: str, status: str, error_detail: str = '') -> None:
    # webhooks chegam fora de ordem: só avança (um "delivered" atrasado não rebaixa "read"); failed grava sempre
    qs = Message.objects.filter(wa_message_id=wa_message_id)
    if status == Message.Status.FAILED:
        # a Meta aceitou o envio (contou como enviado) e recusou a entrega depois: a campanha passa a contar como falha
        changed = qs.exclude(status=status).exists()  # reenvio do mesmo failed não conta nem avisa de novo
        for campaign_id in qs.exclude(status=status).exclude(campaign=None).values_list('campaign_id', flat=True):
            Campaign.objects.filter(pk=campaign_id, sent__gt=0).update(sent=F('sent') - 1, failed=F('failed') + 1)
        qs.update(status=status, **({'error_detail': error_detail[:255]} if error_detail else {}))
    elif status in STATUS_ORDER:
        changed = qs.filter(status__in=STATUS_ORDER[: STATUS_ORDER.index(status)]).update(status=status)
    else:
        return
    if changed:
        # status (✓, ✓✓, falha) atualiza na conversa aberta sem recarregar a página
        for company_id, contact_id in qs.values_list('contact__account__company_id', 'contact_id'):
            events.publish(company_id, {'type': 'message_status', 'contact_id': contact_id})


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


# * ------ Ações da IA no contato ------


def contact_ai_activity(company: Company, contact_id: int) -> dict:
    contact = get_contact(company, contact_id)
    return {
        'tool_calls': list(ToolCall.objects.filter(contact=contact).order_by('-id')[:20]),
        'followups': list(FollowUp.objects.filter(contact=contact).order_by('-id')[:5]),
    }


def cancel_followup(company: Company, contact_id: int, followup_id: int) -> FollowUp:
    contact = get_contact(company, contact_id)
    if not FollowUp.objects.filter(contact=contact, pk=followup_id, status=FollowUp.Status.PENDING).update(status=FollowUp.Status.CANCELED):
        raise Conflict('retomada não está agendada')
    return FollowUp.objects.get(pk=followup_id)
