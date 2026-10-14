"""Boundary da requisição HTTP dos fluxos: a URL vem do usuário, então só sai pra internet pública."""

import ipaddress
import socket
from urllib.parse import urlsplit

import httpx

from core.exceptions import ValidationError

METHODS = ('GET', 'POST', 'PUT', 'PATCH', 'DELETE')
TIMEOUT = 10


def check_url(url: str) -> None:
    """Recusa o que não for https pra IP público: nada de localhost, rede do docker (minio, openwa, redis) ou rede privada."""
    parts = urlsplit(url)
    if parts.scheme != 'https' or not parts.hostname:
        raise ValidationError('a URL precisa começar com https://')
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise ValidationError(f'não foi possível resolver {parts.hostname}')
    for info in infos:
        if not ipaddress.ip_address(info[4][0]).is_global:
            raise ValidationError('a URL aponta pra um endereço interno')


def request(method: str, url: str, headers: dict[str, str], body: str) -> int:
    """Faz a chamada e devolve o status HTTP. A resposta não é guardada."""
    return fetch(method, url, headers, body)[0]


def fetch(method: str, url: str, headers: dict[str, str], body: str, max_chars: int = 4000) -> tuple[int, str]:
    """Faz a chamada e devolve (status, corpo cortado). Usado pelas ferramentas da IA, que precisam ler a resposta."""
    if method not in METHODS:
        raise ValidationError(f'método inválido: {method}')
    # ponytail: entre a checagem e a conexão o DNS pode mudar (rebinding); se virar risco, fixar o IP resolvido no transporte
    check_url(url)
    response = httpx.request(
        method,
        url,
        headers=headers,
        content=body.encode() if body and method != 'GET' else None,
        timeout=TIMEOUT,
        follow_redirects=False,
    )
    return response.status_code, response.text[:max_chars]
