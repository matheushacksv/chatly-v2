from ninja import File, Router, Status, UploadedFile

from apps.chats import services
from apps.chats.schemas import TemplateCreatedDTO, TemplateDetailDTO, TemplateEditedDTO, TemplateInDTO, TemplateMediaDTO, TemplateOutDTO
from apps.companies import services as companies_services

router = Router(tags=['Templates'])


@router.get('/{company_id}/accounts/{account_id}/templates', response=list[TemplateOutDTO])
def list_templates_endpoint(request, company_id: int, account_id: int):
    company = companies_services.get_company(request.auth, company_id)
    return services.list_templates(company, account_id)


@router.post('/{company_id}/accounts/{account_id}/templates/media', response={201: TemplateMediaDTO})
def upload_template_media_endpoint(request, company_id: int, account_id: int, file: File[UploadedFile]):
    company = companies_services.get_owned_company(request.auth, company_id)
    result = services.upload_template_media(company, account_id, file)
    return Status(201, result)
    


@router.get('/{company_id}/accounts/{account_id}/templates/{template_id}', response=TemplateDetailDTO)
def get_template_endpoint(request, company_id: int, account_id: int, template_id: str):
    company = companies_services.get_company(request.auth, company_id)
    return services.get_template_detail(company, account_id, template_id)


@router.post('/{company_id}/accounts/{account_id}/templates', response={201: TemplateCreatedDTO})
def create_template_endpoint(request, company_id: int, account_id: int, payload: TemplateInDTO):
    company = companies_services.get_owned_company(request.auth, company_id)
    return Status(201, services.create_template(company, account_id, payload.dict()))


@router.patch('/{company_id}/accounts/{account_id}/templates/{template_id}', response=TemplateEditedDTO)
def edit_template_endpoint(request, company_id: int, account_id: int, template_id: str, payload: TemplateInDTO):
    company = companies_services.get_owned_company(request.auth, company_id)
    return services.edit_template(company, account_id, template_id, payload.dict())


@router.delete('/{company_id}/accounts/{account_id}/templates/{template_id}', response={204: None})
def delete_template_endpoint(request, company_id: int, account_id: int, name: str, template_id: str):
    company = companies_services.get_owned_company(request.auth, company_id)
    services.delete_template(company, account_id, name, template_id)
    return Status(204, None)
