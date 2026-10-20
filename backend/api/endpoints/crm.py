import json

from django.http import HttpResponse
from ninja import Router, Status

from apps.companies import services as companies_services
from apps.crm import pipedrive, services
from apps.crm.schemas import (
    AutoOpenDTO,
    FieldMapItemDTO,
    PipedriveFieldDTO,
    ActivityOutDTO,
    DealBriefDTO,
    DealCreateDTO,
    DealPatchDTO,
    NoteInDTO,
    NoteOutDTO,
    PersonBriefDTO,
    PersonCreateDTO,
    PersonLinkDTO,
    PersonResolveDTO,
    PipedriveAccountOutDTO,
    PipedriveConnectDTO,
    PipedriveDefaultsDTO,
    PipelineOutDTO,
    StageBriefDTO,
)
from core.exceptions import PipedriveError

router = Router(tags=['Pipedrive'])


@router.post('/{company_id}/pipedrive', response={201: PipedriveAccountOutDTO})
def connect_pipedrive_endpoint(request, company_id: int, payload: PipedriveConnectDTO):
    company = companies_services.get_owned_company(request.auth, company_id)
    return Status(201, services.connect(company, payload.api_key))


@router.get('/{company_id}/pipedrive', response=PipedriveAccountOutDTO)
def get_pipedrive_endpoint(request, company_id: int):
    company = companies_services.get_company(request.auth, company_id)
    return services.get_pipedrive_account(company)


@router.patch('/{company_id}/pipedrive', response=PipedriveAccountOutDTO)
def update_pipedrive_defaults_endpoint(request, company_id: int, payload: PipedriveDefaultsDTO):
    company = companies_services.get_owned_company(request.auth, company_id)
    return services.update_defaults(company, payload.pipeline_id, payload.stage_id, payload.lead_account_id)


@router.put('/{company_id}/pipedrive/auto-open', response=PipedriveAccountOutDTO)
def update_pipedrive_auto_open_endpoint(request, company_id: int, payload: AutoOpenDTO):
    company = companies_services.get_owned_company(request.auth, company_id)
    rules = [r.dict() for r in payload.rules]
    followup = payload.followup.dict() if payload.followup else None
    return services.update_auto_open(
        company, payload.stage_ids, payload.template.dict(), payload.only_in_hours, rules, payload.delay_minutes, followup
    )


@router.get('/{company_id}/pipedrive/deal-labels', response=list[StageBriefDTO])
def list_pipedrive_deal_labels_endpoint(request, company_id: int):
    company = companies_services.get_company(request.auth, company_id)
    return pipedrive.list_deal_labels(services.get_pipedrive_account(company).api_key)


@router.get('/{company_id}/pipedrive/fields', response=list[PipedriveFieldDTO])
def list_pipedrive_fields_endpoint(request, company_id: int):
    company = companies_services.get_owned_company(request.auth, company_id)
    return services.list_custom_fields(company)


@router.put('/{company_id}/pipedrive/field-map', response=PipedriveAccountOutDTO)
def update_pipedrive_field_map_endpoint(request, company_id: int, payload: list[FieldMapItemDTO]):
    company = companies_services.get_owned_company(request.auth, company_id)
    return services.update_field_map(company, [i.dict() for i in payload])


@router.delete('/{company_id}/pipedrive', response={204: None})
def disconnect_pipedrive_endpoint(request, company_id: int):
    company = companies_services.get_owned_company(request.auth, company_id)
    services.disconnect(company)
    return Status(204, None)


@router.get('/{company_id}/pipedrive/pipelines', response=list[PipelineOutDTO])
def list_pipelines_endpoint(request, company_id: int):
    company = companies_services.get_company(request.auth, company_id)
    pd = services.get_pipedrive_account(company)
    return services.pipeline_with_stages(pd)


@router.get('/{company_id}/pipedrive/persons', response=list[PersonBriefDTO])
def search_persons_endpoint(request, company_id: int, term: str):
    company = companies_services.get_company(request.auth, company_id)
    pd = services.get_pipedrive_account(company)
    return pipedrive.search_persons(pd.api_key, term)


@router.get('/{company_id}/contacts/{contact_id}/pipedrive/person', response=PersonResolveDTO)
def resolve_person_endpoint(request, company_id: int, contact_id: int):
    company = companies_services.get_company(request.auth, company_id)
    pd = services.get_pipedrive_account(company)
    contact = services.get_contact(company, contact_id)
    return services.resolve_person(pd, contact)


