import json

from django.http import HttpResponse
from ninja import Query, Router

from apps.chats import services
from apps.chats.schemas import WebhookVerificationParams
from apps.chats.tasks import process_incoming_message

router = Router(tags=['Webhooks'])


@router.get('/webhook', auth=None)
def verify_webhook_endpoint(request, params: Query[WebhookVerificationParams]):
    if params.mode == 'subscribe' and services.verify_webhook_token(params.token):
        return HttpResponse(params.challenge, content_type='text/plain')
    return HttpResponse(status=403)


MEDIA_PLACEHOLDERS = {'image': '[imagem]', 'video': '[vídeo]', 'sticker': '[figurinha]'}


def parse_message(message: dict) -> tuple[str, str, str, str] | None:
    """(content, message_type, media_id, button_id) de uma mensagem do webhook, ou None se não tratamos o tipo."""
    msg_type = message.get('type')
    if msg_type == 'text':
        return message.get('text', {}).get('body', ''), 'text', '', ''
    if msg_type == 'button':
        # botão de resposta rápida de template: payload (ou o texto) identifica o botão pro fluxo da campanha
        button = message.get('button', {})
        text = button.get('text', '')
        return (text, 'text', '', (button.get('payload') or text)[:64]) if text else None
    if msg_type == 'interactive':
        interactive = message.get('interactive', {})
        reply = interactive.get('button_reply') or interactive.get('list_reply') or {}
        title, reply_id = reply.get('title', ''), reply.get('id', '')[:64]
        return (title, 'text', '', reply_id) if title or reply_id else None
    if msg_type == 'location':
        loc = message.get('location', {})
        label = ', '.join(p for p in (loc.get('name', ''), loc.get('address', '')) if p)
        coords = f'{loc.get("latitude")}, {loc.get("longitude")}'
        return f'[localização: {label or coords}]', 'text', '', ''
    if msg_type == 'contacts':
        names = [c.get('name', {}).get('formatted_name', '') for c in message.get('contacts', [])]
        return f'[contato: {", ".join(n for n in names if n) or "Sem nome"}]', 'text', '', ''
    if msg_type in ('image', 'audio', 'video', 'document', 'sticker'):
        media = message.get(msg_type, {})
        if not media.get('id'):
            return None
        placeholder = f'[documento: {media.get("filename", "")}]' if msg_type == 'document' else MEDIA_PLACEHOLDERS.get(msg_type, '')
        return media.get('caption', '') or placeholder, msg_type, media['id'], ''
    return None


@router.post('/webhook', auth=None)
def receive_webhook_endpoint(request):
    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return HttpResponse(status=400)

    # uma entrega pode trazer vários entry/changes, até de números diferentes
    batches = []
    for entry in payload.get('entry', []) if isinstance(payload, dict) else []:
        for change in entry.get('changes', []):
            value = change.get('value', {})
            account = services.get_account_by_phone_number_id(value.get('metadata', {}).get('phone_number_id', ''))
            if account is not None:
                batches.append((account, value))

    # assinatura antes de processar qualquer coisa: HMAC é sobre o body inteiro
    signature = request.headers.get('X-Hub-Signature-256', '')
    if not all(services.verify_webhook_signature(a, request.body, signature) for a in {a for a, _ in batches}):
        return HttpResponse(status=403)

    for account, value in batches:
        contacts = value.get('contacts', [])
        name = contacts[0].get('profile', {}).get('name', '') if contacts else ''

        for message in value.get('messages', []):
            parsed = parse_message(message)
            if parsed is None:
                continue
            content, msg_type, media_id, button_id = parsed
            contact = services.get_or_create_contact(account, message['from'], name)
            # context.id = wamid da mensagem respondida (o template da campanha, no clique de botão)
            reply_to = message.get('context', {}).get('id', '') if message.get('type') == 'button' else ''
            record, created = services.record_incoming_message(contact, message['id'], content, msg_type, media_id, button_id, reply_to)
            if created:
                process_incoming_message.apply_async(args=(record.pk,), countdown=account.agent.response_delay)

        for status_event in value.get('statuses', []):
            errors = status_event.get('errors', [])
            error_detail = errors[0].get('title', '') if errors else ''
            services.update_message_status(status_event['id'], status_event['status'], error_detail)

    return HttpResponse(status=200)
