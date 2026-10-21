"""Planos e barreiras de pagamento. Com BILLING_ENABLED desligado (servidor do cliente) tudo passa."""

from django.conf import settings
from django.utils import timezone

from core.exceptions import PaymentRequired

# None = sem limite; "members" conta o dono
PLANS = {
    'trial': {'label': 'Teste grátis', 'wa_accounts': 2, 'agents': None, 'members': None, 'features': {'automations', 'campaigns'}},
    'starter': {'label': 'Starter', 'wa_accounts': 1, 'agents': 1, 'members': 1, 'features': set()},
    'pro': {'label': 'Pro', 'wa_accounts': 2, 'agents': None, 'members': None, 'features': {'automations', 'campaigns'}},
    'business': {'label': 'Business', 'wa_accounts': None, 'agents': None, 'members': None, 'features': {'automations', 'campaigns'}},
}
FEATURE_LABELS = {'automations': 'Automações', 'campaigns': 'Campanhas'}
LIMIT_LABELS = {'wa_accounts': 'números de WhatsApp', 'agents': 'IAs', 'members': 'membros'}


def is_active(company) -> bool:
    """Plano em dia: sem data de fim (cortesia/manual) ou com a data ainda no futuro."""
    return not settings.BILLING_ENABLED or company.plan_until is None or company.plan_until > timezone.now()


def allows(company, feature: str) -> bool:
    return not settings.BILLING_ENABLED or (is_active(company) and feature in PLANS[company.plan]['features'])


def _require_active(company) -> None:
    if not is_active(company):
        raise PaymentRequired('Seu plano expirou. Assine para continuar usando.')


def require_feature(company, feature: str) -> None:
    _require_active(company)
    if not allows(company, feature):
        raise PaymentRequired(f'{FEATURE_LABELS[feature]} não fazem parte do plano {PLANS[company.plan]["label"]}.')


def require_room(company, limit: str, used: int) -> None:
    """Barra a criação de mais um recurso (`used` = quantos já existem) acima do limite do plano."""
    if not settings.BILLING_ENABLED:
        return
    _require_active(company)
    cap = PLANS[company.plan][limit]
    if cap is not None and used >= cap:
        raise PaymentRequired(f'O plano {PLANS[company.plan]["label"]} permite até {cap} {LIMIT_LABELS[limit]}.')
