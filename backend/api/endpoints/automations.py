from ninja import Router, Status
from ninja.pagination import paginate

from apps.automations import services
from apps.automations.models import LeadSource
from apps.automations.schemas import (
    FlowActivateDTO,
    FlowInDTO,
    FlowOutDTO,
    FlowRunOutDTO,
    LeadAcceptedDTO,
    LeadInDTO,
    LeadSourceInDTO,
    LeadSourceOutDTO,
)
from apps.companies import services as companies_services
from core.exceptions import NotFound

router = Router(tags=['Automations'])


@router.get('/{company_id}/flows', response=list[FlowOutDTO])
def list_flows_endpoint(request, company_id: int):
    company = companies_services.get_owned_company(request.auth, company_id)
    return services.list_flows(company)


@router.post('/{company_id}/flows', response={201: FlowOutDTO})
def create_flow_endpoint(request, company_id: int, payload: FlowInDTO):
    company = companies_services.get_owned_company(request.auth, company_id)
    return Status(201, services.create_flow(company, payload.dict()))


@router.get('/{company_id}/flows/{flow_id}', response=FlowOutDTO)
def get_flow_endpoint(request, company_id: int, flow_id: int):
    company = companies_services.get_owned_company(request.auth, company_id)
    return services.get_flow(company, flow_id)


@router.put('/{company_id}/flows/{flow_id}', response=FlowOutDTO)
def update_flow_endpoint(request, company_id: int, flow_id: int, payload: FlowInDTO):
    company = companies_services.get_owned_company(request.auth, company_id)
    return services.update_flow(company, flow_id, payload.dict())


@router.delete('/{company_id}/flows/{flow_id}', response={204: None})
def delete_flow_endpoint(request, company_id: int, flow_id: int):
    company = companies_services.get_owned_company(request.auth, company_id)
    services.delete_flow(company, flow_id)
    return Status(204, None)


@router.post('/{company_id}/flows/{flow_id}/duplicate', response={201: FlowOutDTO})
def duplicate_flow_endpoint(request, company_id: int, flow_id: int):
    company = companies_services.get_owned_company(request.auth, company_id)
    return Status(201, services.duplicate_flow(company, flow_id))


@router.post('/{company_id}/flows/{flow_id}/activate', response=FlowOutDTO)
def active_flow_endpoint(request, company_id: int, flow_id: int, payload: FlowActivateDTO):
    company = companies_services.get_owned_company(request.auth, company_id)
    return services.set_active(company, flow_id, payload.active)


@router.get('/{company_id}/flows/{flow_id}/runs', response=list[FlowRunOutDTO])
@paginate
def list_runs_endpoint(request, company_id: int, flow_id: int):
    company = companies_services.get_owned_company(request.auth, company_id)
    return services.list_runs(company, flow_id)


@router.get('/{company_id}/lead-sources', response=list[LeadSourceOutDTO])
def list_sources_endpoint(request, company_id: int):
    company = companies_services.get_owned_company(request.auth, company_id)
    return services.list_sources(company)


@router.post('/{company_id}/lead-sources', response={201: LeadSourceOutDTO})
def create_source_endpoint(request, company_id: int, payload: LeadSourceInDTO):
    company = companies_services.get_owned_company(request.auth, company_id)
    return Status(201, services.create_source(company, payload.name, payload.account_id))


@router.delete('/{company_id}/lead-sources/{source_id}', response={204: None})
def delete_source_endpoint(request, company_id: int, source_id: int):
    company = companies_services.get_owned_company(request.auth, company_id)
    services.delete_source(company, source_id)
    return Status(204, None)


@router.post('/leads/{token}', auth=None, response={200: LeadAcceptedDTO})
def receive_lead_endpoint(request, token: str, payload: LeadInDTO):
    contact, started, ignored = services.receive_lead(token, payload.dict())
    return LeadAcceptedDTO(contact_id=contact.pk, flow_started=started, ignored_fields=ignored)
