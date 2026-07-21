from django.http import HttpResponse
from ninja import File, Form, Router, Status, UploadedFile
from ninja.pagination import paginate

from apps.chats import services
from apps.chats.schemas import ManualMessageDTO, MediaUploadDTO, MessageOutDTO, SendStickerDTO, SendTemplateDTO
from apps.companies import services as companies_services

router = Router(tags=['Messages'])


@router.get('/{company_id}/contacts/{contact_id}/messages', response=list[MessageOutDTO])
@paginate
def list_messages_endpoint(request, company_id: int, contact_id: int):
    company = companies_services.get_company(request.auth, company_id)
    return services.list_messages(company, contact_id)


@router.post('/{company_id}/contacts/{contact_id}/messages', response={201: MessageOutDTO})
def send_manual_message_endpoint(request, company_id: int, contact_id: int, payload: ManualMessageDTO):
    company = companies_services.get_company(request.auth, company_id)
    return Status(201, services.send_manual_message(company, contact_id, payload.content))


@router.post('/{company_id}/contacts/{contact_id}/messages/media', response={201: MessageOutDTO})
def send_manual_media_endpoint(request, company_id: int, contact_id: int, file: File[UploadedFile], caption: Form[str] = ''):
    company = companies_services.get_company(request.auth, company_id)
    return Status(201, services.send_manual_media(company, contact_id, file, caption))


@router.get('/{company_id}/messages/{message_id}/media')
def message_media_endpoint(request, company_id: int, message_id: int):
    company = companies_services.get_company(request.auth, company_id)
    content, mime = services.get_message_media(company, message_id)
    response = HttpResponse(content, content_type=mime)
    response['Cache-Control'] = 'private, max-age=86400'
    return response


@router.post('/{company_id}/contacts/{contact_id}/messages/sticker', response={201: MessageOutDTO})
def send_sticker_endpoint(request, company_id: int, contact_id: int, payload: SendStickerDTO):
    company = companies_services.get_company(request.auth, company_id)
    return Status(201, services.send_saved_sticker(company, contact_id, payload.sticker_id))


@router.post('/{company_id}/contacts/{contact_id}/messages/template', response={201: MessageOutDTO})
def send_template_endpoint(request, company_id: int, contact_id: int, payload: SendTemplateDTO):
    company = companies_services.get_company(request.auth, company_id)
    return Status(201, services.send_template_message(company, contact_id, payload.template_name, payload.language, payload.params, payload.header_media_id))


@router.post('/{company_id}/accounts/{account_id}/media', response={201: MediaUploadDTO})
def upload_account_media_endpoint(request, company_id: int, account_id: int, file: File[UploadedFile]):
    company = companies_services.get_company(request.auth, company_id)
    media_id = services.upload_account_media(company, account_id, file)
    return Status(201, {'media_id': media_id})
