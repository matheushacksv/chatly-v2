from ninja import Router, Status
from ninja.pagination import paginate

from apps.chats import services
from apps.chats.schemas import CampaignAudienceInDTO, CampaignDraftInDTO, CampaignAudienceOutDTO, CampaignInDTO, CampaignOutDTO, CampaignRecipientOutDTO
from apps.companies import services as companies_services

router = Router(tags=['Campaigns'])


@router.get('/{company_id}/campaigns', response=list[CampaignOutDTO])
@paginate
def list_campaigns_endpoint(request, company_id: int):
    company = companies_services.get_company(request.auth, company_id)
    return services.list_campaigns(company)


@router.post('/{company_id}/campaigns', response={201: CampaignOutDTO})
def create_campaign_endpoint(request, company_id: int, payload: CampaignInDTO):
    company = companies_services.get_company(request.auth, company_id)
    campaign = services.create_campaign(
        company,
        payload.account_id,
        payload.name,
        payload.template_name,
        payload.template_language,
        payload.body_params,
        payload.contact_ids,
        [n.dict() for n in payload.numbers],
        payload.header_media_id,
        payload.flow_id,
        payload.tag_groups,
        payload.exclude_tag_ids,
        payload.scheduled_at,
        payload.import_tag_ids,
        payload.draft_id,
    )
    return Status(201, campaign)


@router.post('/{company_id}/campaigns/audience', response=CampaignAudienceOutDTO)
def preview_campaign_audience_endpoint(request, company_id: int, payload: CampaignAudienceInDTO):
    company = companies_services.get_company(request.auth, company_id)
    return services.preview_campaign_audience(company, payload.account_id, payload.tag_groups, payload.exclude_tag_ids)


# antes das rotas com {campaign_id}: senão "drafts" casa como id
@router.post('/{company_id}/campaigns/drafts', response={201: CampaignOutDTO})
def create_campaign_draft_endpoint(request, company_id: int, payload: CampaignDraftInDTO):
    company = companies_services.get_company(request.auth, company_id)
    return Status(201, services.save_campaign_draft(company, payload.account_id, payload.name, payload.draft))


@router.put('/{company_id}/campaigns/drafts/{draft_id}', response=CampaignOutDTO)
def update_campaign_draft_endpoint(request, company_id: int, draft_id: int, payload: CampaignDraftInDTO):
    company = companies_services.get_company(request.auth, company_id)
    return services.save_campaign_draft(company, payload.account_id, payload.name, payload.draft, draft_id)


@router.post('/{company_id}/campaigns/{campaign_id}/cancel', response=CampaignOutDTO)
def cancel_campaign_endpoint(request, company_id: int, campaign_id: int):
    company = companies_services.get_company(request.auth, company_id)
    return services.cancel_campaign(company, campaign_id)


@router.delete('/{company_id}/campaigns/{campaign_id}', response={204: None})
def delete_campaign_endpoint(request, company_id: int, campaign_id: int):
    company = companies_services.get_owned_company(request.auth, company_id)
    services.delete_campaign(company, campaign_id)
    return Status(204, None)


@router.get('/{company_id}/campaigns/{campaign_id}', response=CampaignOutDTO)
def get_campaign_endpoint(request, company_id: int, campaign_id: int):
    company = companies_services.get_company(request.auth, company_id)
    return services.get_campaign(company, campaign_id)


@router.get('/{company_id}/campaigns/{campaign_id}/recipients', response=list[CampaignRecipientOutDTO])
def campaign_recipients_endpoint(request, company_id: int, campaign_id: int):
    company = companies_services.get_company(request.auth, company_id)
    return services.campaign_recipients(company, campaign_id)

