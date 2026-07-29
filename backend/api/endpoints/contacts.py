from ninja import Router, Status
from ninja.pagination import paginate

from apps.chats import services
from apps.chats.schemas import ContactCreateDTO, ContactImportDTO, ContactImportResultDTO, ContactOutDTO, ContactPatchDTO
from apps.companies import services as companies_services

router = Router(tags=['Contacts'])


@router.get('/{company_id}/whatsapp-accounts/{account_id}/contacts', response=list[ContactOutDTO])
@paginate
def list_contacts_endpoint(request, company_id: int, account_id: int, search: str = ''):
    company = companies_services.get_company(request.auth, company_id)
    return services.list_contacts(company, account_id, search)


@router.post('/{company_id}/whatsapp-accounts/{account_id}/contacts', response={201: ContactOutDTO})
def create_contact_endpoint(request, company_id: int, account_id: int, payload: ContactCreateDTO):
    company = companies_services.get_company(request.auth, company_id)
    return Status(201, services.create_contact(company, account_id, payload.number, payload.name))


@router.delete('/{company_id}/contacts/{contact_id}/memory', response={204: None})
def clear_contact_memory_endpoint(request, company_id: int, contact_id: int):
    company = companies_services.get_company(request.auth, company_id)
    services.clear_contact_memory(company, contact_id)
    return Status(204, None)


@router.patch('/{company_id}/contacts/{contact_id}', response=ContactOutDTO)
def update_contact_endpoint(request, company_id: int, contact_id: int, payload: ContactPatchDTO):
    company = companies_services.get_company(request.auth, company_id)
    return services.update_contact(company, contact_id, payload.dict(exclude_unset=True))


@router.post('/{company_id}/whatsapp-accounts/{account_id}/contacts/import', response=ContactImportResultDTO)
def import_contacts_endpoint(request, company_id: int, account_id: int, payload: ContactImportDTO):
    company = companies_services.get_company(request.auth, company_id)
    return services.import_contacts(company, account_id, [n.dict() for n in payload.numbers])


@router.delete('/{company_id}/contacts/{contact_id}', response={204: None})
def delete_contact_endpoint(request, company_id: int, contact_id: int):
    company = companies_services.get_owned_company(request.auth, company_id)
    services.delete_contact(company, contact_id)
    return Status(204, None)
