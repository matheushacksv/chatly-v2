from ninja_extra import NinjaExtraAPI
from ninja_jwt.authentication import JWTAuth

from api.endpoints.accounts import router as accounts_router
from api.endpoints.agents import router as agents_router
from api.endpoints.automations import router as automations_router
from api.endpoints.campaign import router as campaigns_router
from api.endpoints.companies import router as companies_router
from api.endpoints.contacts import router as contacts_router
from api.endpoints.credentials import router as credentials_router
from api.endpoints.crm import router as crm_router
from api.endpoints.documents import router as documents_router
from api.endpoints.events import router as events_router
from api.endpoints.evolution import router as evolution_router
from api.endpoints.messages import router as messages_router
from api.endpoints.stickers import router as stickers_router
from api.endpoints.templates import router as templates_router
from api.endpoints.webhook import router as webhooks_router
from api.endpoints.whatsapp_accounts import router as whatsapp_accounts_router
from core.exceptions import DomainError

api = NinjaExtraAPI(title='Chatly', version='1.0.0', auth=JWTAuth())


@api.exception_handler(DomainError)
def handle_domain_error(request, exc: DomainError):
    return api.create_response(request, {'detail': exc.message}, status=exc.status_code)


api.add_router('/auth', accounts_router)
api.add_router('/companies', companies_router)
api.add_router('/companies', credentials_router)
api.add_router('/companies', agents_router)
api.add_router('/companies', whatsapp_accounts_router)
api.add_router('/companies', contacts_router)
api.add_router('/companies', messages_router)
api.add_router('/whatsapp', webhooks_router)
api.add_router('/companies', documents_router)
api.add_router('/companies', events_router)
api.add_router('/companies', stickers_router)
api.add_router('/companies', campaigns_router)
api.add_router('/companies', crm_router)
api.add_router('/companies', templates_router)
api.add_router('/evolution', evolution_router)
api.add_router('/automations', automations_router)
