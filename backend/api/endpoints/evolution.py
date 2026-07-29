import json

from django.http import HttpResponse
from ninja import File, Form, Router, Status
from ninja.files import UploadedFile

from apps.chats.schemas import SendStickerDTO, StickerInDTO, StickerOutDTO
from apps.companies import services as companies_services
from apps.evolution import services
from apps.evolution.schemas import (
    EvolutionAccountOutDTO,
    EvolutionConnectDTO,
    EvolutionConnectOutDTO,
    EvolutionContactOutDTO,
    EvolutionDirectoryEntryDTO,
    EvolutionMessageOutDTO,
    EvolutionQRCodeOutDTO,
    EvolutionSendMessageDTO,
    EvolutionStartConversationDTO,
)

router = Router(tags=['Evolution'])


@router.post('/webhook', auth=None)
def evolution_webhook_endpoint(request):
    payload = json.loads(request.body)
    services.handle_webhook(payload)
    return HttpResponse(status=200)


@router.post('/{company_id}/evolution-accounts', response={201: EvolutionConnectOutDTO})
def connect_endpoint(request, company_id: int, payload: EvolutionConnectDTO):
    company = companies_services.get_company(request.auth, company_id)
    instance_name = f'c{company_id}u{request.auth.id}'
    result = services.create_and_connect_evolution_instance(company, request.auth, instance_name, payload.phone or None)
    if payload.phone:
        return Status(201, EvolutionConnectOutDTO(pair_code=result))
    return Status(201, EvolutionConnectOutDTO(qrcode=result))


@router.get('/{company_id}/evolution-accounts/me', response=EvolutionAccountOutDTO | None)
def get_my_connection_endpoint(request, company_id: int):
    company = companies_services.get_company(request.auth, company_id)
    return services.get_my_connection(company, request.auth)


@router.get('/{company_id}/evolution-accounts', response=list[EvolutionAccountOutDTO])
def list_connections_endpoint(request, company_id: int):
    company = companies_services.get_owned_company(request.auth, company_id)
    return services.list_connections(company)


@router.get('/{company_id}/evolution-accounts/{account_id}/qrcode', response=EvolutionQRCodeOutDTO)
def get_qrcode_endpoint(request, company_id: int, account_id: int):
    company = companies_services.get_company(request.auth, company_id)
    return EvolutionQRCodeOutDTO(qrcode=services.get_qrcode(company, request.auth, account_id))


@router.post('/{company_id}/evolution-accounts/{account_id}/paircode', response=EvolutionConnectOutDTO)
def request_pair_code_endpoint(request, company_id: int, account_id: int, payload: EvolutionConnectDTO):
    company = companies_services.get_company(request.auth, company_id)
    return EvolutionConnectOutDTO(pair_code=services.request_pairing_code(company, request.auth, account_id, payload.phone))


@router.delete('/{company_id}/evolution-accounts/{account_id}', response={204: None})
def disconnect_endpoint(request, company_id: int, account_id: int):
    company = companies_services.get_company(request.auth, company_id)
    services.disconnect(company, request.auth, account_id)
    return Status(204, None)


@router.get('/{company_id}/evolution-accounts/{account_id}/contacts', response=list[EvolutionContactOutDTO])
def list_contacts_endpoint(request, company_id: int, account_id: int):
    company = companies_services.get_company(request.auth, company_id)
    return services.list_contacts(company, request.auth, account_id)


@router.get('/{company_id}/evolution-accounts/{account_id}/directory', response=list[EvolutionDirectoryEntryDTO])
def list_directory_endpoint(request, company_id: int, account_id: int):
    company = companies_services.get_company(request.auth, company_id)
    return services.list_directory(company, request.auth, account_id)


@router.post('/{company_id}/evolution-accounts/{account_id}/contacts', response={201: EvolutionContactOutDTO})
def start_conversation_endpoint(request, company_id: int, account_id: int, payload: EvolutionStartConversationDTO):
    company = companies_services.get_company(request.auth, company_id)
    return Status(201, services.start_conversation(company, request.auth, account_id, payload.phone, payload.name))


@router.get('/{company_id}/evolution-accounts/{account_id}/contacts/{contact_id}/messages', response=list[EvolutionMessageOutDTO])
def list_messages_endpoint(request, company_id: int, account_id: int, contact_id: int):
    company = companies_services.get_company(request.auth, company_id)
    return services.list_messages(company, request.auth, account_id, contact_id)


@router.post('/{company_id}/evolution-accounts/{account_id}/contacts/{contact_id}/messages', response={201: EvolutionMessageOutDTO})
def send_text_message_endpoint(request, company_id: int, account_id: int, contact_id: int, payload: EvolutionSendMessageDTO):
    company = companies_services.get_company(request.auth, company_id)
    return Status(201, services.send_text_message(company, request.auth, account_id, contact_id, payload.content))


@router.post('/{company_id}/evolution-accounts/{account_id}/contacts/{contact_id}/messages/media', response={201: EvolutionMessageOutDTO})
def send_media_endpoint(request, company_id: int, account_id: int, contact_id: int, file: File[UploadedFile], caption: Form[str] = ''):
    company = companies_services.get_company(request.auth, company_id)
    return Status(201, services.send_media_message(company, request.auth, account_id, contact_id, file, caption))


@router.post('/{company_id}/evolution-accounts/{account_id}/stickers', response={201: StickerOutDTO})
def save_evolution_sticker_endpoint(request, company_id: int, account_id: int, payload: StickerInDTO):
    company = companies_services.get_company(request.auth, company_id)
    return Status(201, services.save_sticker(company, request.auth, account_id, payload.message_id))


@router.post('/{company_id}/evolution-accounts/{account_id}/contacts/{contact_id}/messages/sticker', response={201: EvolutionMessageOutDTO})
def send_evolution_sticker_endpoint(request, company_id: int, account_id: int, contact_id: int, payload: SendStickerDTO):
    company = companies_services.get_company(request.auth, company_id)
    return Status(201, services.send_saved_sticker(company, request.auth, account_id, contact_id, payload.sticker_id))


@router.delete('/{company_id}/evolution-accounts/{account_id}/contacts/{contact_id}', response={204: None})
def delete_conversation_endpoint(request, company_id: int, account_id: int, contact_id: int):
    company = companies_services.get_company(request.auth, company_id)
    services.delete_conversation(company, request.auth, account_id, contact_id)
    return Status(204, None)