@router.put('/{company_id}/contacts/{contact_id}/pipedrive/person', response=PersonBriefDTO)
def link_person_endpoint(request, company_id: int, contact_id: int, payload: PersonLinkDTO):
    company = companies_services.get_company(request.auth, company_id)
    pd = services.get_pipedrive_account(company)
    contact = services.get_contact(company, contact_id)
    person = pipedrive.get_person(pd.api_key, payload.person_id)
    services.link_person(contact, payload.person_id)
    return person


@router.post('/{company_id}/contacts/{contact_id}/pipedrive/person', response={201: PersonBriefDTO})
def create_person_endpoint(request, company_id: int, contact_id: int, payload: PersonCreateDTO):
    company = companies_services.get_company(request.auth, company_id)
    pd = services.get_pipedrive_account(company)
    contact = services.get_contact(company, contact_id)
    return Status(201, services.create_person_for_contact(pd, contact, payload.name))


@router.get('/{company_id}/contacts/{contact_id}/pipedrive/deals', response=list[DealBriefDTO])
def list_deals_endpoint(request, company_id: int, contact_id: int):
    company = companies_services.get_company(request.auth, company_id)
    pd = services.get_pipedrive_account(company)
    contact = services.get_contact(company, contact_id)
    if not contact.pipedrive_person_id:
        return []
    return pipedrive.list_open_deals(pd.api_key, contact.pipedrive_person_id)


@router.post('/{company_id}/contacts/{contact_id}/pipedrive/deals', response={201: DealBriefDTO})
def create_deal_endpoint(request, company_id: int, contact_id: int, payload: DealCreateDTO):
    company = companies_services.get_company(request.auth, company_id)
    pd = services.get_pipedrive_account(company)
    contact = services.get_contact(company, contact_id)
    deal = services.create_deal_for_contact(pd, contact, payload.title, payload.value, payload.pipeline_id, payload.stage_id)
    return Status(201, deal)


@router.patch('/{company_id}/pipedrive/deals/{deal_id}', response=DealBriefDTO)
def update_deal_endpoint(request, company_id: int, deal_id: int, payload: DealPatchDTO):
    company = companies_services.get_company(request.auth, company_id)
    pd = services.get_pipedrive_account(company)
    return pipedrive.update_deal(pd.api_key, deal_id, stage_id=payload.stage_id, status=payload.status)


@router.get('/{company_id}/pipedrive/deals/{deal_id}/notes', response=list[NoteOutDTO])
def list_notes_endpoint(request, company_id: int, deal_id: int):
    company = companies_services.get_company(request.auth, company_id)
    pd = services.get_pipedrive_account(company)
    return pipedrive.list_notes(pd.api_key, deal_id)


@router.post('/{company_id}/pipedrive/deals/{deal_id}/notes', response={201: NoteOutDTO})
def create_note_endpoint(request, company_id: int, deal_id: int, payload: NoteInDTO):
    company = companies_services.get_company(request.auth, company_id)
    pd = services.get_pipedrive_account(company)
    return Status(201, pipedrive.create_note(pd.api_key, payload.content, deal_id=deal_id))


@router.get('/{company_id}/pipedrive/deals/{deal_id}/activities', response=list[ActivityOutDTO])
def list_activities_endpoint(request, company_id: int, deal_id: int):
    company = companies_services.get_company(request.auth, company_id)
    pd = services.get_pipedrive_account(company)
    return pipedrive.list_open_activities(pd.api_key, deal_id)


@router.post('/{company_id}/pipedrive/activities/{activity_id}/done', response=ActivityOutDTO)
def mark_acitivity_done_endpoint(request, company_id: int, activity_id: int):
    company = companies_services.get_company(request.auth, company_id)
    pd = services.get_pipedrive_account(company)
    return pipedrive.mark_activity_done(pd.api_key, activity_id)


@router.post('/pipedrive/webhook/{token}', auth=None)
def pipedrive_webhook_endpoint(request, token: str):
    account = services.get_account_by_webhook_token(token)
    if account is None:
        return HttpResponse(status=404)

    try:
        payload = json.loads(request.body)
        meta = payload['meta']
        action, entity = meta['action'], meta['entity']
        data = payload['data']
    except (KeyError, ValueError, TypeError):
        return HttpResponse(status=200)

    if entity == 'deal' and action in ('create', 'change') and isinstance(data, dict):
        try:
            services.ingest_deal_event(account, action, data, payload.get('previous'), meta.get('change_source', ''))
        except PipedriveError:
            pass
    return HttpResponse(status=200)
