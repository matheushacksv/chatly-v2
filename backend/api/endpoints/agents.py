from ninja import Router, Status

from apps.agents import services
from apps.agents.schemas import (
    AgentInDTO,
    AgentOutDTO,
    AgentPatchDTO,
    AgentToolInDTO,
    AgentToolOutDTO,
    AgentToolPatchDTO,
    AgentToolTestDTO,
    AgentToolTestResultDTO,
    PlaygroundInDTO,
    PlaygroundOutDTO,
)
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
            ai_hours_start=payload.ai_hours_start,
            ai_hours_end=payload.ai_hours_end,
            ai_days=payload.ai_days,
            native_tools=payload.native_tools,
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


# ferramentas personalizadas: só o dono cria, edita, apaga e testa (guardam tokens de sistemas externos)


@router.get('/{company_id}/agents/{agent_id}/tools', response=list[AgentToolOutDTO])
def list_agent_tools_endpoint(request, company_id: int, agent_id: int):
    company = companies_services.get_company(request.auth, company_id)
    return services.list_agent_tools(company, agent_id)


@router.post('/{company_id}/agents/{agent_id}/tools', response={201: AgentToolOutDTO})
def create_agent_tool_endpoint(request, company_id: int, agent_id: int, payload: AgentToolInDTO):
    company = companies_services.get_owned_company(request.auth, company_id)
    return Status(201, services.create_agent_tool(company, agent_id, payload.dict()))


@router.patch('/{company_id}/agents/{agent_id}/tools/{tool_id}', response=AgentToolOutDTO)
def update_agent_tool_endpoint(request, company_id: int, agent_id: int, tool_id: int, payload: AgentToolPatchDTO):
    company = companies_services.get_owned_company(request.auth, company_id)
    return services.update_agent_tool(company, agent_id, tool_id, payload.dict(exclude_unset=True))


@router.delete('/{company_id}/agents/{agent_id}/tools/{tool_id}', response={204: None})
def delete_agent_tool_endpoint(request, company_id: int, agent_id: int, tool_id: int):
    company = companies_services.get_owned_company(request.auth, company_id)
    services.delete_agent_tool(company, agent_id, tool_id)
    return Status(204, None)


@router.post('/{company_id}/agents/{agent_id}/tools/{tool_id}/test', response=AgentToolTestResultDTO)
def test_agent_tool_endpoint(request, company_id: int, agent_id: int, tool_id: int, payload: AgentToolTestDTO):
    company = companies_services.get_owned_company(request.auth, company_id)
    tool = services.get_agent_tool(company, agent_id, tool_id)
    ok, result = services.run_agent_tool(tool, payload.arguments, {'nome': 'Contato de teste', 'telefone': '5511999999999'})
    return {'ok': ok, 'result': result}


@router.post('/{company_id}/agents/{agent_id}/playground', response=PlaygroundOutDTO)
def playground_endpoint(request, company_id: int, agent_id: int, payload: PlaygroundInDTO):
    from apps.chats import ai_tools

    company = companies_services.get_company(request.auth, company_id)
    agent = services.get_agent(company, agent_id)
    # alterações do formulário valem só neste teste: o agente não é salvo
    if payload.prompt is not None:
        agent.prompt = payload.prompt
    if payload.native_tools is not None:
        agent.native_tools = payload.native_tools
    return ai_tools.run_playground(
        agent, [m.dict() for m in payload.messages], payload.name, payload.phone, payload.fields, payload.memory
    )
