from typing import Literal

from ninja import Router, Status
from ninja.pagination import paginate

from apps.chats import services
from apps.chats.schemas import ContactCreateDTO, ContactImportDTO, ContactImportResultDTO, ContactOutDTO, ContactPatchDTO, TagInDTO, TagOutDTO
from apps.companies import services as companies_services

router = Router(tags=['Contacts'])


@router.get('/{company_id}/whatsapp-accounts/{account_id}/contacts', response=list[ContactOutDTO])
@paginate
def list_contacts_endpoint(
    request, company_id: int, account_id: int, search: str = '', queue: Literal['', 'waiting', 'mine'] = '', tag: int | None = None
):
    company = companies_services.get_company(request.auth, company_id)
    return services.list_contacts(company, account_id, search, queue, request.auth, tag)


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


@router.get('/{company_id}/tags', response=list[TagOutDTO])
def list_tags_endpoint(request, company_id: int):
    company = companies_services.get_company(request.auth, company_id)
    return services.list_tags(company)


@router.post('/{company_id}/tags', response={201: TagOutDTO})
def create_tag_endpoint(request, company_id: int, payload: TagInDTO):
    company = companies_services.get_company(request.auth, company_id)
    return Status(201, services.create_tag(company, payload.name, payload.color))


@router.delete('/{company_id}/tags/{tag_id}', response={204: None})
def delete_tag_endpoint(request, company_id: int, tag_id: int):
    company = companies_services.get_owned_company(request.auth, company_id)
    services.delete_tag(company, tag_id)
    return Status(204, None)


@router.post('/{company_id}/contacts/{contact_id}/tags/{tag_id}', response=ContactOutDTO)
def add_contact_tag_endpoint(request, company_id: int, contact_id: int, tag_id: int):
    company = companies_services.get_company(request.auth, company_id)
    return services.add_contact_tag(company, contact_id, tag_id)


@router.delete('/{company_id}/contacts/{contact_id}/tags/{tag_id}', response=ContactOutDTO)
def remove_contact_tag_endpoint(request, company_id: int, contact_id: int, tag_id: int):
    company = companies_services.get_company(request.auth, company_id)
    return services.remove_contact_tag(company, contact_id, tag_id)
