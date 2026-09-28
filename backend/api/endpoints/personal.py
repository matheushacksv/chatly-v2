import json

from django.http import HttpResponse
from ninja import File, Form, Router, Status
from ninja.files import UploadedFile

from apps.chats.schemas import SendStickerDTO, StickerInDTO, StickerOutDTO
from apps.companies import services as companies_services
from apps.personal import services
from apps.personal.schemas import (
    PersonalAccountOutDTO,
    PersonalConnectDTO,
    PersonalConnectOutDTO,
    PersonalContactOutDTO,
    PersonalDirectoryEntryDTO,
    PersonalMessageOutDTO,
    PersonalQRCodeOutDTO,
    PersonalSendMessageDTO,
    PersonalStartConversationDTO,
)

router = Router(tags=['Personal WhatsApp'])


@router.post('/webhook', auth=None)
def personal_webhook_endpoint(request):
    # HMAC é sobre o corpo cru: confere antes de interpretar qualquer coisa
    if not services.verify_webhook_signature(request.body, request.headers.get('X-OpenWA-Signature', '')):
        return HttpResponse(status=403)
    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return HttpResponse(status=400)
    if isinstance(payload, dict):
        services.handle_webhook(payload)
    return HttpResponse(status=200)


@router.post('/{company_id}/accounts', response={201: PersonalConnectOutDTO})
def connect_endpoint(request, company_id: int, payload: PersonalConnectDTO):
    company = companies_services.get_company(request.auth, company_id)
    return Status(201, services.connect(company, request.auth, payload.phone or None))


@router.get('/{company_id}/accounts/me', response=PersonalAccountOutDTO | None)
def get_my_connection_endpoint(request, company_id: int):
    company = companies_services.get_company(request.auth, company_id)
    return services.get_my_connection(company, request.auth)


@router.get('/{company_id}/accounts', response=list[PersonalAccountOutDTO])
def list_connections_endpoint(request, company_id: int):
    company = companies_services.get_owned_company(request.auth, company_id)
    return services.list_connections(company)


@router.get('/{company_id}/accounts/{account_id}/qrcode', response=PersonalQRCodeOutDTO)
def get_qrcode_endpoint(request, company_id: int, account_id: int):
    company = companies_services.get_company(request.auth, company_id)
    return PersonalQRCodeOutDTO(qrcode=services.get_qrcode(company, request.auth, account_id))


@router.post('/{company_id}/accounts/{account_id}/paircode', response=PersonalConnectOutDTO)
def request_pair_code_endpoint(request, company_id: int, account_id: int, payload: PersonalConnectDTO):
    company = companies_services.get_company(request.auth, company_id)
    return PersonalConnectOutDTO(pair_code=services.request_pairing_code(company, request.auth, account_id, payload.phone))


@router.delete('/{company_id}/accounts/{account_id}', response={204: None})
def disconnect_endpoint(request, company_id: int, account_id: int):
    company = companies_services.get_company(request.auth, company_id)
    services.disconnect(company, request.auth, account_id)
    return Status(204, None)


@router.get('/{company_id}/accounts/{account_id}/contacts', response=list[PersonalContactOutDTO])
def list_contacts_endpoint(request, company_id: int, account_id: int):
    company = companies_services.get_company(request.auth, company_id)
    return services.list_contacts(company, request.auth, account_id)


@router.get('/{company_id}/accounts/{account_id}/directory', response=list[PersonalDirectoryEntryDTO])
def list_directory_endpoint(request, company_id: int, account_id: int):
    company = companies_services.get_company(request.auth, company_id)
    return services.list_directory(company, request.auth, account_id)


@router.post('/{company_id}/accounts/{account_id}/contacts', response={201: PersonalContactOutDTO})
def start_conversation_endpoint(request, company_id: int, account_id: int, payload: PersonalStartConversationDTO):
    company = companies_services.get_company(request.auth, company_id)
    return Status(201, services.start_conversation(company, request.auth, account_id, payload.phone, payload.name))


@router.get('/{company_id}/accounts/{account_id}/contacts/{contact_id}/messages', response=list[PersonalMessageOutDTO])
def list_messages_endpoint(request, company_id: int, account_id: int, contact_id: int):
    company = companies_services.get_company(request.auth, company_id)
    return services.list_messages(company, request.auth, account_id, contact_id)


@router.post('/{company_id}/accounts/{account_id}/contacts/{contact_id}/messages', response={201: PersonalMessageOutDTO})
def send_text_message_endpoint(request, company_id: int, account_id: int, contact_id: int, payload: PersonalSendMessageDTO):
    company = companies_services.get_company(request.auth, company_id)
    return Status(201, services.send_text_message(company, request.auth, account_id, contact_id, payload.content))


@router.post('/{company_id}/accounts/{account_id}/contacts/{contact_id}/messages/media', response={201: PersonalMessageOutDTO})
def send_media_endpoint(request, company_id: int, account_id: int, contact_id: int, file: File[UploadedFile], caption: Form[str] = ''):
    company = companies_services.get_company(request.auth, company_id)
    return Status(201, services.send_media_message(company, request.auth, account_id, contact_id, file, caption))


@router.post('/{company_id}/accounts/{account_id}/stickers', response={201: StickerOutDTO})
def save_sticker_endpoint(request, company_id: int, account_id: int, payload: StickerInDTO):
    company = companies_services.get_company(request.auth, company_id)
    return Status(201, services.save_sticker(company, request.auth, account_id, payload.message_id))


@router.post('/{company_id}/accounts/{account_id}/contacts/{contact_id}/messages/sticker', response={201: PersonalMessageOutDTO})
def send_sticker_endpoint(request, company_id: int, account_id: int, contact_id: int, payload: SendStickerDTO):
    company = companies_services.get_company(request.auth, company_id)
    return Status(201, services.send_saved_sticker(company, request.auth, account_id, contact_id, payload.sticker_id))


@router.delete('/{company_id}/accounts/{account_id}/contacts/{contact_id}', response={204: None})
def delete_conversation_endpoint(request, company_id: int, account_id: int, contact_id: int):
    company = companies_services.get_company(request.auth, company_id)
    services.delete_conversation(company, request.auth, account_id, contact_id)
    return Status(204, None)
