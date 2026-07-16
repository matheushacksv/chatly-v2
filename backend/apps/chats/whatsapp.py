import httpx

from apps.chats.models import WhatsAppAccount

GRAPH_URL = 'https://graph.facebook.com/v25.0'


def send_text(account: WhatsAppAccount, wa_id: str, text: str) -> str:
    response = httpx.post(
        f'{GRAPH_URL}/{account.phone_number_id}/messages',
        headers={'Authorization': f'Bearer {account.access_token}'},
        json={
            'messaging_product': 'whatsapp',
            'to': wa_id,
            'type': 'text',
            'text': {'body': text},
        },
        timeout=10,
    )
    response.raise_for_status()
    return response.json()['messages'][0]['id']


def download_media(account: WhatsAppAccount, media_id: str) -> tuple[bytes, str]:
    headers = {'Authorization': f'Bearer {account.access_token}'}
    meta = httpx.get(f'{GRAPH_URL}/{media_id}', headers=headers, timeout=10)
    meta.raise_for_status()
    data = meta.json()
    media = httpx.get(data['url'], headers=headers, timeout=10)
    media.raise_for_status()
    return media.content, data['mime_type']


def upload_media(account: WhatsAppAccount, content: bytes, mime_type: str, filename: str) -> str:
    response = httpx.post(
        f'{GRAPH_URL}/{account.phone_number_id}/media',
        headers={'Authorization': f'Bearer {account.access_token}'},
        data={'messaging_product': 'whatsapp'},
        files={'file': (filename, content, mime_type)},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()['id']


def send_media(account: WhatsAppAccount, wa_id: str, media_type: str, media_id: str, caption: str = '', filename: str = '') -> str:
    media_payload: dict = {'id': media_id}
    if caption and media_type not in ('audio', 'sticker'):
        media_payload['caption'] = caption
    if filename and media_type == 'document':
        media_payload['filename'] = filename

    response = httpx.post(
        f'{GRAPH_URL}/{account.phone_number_id}/messages',
        headers={'Authorization': f'Bearer {account.access_token}'},
        json={
            'messaging_product': 'whatsapp',
            'to': wa_id,
            'type': media_type,
            media_type: media_payload,
        },
        timeout=10,
    )
    response.raise_for_status()
    return response.json()['messages'][0]['id']


def list_templates(account: WhatsAppAccount) -> list[dict]:
    response = httpx.get(
        f'{GRAPH_URL}/{account.waba_id}/message_templates',
        headers={'Authorization': f'Bearer {account.access_token}'},
        params={'fields': 'name,status,language,category,components', 'limit': 100},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()['data']


def send_template(account: WhatsAppAccount, wa_id: str, name: str, language: str, body_params: list[str]) -> str:
    template: dict = {'name': name, 'language': {'code': language}}
    if body_params:
        template['components'] = [{'type': 'body', 'parameters': [{'type': 'text', 'text': p} for p in body_params]}]

    response = httpx.post(
        f'{GRAPH_URL}/{account.phone_number_id}/messages',
        headers={'Authorization': f'Bearer {account.access_token}'},
        json={'messaging_product': 'whatsapp', 'to': wa_id, 'type': 'template', 'template': template},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()['messages'][0]['id']
