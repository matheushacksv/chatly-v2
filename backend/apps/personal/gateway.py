"""Boundary do WhatsApp pessoal: única camada que fala HTTP com o OpenWA.

Chave de API global no header, sessão na URL. O gateway escolhe o id da mensagem e só devolve
na resposta do envio (quem casa isso com o eco do webhook é o services).
"""

import base64
import logging

import httpx
from django.conf import settings

from core.exceptions import GatewayError, NotFound

logger = logging.getLogger(__name__)

TIMEOUT = 10
MEDIA_TIMEOUT = 120

MEDIA_ROUTES = {
    'image': 'send-image',
    'video': 'send-video',
    'audio': 'send-audio',
    'document': 'send-document',
    'sticker': 'send-sticker',
}


def chat_id(number: str) -> str:
    return number if '@' in number else f'{number}{settings.OPENWA_JID_SUFFIX}'


def number_from_chat_id(value: str) -> str:
    return (value or '').split('@')[0].split(':')[0]


def _response(method: str, path: str, *, json: dict | None = None, params: dict | None = None, timeout: int = TIMEOUT) -> httpx.Response:
    try:
        response = httpx.request(
            method,
            f'{settings.OPENWA_API_URL}/api{path}',
            headers={'X-API-Key': settings.OPENWA_API_KEY},
            json=json,
            params=params,
            timeout=timeout,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise NotFound('sessão não encontrada no gateway')
        raise GatewayError(f'OpenWA respondeu {exc.response.status_code}: {exc.response.text[:200]}')
    except httpx.RequestError:
        raise GatewayError('não deu pra falar com o OpenWA')
    return response


def _req(method: str, path: str, **kwargs) -> dict:
    response = _response(method, path, **kwargs)
    if not response.content:
        return {}
    body = response.json()
    return body if isinstance(body, dict) else {'data': body}


def _rows(body: dict) -> list:
    data = body.get('data', body)
    return data if isinstance(data, list) else []


# * ----- Sessões -----


def create_session(name: str) -> str:
    return str(_req('POST', '/sessions', json={'name': name})['id'])


def find_session_id(name: str) -> str:
    row = next((r for r in _rows(_req('GET', '/sessions')) if r.get('name') == name), None)
    return str(row['id']) if row else ''


def start_session(session_id: str) -> None:
    _req('POST', f'/sessions/{session_id}/start')


def session_state(session_id: str) -> dict:
    return _req('GET', f'/sessions/{session_id}')


def get_qr_code(session_id: str) -> str:
    data = _req('GET', f'/sessions/{session_id}/qr')
    return data.get('qrCode') or data.get('qr') or ''


def get_pair_code(session_id: str, phone: str) -> str:
    return _req('POST', f'/sessions/{session_id}/pairing-code', json={'phoneNumber': phone}).get('pairingCode', '')


def logout_session(session_id: str) -> None:
    _req('POST', f'/sessions/{session_id}/logout')


def delete_session(session_id: str) -> None:
    _req('DELETE', f'/sessions/{session_id}')


def register_webhook(session_id: str, url: str, events: list[str], secret: str) -> None:
    # idempotente pela URL: reconectar não empilha webhooks repetidos
    if any(row.get('url') == url for row in _rows(_req('GET', f'/sessions/{session_id}/webhooks'))):
        return
    _req('POST', f'/sessions/{session_id}/webhooks', json={'url': url, 'events': events, 'secret': secret})


# * ----- Envio -----


def send_text(session_id: str, number: str, text: str) -> str:
    return _req('POST', f'/sessions/{session_id}/messages/send-text', json={'chatId': chat_id(number), 'text': text}).get('messageId', '')


def _voice_note(session_id: str, raw: bytes) -> tuple[str, str] | None:
    """Converte pra OGG/Opus no próprio OpenWA (ele já tem ffmpeg); None se não der."""
    try:
        data = _req(
            'POST', f'/sessions/{session_id}/media/convert/voice', json={'base64': base64.b64encode(raw).decode()}, timeout=MEDIA_TIMEOUT
        )
    except GatewayError:
        return None
    return (data['base64'], data.get('mimetype') or 'audio/ogg; codecs=opus') if data.get('base64') else None


def send_media(session_id: str, number: str, raw: bytes, media_type: str, mime: str, filename: str = '', caption: str = '') -> str:
    payload = {'chatId': chat_id(number), 'base64': base64.b64encode(raw).decode(), 'mimetype': mime}
    if filename:
        payload['filename'] = filename
    if caption and media_type not in ('audio', 'sticker'):
        payload['caption'] = caption
    if media_type == 'audio':
        # só OGG/Opus vira nota de voz tocável; o resto chega como arquivo que não toca
        voice = (payload['base64'], 'audio/ogg; codecs=opus') if mime.startswith('audio/ogg') else _voice_note(session_id, raw)
        if voice is None:
            logger.warning('áudio %s sem conversão pra opus: vai como arquivo, não como nota de voz', mime)
        else:
            payload['base64'], payload['mimetype'] = voice
            payload['ptt'] = True
    route = MEDIA_ROUTES.get(media_type, 'send-document')
    return _req('POST', f'/sessions/{session_id}/messages/{route}', json=payload, timeout=MEDIA_TIMEOUT).get('messageId', '')


def download_media(session_id: str, number: str, message_id: str) -> tuple[bytes, str]:
    response = _response('GET', f'/sessions/{session_id}/messages/{chat_id(number)}/{message_id}/media', timeout=MEDIA_TIMEOUT)
    return response.content, response.headers.get('content-type', '').split(';')[0]


# * ----- Contatos -----


def check_number(session_id: str, number: str) -> str:
    data = _req('GET', f'/sessions/{session_id}/contacts/check/{number}')
    return number_from_chat_id(data.get('whatsappId') or '') if data.get('exists') else ''


def list_contacts(session_id: str) -> dict[str, str]:
    """Agenda do celular: {wa_id: nome}. Só entra quem tem nome (da agenda ou o que a pessoa se deu)."""
    out = {}
    for row in _rows(_req('GET', f'/sessions/{session_id}/contacts', params={'limit': 5000})):
        number = row.get('number') or ''
        name = row.get('name') or row.get('pushName') or ''
        if number.isdigit() and name:
            out[number] = name
    return out


def get_profile_picture(session_id: str, number: str) -> str:
    return _req('GET', f'/sessions/{session_id}/contacts/{chat_id(number)}/profile-picture').get('url') or ''
