from datetime import timedelta

from django.conf import settings
from django.db.models import Case, CharField, Q, QuerySet, Value, When
from django.utils import timezone

from apps.accounts.models import User
from apps.companies import plans
from apps.companies.models import Company, Invite, Membership
from core.exceptions import NotFound


def create_company(name: str, owner: User) -> Company:
    if not settings.BILLING_ENABLED:
        return Company.objects.create(name=name, owner=owner)
    return Company.objects.create(name=name, owner=owner, plan='trial', plan_until=timezone.now() + timedelta(days=settings.TRIAL_DAYS))


def get_company(user: User, company_id: int) -> Company:
    company = Company.objects.filter(Q(owner=user) | Q(memberships__user=user), pk=company_id).distinct().first()
    if company is None:
        raise NotFound('company not found')
    return company


def get_owned_company(user: User, company_id: int) -> Company:
    company = Company.objects.filter(owner=user, pk=company_id).first()
    if company is None:
        raise NotFound('company not found')
    return company


def list_my_companies(user: User) -> QuerySet[Company]:
    return (
        Company.objects.filter(Q(owner=user) | Q(memberships__user=user))
        .distinct()
        .annotate(role=Case(When(owner=user, then=Value('owner')), default=Value('member'), output_field=CharField()))
    )


def update_company(owner: User, company_id: int, name: str) -> Company:
    company = get_owned_company(owner, company_id)
    company.name = name
    company.save(update_fields=['name'])
    return company


def delete_company(owner: User, company_id: int) -> None:
    get_owned_company(owner, company_id).delete()


def list_members(user: User, company_id: int) -> list[dict]:
    company = get_company(user, company_id)
    owner = company.owner
    members = [{'id': owner.id, 'name': company.owner.name, 'email': owner.email, 'role': 'owner'}]
    for membership in Membership.objects.filter(company=company).select_related('user').order_by('id'):
        member = membership.user
        members.append({'id': member.id, 'name': member.name, 'email': member.email, 'role': 'member'})
    return members


def remove_member(user: User, company_id: int, member_id: int) -> None:
    deleted, _ = Membership.objects.filter(company=get_owned_company(user, company_id), user_id=member_id).delete()
    if not deleted:
        raise NotFound('member not found')


def create_invite(user: User, company_id: int) -> Invite:
    company = get_owned_company(user, company_id)
    plans.require_room(company, 'members', company.memberships.count() + 1)
    return Invite.objects.create(company=company)


def list_invites(user: User, company_id: int) -> QuerySet[Invite]:
    return Invite.objects.filter(company=get_owned_company(user, company_id)).order_by('-id')


def revoke_invite(user: User, company_id: int, invite_id: int) -> None:
    deleted, _ = Invite.objects.filter(company=get_owned_company(user, company_id), pk=invite_id).delete()
    if not deleted:
        raise NotFound('invite not found')


def accept_invite(user: User, token: str) -> Company:
    invite = Invite.objects.filter(token=token, created_at__gte=timezone.now() - timedelta(days=7)).select_related('company__owner').first()
    if invite is None:
        raise NotFound('invite not found')
    company = invite.company
    if company.owner != user and not company.memberships.filter(user=user).exists():
        plans.require_room(company, 'members', company.memberships.count() + 1)
        Membership.objects.get_or_create(company=company, user=user)
    invite.delete()
    return company
