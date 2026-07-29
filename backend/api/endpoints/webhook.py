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


@router.post('/webhook', auth=None)
def receive_webhook_endpoint(request):
    payload = json.loads(request.body)
    try:
        value = payload['entry'][0]['changes'][0]['value']
        phone_number_id = value['metadata']['phone_number_id']
    except (KeyError, IndexError):
        return HttpResponse(status=200)

    account = services.get_account_by_phone_number_id(phone_number_id)
    if account is None:
        return HttpResponse(status=200)

    signature = request.headers.get('X-Hub-Signature-256', '')
    if not services.verify_webhook_signature(account, request.body, signature):
        return HttpResponse(status=403)

    contacts = value.get('contacts', [])
    name = contacts[0].get('profile', {}).get('name', '') if contacts else ''

    for message in value.get('messages', []):
        msg_type = message.get('type')
        if msg_type not in ('text', 'image', 'audio', 'video', 'document', 'sticker', 'interactive'):
            continue
        contact = services.get_or_create_contact(account, message['from'], name)
        button_id = ''
        if msg_type == 'text':
            content, media_id = message['text']['body'], ''
        elif msg_type == 'interactive':
            reply = message.get('interactive', {}).get('button_reply', {})
            content, media_id, button_id = reply.get('title', ''), '', reply.get('id', '')
            msg_type = 'text'
            if not content and not button_id:
                continue
        else:
            media = message.get(msg_type, {})
            media_id = media.get('id', '')
            if not media_id:
                continue
            placeholders = {
                'image': '[imagem]',
                'video': '[vídeo]',
                'sticker': '[figurinha]',
                'document': f'[documento: {media.get("filename", "")}]',
            }
            content = media.get('caption', '') or placeholders.get(msg_type, '')
        record, created = services.record_incoming_message(contact, message['id'], content, msg_type, media_id, button_id)
        if created:
            process_incoming_message.apply_async(args=(record.pk,), countdown=account.agent.response_delay)

    for status_event in value.get('statuses', []):
        errors = status_event.get('errors', [])
        error_detail = errors[0].get('title', '') if errors else ''
        services.update_message_status(status_event['id'], status_event['status'], error_detail)

    return HttpResponse(status=200)
