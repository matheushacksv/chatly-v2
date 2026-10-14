import httpx

from apps.chats.models import WhatsAppAccount
from core.exceptions import ValidationError

GRAPH_URL = 'https://graph.facebook.com/v25.0'


def graph_error_message(response: httpx.Response) -> str:
    """Mensagem real da Meta numa resposta de erro"""
    try:
        error = response.json()['error']
        return error.get('error_user_msg') or error.get('message') or 'erro no WhatsApp'
    except Exception:
        return 'erro no WhatsApp'


def _graph_error(response: httpx.Response) -> None:
    """Levanta ValidationError com a mensagem real da Meta"""
    if not response.is_success:
        raise ValidationError(graph_error_message(response))


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
        params={'fields': 'id,name,status,language,category,components', 'limit': 100},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()['data']


def send_template(
    account: WhatsAppAccount, wa_id: str, name: str, language: str, body_params: list[str], header_media_id: str = '', header_media_type: str = ''
) -> str:
    template: dict = {'name': name, 'language': {'code': language}}
    components = []
    if header_media_id and header_media_type:
        components.append({'type': 'header', 'parameters': [{'type': header_media_type, header_media_type: {'id': header_media_id}}]})
    if body_params:
        components.append({'type': 'body', 'parameters': [{'type': 'text', 'text': p} for p in body_params]})
    if components:
        template['components'] = components

    response = httpx.post(
        f'{GRAPH_URL}/{account.phone_number_id}/messages',
        headers={'Authorization': f'Bearer {account.access_token}'},
        json={'messaging_product': 'whatsapp', 'to': wa_id, 'type': 'template', 'template': template},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()['messages'][0]['id']


def create_template(account: WhatsAppAccount, payload: dict) -> dict:
    response = httpx.post(
        f'{GRAPH_URL}/{account.waba_id}/message_templates',
        headers={'Authorization': f'Bearer {account.access_token}'},
        json=payload,
        timeout=10,
    )
    _graph_error(response)
    return response.json()


def edit_template(account: WhatsAppAccount, template_id: str, components: list, category: str | None = None) -> dict:
    payload: dict = {'components': components}
    if category:
        payload['category'] = category
    response = httpx.post(
        f'{GRAPH_URL}/{template_id}',
        headers={'Authorization': f'Bearer {account.access_token}'},
        json=payload,
        timeout=10,
    )
    _graph_error(response)
    return response.json()


def delete_template(account: WhatsAppAccount, name: str, template_id: str | None = None) -> None:
    params: dict = {'name': name}
    if template_id:
        params['hsm_id'] = template_id
    response = httpx.delete(
        f'{GRAPH_URL}/{account.waba_id}/message_templates',
        headers={'Authorization': f'Bearer {account.access_token}'},
        params=params,
    )
    _graph_error(response)


def upload_sample_media(account: WhatsAppAccount, content: bytes, mime: str, filename: str) -> str:
    if not account.app_id:
        raise ValidationError('conta sem app_id configurado. necessário para cabeçalho de mídia')

    session_response = httpx.post(
        f'{GRAPH_URL}/{account.app_id}/uploads',
        headers={'Authorization': f'Bearer {account.access_token}'},
        params={'file_length': len(content), 'file_type': mime, 'file_name': filename},
        timeout=10,
    )
    _graph_error(session_response)
    session_id = session_response.json()['id']

    upload_response = httpx.post(
        f'{GRAPH_URL}/{session_id}',
        headers={'Authorization': f'OAuth {account.access_token}', 'file_offset': '0'},
        content=content,
        timeout=30,
    )
    _graph_error(upload_response)
    return upload_response.json()['h']


def send_interactive(account: WhatsAppAccount, wa_id: str, text: str, buttons: list[dict]) -> str:
    response = httpx.post(
        f'{GRAPH_URL}/{account.phone_number_id}/messages',
        headers={'Authorization': f'Bearer {account.access_token}'},
        json={
            'messaging_product': 'whatsapp',
            'to': wa_id,
            'type': 'interactive',
            'interactive': {
                'type': 'button',
                'body': {'text': text},
                'action': {'buttons': [{'type': 'reply', 'reply': {'id': b['id'], 'title': b['title'][:20]}} for b in buttons[:3]]},
            },
        },
        timeout=10,
    )
    response.raise_for_status()
    return response.json()['messages'][0]['id']


def register_number(account: WhatsAppAccount, pin: str) -> None:
    response = httpx.post(
        f'{GRAPH_URL}/{account.phone_number_id}/register',
        headers={'Authorization': f'Bearer {account.access_token}'},
        json={'messaging_product': 'whatsapp', 'pin': pin},
        timeout=60,  # o registro na Meta leva ~10s; com 10 o painel desistia antes da resposta
    )
    _graph_error(response)
