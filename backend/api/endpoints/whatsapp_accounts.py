from ninja import Router, Status

from apps.chats import services
from apps.chats.schemas import WhatsAppAccountInDTO, WhatsAppAccountOutDTO, WhatsAppAccountPatchDTO, WhatsAppRegisterDTO
from apps.companies import services as companies_services

router = Router(tags=['WhatsApp Accounts'])


@router.post('/{company_id}/whatsapp-accounts', response={201: WhatsAppAccountOutDTO})
def create_account_endpoint(request, company_id: int, payload: WhatsAppAccountInDTO):
    company = companies_services.get_owned_company(request.auth, company_id)
    return Status(
        201,
        services.create_account(
            company,
            payload.agent_id,
            payload.phone_number_id,
            payload.waba_id,
            payload.access_token,
            payload.app_secret,
            payload.verify_token,
            payload.app_id,
        ),
    )


@router.get('/{company_id}/whatsapp-accounts', response=list[WhatsAppAccountOutDTO])
def list_accounts_endpoint(request, company_id: int):
    company = companies_services.get_company(request.auth, company_id)
    return services.list_accounts(company)


@router.get('/{company_id}/whatsapp-accounts/{account_id}', response=WhatsAppAccountOutDTO)
def get_account_endpoint(request, company_id: int, account_id: int):
    company = companies_services.get_company(request.auth, company_id)
    return services.get_account(company, account_id)


@router.patch('/{company_id}/whatsapp-accounts/{account_id}', response={200: WhatsAppAccountOutDTO})
def update_account_endpoint(request, company_id: int, account_id: int, payload: WhatsAppAccountPatchDTO):
    company = companies_services.get_owned_company(request.auth, company_id)
    return Status(200, services.update_account(company=company, account_id=account_id, data=payload.dict(exclude_unset=True)))


@router.delete('/{company_id}/whatsapp-accounts/{account_id}', response={204: None})
def delete_account_endpoint(request, company_id: int, account_id: int):
    company = companies_services.get_owned_company(request.auth, company_id)
    services.delete_account(company, account_id)
    return Status(204, None)


@router.post('/{company_id}/whatsapp-accounts/{account_id}/register', response={200: dict})
def register_account_endpoint(request, company_id: int, account_id: int, payload: WhatsAppRegisterDTO):
    company = companies_services.get_owned_company(request.auth, company_id)
    services.register_account(company, account_id, payload.pin)
    return Status(200, {'detail': 'Número registrado no Cloud API'})
