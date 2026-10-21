class DomainError(Exception):
    """Base for bussiness-rule errors"""

    status_code = 400
    default_message = 'invalid request'

    def __init__(self, message: str | None = None):
        self.message = message or self.default_message
        super().__init__(self.message)


class NotFound(DomainError):
    status_code = 404
    default_message = 'Resource not found'


class ValidationError(DomainError):
    status_code = 400
    default_message = 'Validation failed'


class Conflict(DomainError):
    status_code = 409
    default_message = 'Conflict'


class Unauthorized(DomainError):
    status_code = 401
    default_message = 'Unauthorized'


class Forbidden(DomainError):
    status_code = 403
    default_message = 'Forbidden'


class PipedriveError(DomainError):
    status_code = 502
    default_message = 'Pipedrive request failed'

class GatewayError(DomainError):
    """Falha ao falar com o gateway do WhatsApp pessoal (OpenWA)."""

    status_code = 502
    default_message = 'WhatsApp gateway request failed'

class PaymentRequired(DomainError):
    """Plano expirado ou recurso fora do plano da empresa."""

    status_code = 402
    default_message = 'Payment required'
