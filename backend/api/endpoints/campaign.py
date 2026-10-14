from ninja import Router, Status
from ninja.pagination import paginate

from apps.chats import services
from apps.chats.schemas import CampaignAudienceInDTO, CampaignAudienceOutDTO, CampaignInDTO, CampaignOutDTO
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
    )
    return Status(201, campaign)


@router.post('/{company_id}/campaigns/audience', response=CampaignAudienceOutDTO)
def preview_campaign_audience_endpoint(request, company_id: int, payload: CampaignAudienceInDTO):
    company = companies_services.get_company(request.auth, company_id)
    return services.preview_campaign_audience(company, payload.account_id, payload.tag_groups, payload.exclude_tag_ids)


@router.post('/{company_id}/campaigns/{campaign_id}/cancel', response=CampaignOutDTO)
def cancel_campaign_endpoint(request, company_id: int, campaign_id: int):
    company = companies_services.get_company(request.auth, company_id)
    return services.cancel_campaign(company, campaign_id)


@router.get('/{company_id}/campaigns/{campaign_id}', response=CampaignOutDTO)
def get_campaign_endpoint(request, company_id: int, campaign_id: int):
    company = companies_services.get_company(request.auth, company_id)
    return services.get_campaign(company, campaign_id)
