from ninja import File, Form, Router, Status, UploadedFile

from apps.agents import services
from apps.agents.schemas import AgentFileOutDTO, DocumentOutDTO
from apps.companies import services as companies_services

router = Router(tags=['Documents'])


@router.post('/{company_id}/agents/{agent_id}/documents', response={201: DocumentOutDTO})
def upload_document_endpoint(request, company_id: int, agent_id: int, file: File[UploadedFile]):
    company = companies_services.get_company(request.auth, company_id)
    return Status(201, services.create_document(company, agent_id, file))


@router.get('/{company_id}/agents/{agent_id}/documents/{document_id}', response=DocumentOutDTO)
def get_document_endpoint(request, company_id: int, agent_id: int, document_id: int):
    company = companies_services.get_company(request.auth, company_id)
    return services.get_document(company, agent_id, document_id)


@router.get('/{company_id}/agents/{agent_id}/documents', response=list[DocumentOutDTO])
def list_documents_endpoint(request, company_id: int, agent_id):
    company = companies_services.get_company(request.auth, company_id)
    return services.list_documents(company, agent_id)


@router.delete('/{company_id}/agents/{agent_id}/documents/{document_id}', response={204: None})
def delete_document_endpoint(request, company_id: int, agent_id: int, document_id: int):
    company = companies_services.get_company(request.auth, company_id)
    services.delete_document(company, agent_id, document_id)
    return Status(204, None)


@router.post('/{company_id}/agents/{agent_id}/files', response={201: AgentFileOutDTO})
def upload_agent_file_endpoint(
    request, company_id: int, agent_id: int, file: File[UploadedFile], name: Form[str] = '', description: Form[str] = ''
):
    company = companies_services.get_company(request.auth, company_id)
    return Status(201, services.create_agent_file(company, agent_id, file, name, description))


@router.get('/{company_id}/agents/{agent_id}/files', response=list[AgentFileOutDTO])
def list_agent_files_endpoint(request, company_id: int, agent_id: int):
    company = companies_services.get_company(request.auth, company_id)
    return services.list_agent_files(company, agent_id)


@router.delete('/{company_id}/agents/{agent_id}/files/{file_id}', response={204: None})
def delete_agent_file_endpoint(request, company_id: int, agent_id: int, file_id: int):
    company = companies_services.get_company(request.auth, company_id)
    services.delete_agent_file(company, agent_id, file_id)
    return Status(204, None)
