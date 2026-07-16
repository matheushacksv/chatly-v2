from ninja import Router, Status

from apps.chats import services
from apps.chats.schemas import StickerInDTO, StickerOutDTO
from apps.companies import services as companies_services

router = Router(tags=['Stickers'])


@router.get('/{company_id}/stickers', response=list[StickerOutDTO])
def list_stickers_endpoint(request, company_id: int):
    company = companies_services.get_company(request.auth, company_id)
    return services.list_stickers(company)


@router.post('/{company_id}/stickers', response={201: StickerOutDTO})
def save_sticker_endpoint(request, company_id: int, payload: StickerInDTO):
    company = companies_services.get_company(request.auth, company_id)
    return Status(201, services.save_sticker(company, payload.message_id))


@router.delete('/{company_id}/stickers/{sticker_id}', response={204: None})
def delete_sticker_endpoint(request, company_id: int, sticker_id: int):
    company = companies_services.get_company(request.auth, company_id)
    services.delete_sticker(company, sticker_id)
    return Status(204, None)
