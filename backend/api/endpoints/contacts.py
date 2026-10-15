from typing import Literal

from ninja import Router, Status
from ninja.pagination import paginate

from apps.chats import services
from apps.chats.schemas import QuickReplyInDTO, QuickReplyOutDTO, ContactAIActivityDTO, FollowUpOutDTO, ContactCreateDTO, ContactImportDTO, ContactImportResultDTO, ContactOutDTO, ContactFieldInDTO, ContactFieldOutDTO, ContactPatchDTO, TagInDTO, TagOutDTO
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
    return services.import_contacts(company, account_id, [n.dict() for n in payload.numbers], payload.tag_ids)


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


@router.get('/{company_id}/contact-fields', response=list[ContactFieldOutDTO])
def list_contact_fields_endpoint(request, company_id: int):
    company = companies_services.get_company(request.auth, company_id)
    return services.list_contact_fields(company)


@router.post('/{company_id}/contact-fields', response={201: ContactFieldOutDTO})
def create_contact_field_endpoint(request, company_id: int, payload: ContactFieldInDTO):
    company = companies_services.get_owned_company(request.auth, company_id)
    return Status(201, services.create_contact_field(company, payload.label))


@router.delete('/{company_id}/contact-fields/{field_id}', response={204: None})
def delete_contact_field_endpoint(request, company_id: int, field_id: int):
    company = companies_services.get_owned_company(request.auth, company_id)
    services.delete_contact_field(company, field_id)
    return Status(204, None)


@router.post('/{company_id}/contacts/{contact_id}/tags/{tag_id}', response=ContactOutDTO)
def add_contact_tag_endpoint(request, company_id: int, contact_id: int, tag_id: int):
    company = companies_services.get_company(request.auth, company_id)
    return services.add_contact_tag(company, contact_id, tag_id)


@router.delete('/{company_id}/contacts/{contact_id}/tags/{tag_id}', response=ContactOutDTO)
def remove_contact_tag_endpoint(request, company_id: int, contact_id: int, tag_id: int):
    company = companies_services.get_company(request.auth, company_id)
    return services.remove_contact_tag(company, contact_id, tag_id)


@router.get('/{company_id}/contacts/{contact_id}/ai-activity', response=ContactAIActivityDTO)
def contact_ai_activity_endpoint(request, company_id: int, contact_id: int):
    company = companies_services.get_company(request.auth, company_id)
    return services.contact_ai_activity(company, contact_id)


@router.post('/{company_id}/contacts/{contact_id}/followups/{followup_id}/cancel', response=FollowUpOutDTO)
def cancel_followup_endpoint(request, company_id: int, contact_id: int, followup_id: int):
    company = companies_services.get_company(request.auth, company_id)
    return services.cancel_followup(company, contact_id, followup_id)


# respostas rápidas: qualquer membro gerencia (é ferramenta do atendente no dia a dia)


@router.get('/{company_id}/quick-replies', response=list[QuickReplyOutDTO])
def list_quick_replies_endpoint(request, company_id: int):
    company = companies_services.get_company(request.auth, company_id)
    return services.list_quick_replies(company)


@router.post('/{company_id}/quick-replies', response={201: QuickReplyOutDTO})
def create_quick_reply_endpoint(request, company_id: int, payload: QuickReplyInDTO):
    company = companies_services.get_company(request.auth, company_id)
    return Status(201, services.save_quick_reply(company, payload.shortcut, payload.text))


@router.put('/{company_id}/quick-replies/{reply_id}', response=QuickReplyOutDTO)
def update_quick_reply_endpoint(request, company_id: int, reply_id: int, payload: QuickReplyInDTO):
    company = companies_services.get_company(request.auth, company_id)
    reply = services.get_quick_reply(company, reply_id)
    return services.save_quick_reply(company, payload.shortcut, payload.text, reply)


@router.delete('/{company_id}/quick-replies/{reply_id}', response={204: None})
def delete_quick_reply_endpoint(request, company_id: int, reply_id: int):
    company = companies_services.get_company(request.auth, company_id)
    services.delete_quick_reply(company, reply_id)
    return Status(204, None)
