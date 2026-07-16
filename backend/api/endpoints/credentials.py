from ninja import Router, Status

from apps.agents import services
from apps.agents.schemas import CredentialInDTO, CredentialOutDTO, CredentialPatchDTO
from apps.companies import services as companies_services

router = Router(tags=['Credentials'])


@router.post('/{company_id}/credentials', response={201: CredentialOutDTO})
def create_credential_endpoint(request, company_id: int, payload: CredentialInDTO):
    company = companies_services.get_owned_company(request.auth, company_id)
    return Status(201, services.create_credential(company, payload.provider, payload.api_key))


@router.get('/{company_id}/credentials', response=list[CredentialOutDTO])
def list_credentials_endpoint(request, company_id: int):
    company = companies_services.get_company(request.auth, company_id)
    return services.list_credentials(company)


@router.get('/{company_id}/credentials/{credential_id}', response=CredentialOutDTO)
def get_credential_endpoint(request, company_id: int, credential_id: int):
    company = companies_services.get_company(request.auth, company_id)
    return services.get_credential(company, credential_id)


@router.patch('/{company_id}/credentials/{credential_id}', response={200: CredentialOutDTO})
def update_credential_endpoint(request, company_id: int, credential_id: int, payload: CredentialPatchDTO):
    company = companies_services.get_owned_company(request.auth, company_id)
    return Status(200, services.update_credential(company, credential_id, payload.api_key))


@router.delete('/{company_id}/credentials/{credential_id}', response={204: None})
def delete_credential_endpoint(request, company_id: int, credential_id: int):
    company = companies_services.get_owned_company(request.auth, company_id)
    services.delete_credential(company, credential_id)
    return Status(204, None)
