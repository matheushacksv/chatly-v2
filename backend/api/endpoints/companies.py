from ninja import Router, Status

from apps.companies import services
from apps.companies.schemas import CompanyInDTO, CompanyOutDTO, CompanyPatchDTO, InviteOutDTO, MemberOutDTO

router = Router(tags=['Company'])


@router.post('/', response={201: CompanyOutDTO})
def create_company_endpoint(request, payload: CompanyInDTO):
    return Status(201, services.create_company(owner=request.auth, name=payload.name))


@router.get('/', response=list[CompanyOutDTO])
def list_companies_endpoint(request):
    return services.list_my_companies(request.auth)


@router.post('/invites/{token}/accept', response=CompanyOutDTO)
def accept_invite_endpoint(request, token: str):
    return services.accept_invite(request.auth, token)


@router.get('/{company_id}', response=CompanyOutDTO)
def get_company_endpoint(request, company_id: int):
    return services.get_company(request.auth, company_id)


@router.patch('/{company_id}', response={200: CompanyOutDTO})
def update_company_endpoint(request, company_id: int, payload: CompanyPatchDTO):
    return Status(200, services.update_company(owner=request.auth, company_id=company_id, name=payload.name))


@router.delete('/{company_id}', response={204: None})
def delete_company_endpoint(request, company_id: int):
    services.delete_company(owner=request.auth, company_id=company_id)
    return Status(204, None)


@router.get('/{company_id}/members', response=list[MemberOutDTO])
def list_members_endpoint(request, company_id: int):
    return services.list_members(request.auth, company_id)


@router.delete('/{company_id}/members/{member_id}', response={204: None})
def remove_member_endpoint(request, company_id: int, member_id: int):
    services.remove_member(request.auth, company_id, member_id)
    return Status(204, None)


@router.post('/{company_id}/invites', response={201: InviteOutDTO})
def create_invite_endpoint(request, company_id: int):
    return Status(201, services.create_invite(request.auth, company_id))


@router.get('/{company_id}/invites', response=list[InviteOutDTO])
def list_invites_endpoint(request, company_id: int):
    return services.list_invites(request.auth, company_id)


@router.delete('/{company_id}/invites/{invite_id}', response={204: None})
def revoke_invite_endpoint(request, company_id: int, invite_id: int):
    services.revoke_invite(request.auth, company_id, invite_id)
    return Status(204, None)
