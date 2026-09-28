from ninja import Router, Status

from apps.agents import services
from apps.agents.schemas import AgentInDTO, AgentOutDTO, AgentPatchDTO
from apps.companies import services as companies_services

router = Router(tags=['Agents'])


@router.post('/{company_id}/agents', response={201: AgentOutDTO})
def create_agent_endpoint(request, company_id: int, payload: AgentInDTO):
    company = companies_services.get_company(request.auth, company_id)
    return Status(
        201,
        services.create_agent(
            company=company,
            credential_id=payload.credential_id,
            name=payload.name,
            model=payload.model,
            prompt=payload.prompt,
            description=payload.description,
            response_delay=payload.response_delay,
            split_messages=payload.split_messages,
            handoff_resume_hours=payload.handoff_resume_hours,
        ),
    )


@router.get('/{company_id}/agents', response=list[AgentOutDTO])
def list_agent_endpoint(request, company_id: int):
    company = companies_services.get_company(request.auth, company_id)
    return services.list_agents(company)


@router.get('/{company_id}/agents/{agent_id}', response=AgentOutDTO)
def get_agent_endpoint(request, company_id: int, agent_id: int):
    company = companies_services.get_company(request.auth, company_id)
    return services.get_agent(company, agent_id)


@router.patch('/{company_id}/agents/{agent_id}', response=AgentOutDTO)
def update_agent_endpoint(request, company_id: int, agent_id: int, payload: AgentPatchDTO):
    company = companies_services.get_company(request.auth, company_id)
    return services.update_agent(company, agent_id, payload.dict(exclude_unset=True))


@router.delete('/{company_id}/agents/{agent_id}', response={204: None})
def delete_agent_endpoint(request, company_id: int, agent_id: int):
    company = companies_services.get_company(request.auth, company_id)
    services.delete_agent(company, agent_id)
    return Status(204, None)
