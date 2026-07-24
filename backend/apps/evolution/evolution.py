import base64
import secrets

import httpx
from django.conf import settings

from core.exceptions import EvolutionError, NotFound

EVOLUTION_URL = settings.EVOLUTION_API_URL
EVOLUTION_GLOBAL_KEY = settings.EVOLUTION_API_KEY
TIMEOUT = 10


def _req(api_key: str, method: str, path: str, *, params: dict | None = None, json: dict | None = None) -> dict:
    url = f'{EVOLUTION_URL}{path}'
    headers = {'apikey': api_key}
    try:
        response = httpx.request(method=method, url=url, headers=headers, params=params, json=json, timeout=TIMEOUT)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        if code == 404:
            raise NotFound('instance not found')
        raise EvolutionError(f'Evolution API returned {code}')
    except httpx.RequestError:
        raise EvolutionError('could not reach evolution api')
    return response.json() if response.content else {}


# * ----- Instances ------


def create_instance(name: str):
    token = secrets.token_hex(16)
    instance = _req(api_key=EVOLUTION_GLOBAL_KEY, method='POST', path='/instance/create', json={'name': name, 'token': token})
    return instance['data']['token']


def get_instance_phone(name: str) -> str:
    instances = _req(api_key=EVOLUTION_GLOBAL_KEY, method='GET', path='/instance/all').get('data', [])
    match = next((i for i in instances if i.get('name') == name), None)
    jid = match.get('jid', '') if match else ''
    return jid.split('@')[0].split(':')[0] if jid else ''


def connect_instance(instance_api_key: str, webhook_url: str, subscribe: list):
    connection = _req(api_key=instance_api_key, method='POST', path='/instance/connect', json={'webhookUrl': webhook_url, 'subscribe': subscribe})
    return connection['message']


def get_qr_code(instance_api_key: str):
    qr = _req(api_key=instance_api_key, method='GET', path='/instance/qr')
    return qr['data']['qrcode']


def get_pair_code(instance_api_key: str, phone: str):
    pair = _req(api_key=instance_api_key, method='POST', path='/instance/pair', json={'phone': phone})
    return pair['data']['PairingCode']


def reconnect_instance(instance_api_key: str):
    _req(api_key=instance_api_key, method='POST', path='/instance/reconnect')


def disconnect_instance(instance_api_key: str):
    _req(api_key=instance_api_key, method='POST', path='/instance/disconnect')


def get_connection_status(instance_api_key: str):
    status = _req(api_key=instance_api_key, method='GET', path='/instance/status')
    return status['data']


def logout_instance(instance_api_key: str):
    logout = _req(api_key=instance_api_key, method='DELETE', path='/instance/logout')
    return logout['message']


def delete_instance(name: str):
    instances = _req(api_key=EVOLUTION_GLOBAL_KEY, method='GET', path='/instance/all').get('data', [])
    match = next((i for i in instances if i.get('name') == name), None)
    if match:
        _req(api_key=EVOLUTION_GLOBAL_KEY, method='DELETE', path=f'/instance/delete/{match["id"]}')


# * ----- Send ------


def send_text(instance_api_key: str, number: str, text: str):
    _req(api_key=instance_api_key, method='POST', path='/send/text', json={'number': number, 'text': text})


def send_sticker(instance_api_key: str, number: str, sticker_url: str):
    _req(api_key=instance_api_key, method='POST', path='/send/sticker', json={'number': number, 'sticker': sticker_url})


def send_media_url(instance_api_key: str, number: str, url: str, message_type: str, caption: str | None = None):
    _req(api_key=instance_api_key, method='POST', path='/send/media', json={'number': number, 'url': url, 'type': message_type, 'caption': caption})


def send_media_file(
    instance_api_key: str, number: str, message_type: str, content: bytes, filename: str, content_type: str, caption: str | None = None
):
    url = f'{EVOLUTION_URL}/send/media'
    data = {'number': number, 'type': message_type}
    if caption:
        data['caption'] = caption
    if filename:
        data['filename'] = filename
    files = {'file': (filename, content, content_type)}
    try:
        response = httpx.post(url, headers={'apikey': instance_api_key}, data=data, files=files, timeout=TIMEOUT)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise EvolutionError(f'evolution api returned {exc.response.status_code}')
    except httpx.RequestError:
        raise EvolutionError('could not reach evolution api')


def download_media(instance_api_key: str, message: dict) -> tuple[bytes, str]:
    res = _req(api_key=instance_api_key, method='POST', path='/message/downloadmedia', json={'message': message})
    data_url = res['data']['base64']
    header, b64 = data_url.split(',', 1)
    mime = header.removeprefix('data:').split(';')[0]
    return base64.b64decode(b64), mime


# * ----- User ------


def get_user_avatar(instance_api_key: str, number: str):
    avatar = _req(api_key=instance_api_key, method='POST', path='/user/avatar', json={'number': number})
    return avatar['avatar']
