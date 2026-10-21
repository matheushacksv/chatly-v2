from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import User
from apps.companies.models import Company, Invite, Membership
from apps.testing import auth_header


class CompanyTests(TestCase):
    url = '/api/companies/'

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(email='owner@a.com', password='strongpass123')
        cls.other_user = User.objects.create_user(email='other@a.com', password='strongpass123')
        cls.company = Company.objects.create(name='Minha Empresa', owner=cls.user)
        cls.other_company = Company.objects.create(name='Empresa Alheia', owner=cls.other_user)

    def setUp(self):
        self.auth = auth_header(self.user)

    def detail_url(self, company_id: int) -> str:
        return f'{self.url}{company_id}'

    # create

    def test_create_company(self):
        response = self.client.post(
            self.url, data={'name': 'Nova Empresa'}, content_type='application/json', headers=self.auth
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(Company.objects.filter(name='Nova Empresa', owner=self.user).exists())

    def test_create_company_unauthenticated(self):
        response = self.client.post(self.url, data={'name': 'X'}, content_type='application/json')
        self.assertEqual(response.status_code, 401)

    # list

    def test_list_only_my_companies(self):
        response = self.client.get(self.url, headers=self.auth)
        self.assertEqual(response.status_code, 200)
        names = [company['name'] for company in response.json()]
        self.assertEqual(names, ['Minha Empresa'])

    # detail

    def test_get_company(self):
        response = self.client.get(self.detail_url(self.company.pk), headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['name'], 'Minha Empresa')

    def test_get_foreign_company_returns_404(self):
        response = self.client.get(self.detail_url(self.other_company.pk), headers=self.auth)
        self.assertEqual(response.status_code, 404)

    def test_get_nonexistent_company_returns_404(self):
        response = self.client.get(self.detail_url(99999), headers=self.auth)
        self.assertEqual(response.status_code, 404)

    # update

    def test_update_company(self):
        response = self.client.patch(
            self.detail_url(self.company.pk),
            data={'name': 'Renomeada'},
            content_type='application/json',
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 200)
        self.company.refresh_from_db()
        self.assertEqual(self.company.name, 'Renomeada')

    def test_update_foreign_company_returns_404(self):
        response = self.client.patch(
            self.detail_url(self.other_company.pk),
            data={'name': 'Invadida'},
            content_type='application/json',
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 404)
        self.other_company.refresh_from_db()
        self.assertEqual(self.other_company.name, 'Empresa Alheia')

    # delete

    def test_delete_company(self):
        response = self.client.delete(self.detail_url(self.company.pk), headers=self.auth)
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Company.objects.filter(pk=self.company.pk).exists())

    def test_delete_foreign_company_returns_404(self):
        response = self.client.delete(self.detail_url(self.other_company.pk), headers=self.auth)
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Company.objects.filter(pk=self.other_company.pk).exists())


class MembershipTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(email='owner@m.com', password='strongpass123')
        cls.member = User.objects.create_user(email='member@m.com', password='strongpass123')
        cls.outsider = User.objects.create_user(email='outsider@m.com', password='strongpass123')
        cls.company = Company.objects.create(name='Empresa', owner=cls.owner)
        cls.membership = Membership.objects.create(company=cls.company, user=cls.member)

    def setUp(self):
        self.owner_auth = auth_header(self.owner)
        self.member_auth = auth_header(self.member)

    # member opera (get_company aceita membership)

    def test_member_gets_company(self):
        response = self.client.get(f'/api/companies/{self.company.pk}', headers=self.member_auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['name'], 'Empresa')

    def test_list_companies_annotates_role(self):
        member_list = self.client.get('/api/companies/', headers=self.member_auth).json()
        self.assertEqual([(c['name'], c['role']) for c in member_list], [('Empresa', 'member')])
        owner_list = self.client.get('/api/companies/', headers=self.owner_auth).json()
        self.assertEqual([(c['name'], c['role']) for c in owner_list], [('Empresa', 'owner')])

    def test_member_reads_credentials_list(self):
        response = self.client.get(f'/api/companies/{self.company.pk}/credentials', headers=self.member_auth)
        self.assertEqual(response.status_code, 200)

    def test_member_reads_whatsapp_accounts_list(self):
        response = self.client.get(f'/api/companies/{self.company.pk}/whatsapp-accounts', headers=self.member_auth)
        self.assertEqual(response.status_code, 200)

    # member não administra (get_owned_company)

    def test_member_cannot_create_credential(self):
        response = self.client.post(
            f'/api/companies/{self.company.pk}/credentials',
            data={'provider': 'anthropic', 'api_key': 'sk-x'},
            content_type='application/json',
            headers=self.member_auth,
        )
        self.assertEqual(response.status_code, 404)

    def test_member_cannot_create_whatsapp_account(self):
        response = self.client.post(
            f'/api/companies/{self.company.pk}/whatsapp-accounts',
            data={
                'agent_id': 1, 'phone_number_id': 'x', 'waba_id': 'x',
                'access_token': 'x', 'app_secret': 'x', 'verify_token': 'x',
            },
            content_type='application/json',
            headers=self.member_auth,
        )
        self.assertEqual(response.status_code, 404)

    def test_member_cannot_update_company(self):
        response = self.client.patch(
            f'/api/companies/{self.company.pk}',
            data={'name': 'Tomada'},
            content_type='application/json',
            headers=self.member_auth,
        )
        self.assertEqual(response.status_code, 404)
        self.company.refresh_from_db()
        self.assertEqual(self.company.name, 'Empresa')

    def test_member_cannot_delete_company(self):
        response = self.client.delete(f'/api/companies/{self.company.pk}', headers=self.member_auth)
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Company.objects.filter(pk=self.company.pk).exists())

    # lista de membros

    def test_list_members(self):
        response = self.client.get(f'/api/companies/{self.company.pk}/members', headers=self.owner_auth)
        self.assertEqual(response.status_code, 200)
        members = [(m['email'], m['role']) for m in response.json()]
        self.assertEqual(members, [('owner@m.com', 'owner'), ('member@m.com', 'member')])

    def test_member_also_sees_members_list(self):
        response = self.client.get(f'/api/companies/{self.company.pk}/members', headers=self.member_auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 2)

    # remoção

    def test_owner_removes_member(self):
        response = self.client.delete(
            f'/api/companies/{self.company.pk}/members/{self.member.pk}', headers=self.owner_auth
        )
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Membership.objects.filter(pk=self.membership.pk).exists())
        # removido perde acesso na hora
        response = self.client.get(f'/api/companies/{self.company.pk}', headers=self.member_auth)
        self.assertEqual(response.status_code, 404)

    def test_member_cannot_remove_member(self):
        response = self.client.delete(
            f'/api/companies/{self.company.pk}/members/{self.member.pk}', headers=self.member_auth
        )
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Membership.objects.filter(pk=self.membership.pk).exists())

    def test_remove_nonmember_returns_404(self):
        response = self.client.delete(
            f'/api/companies/{self.company.pk}/members/{self.outsider.pk}', headers=self.owner_auth
        )
        self.assertEqual(response.status_code, 404)


class InviteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(email='owner@i.com', password='strongpass123')
        cls.member = User.objects.create_user(email='member@i.com', password='strongpass123')
        cls.stranger = User.objects.create_user(email='stranger@i.com', password='strongpass123')
        cls.company = Company.objects.create(name='Empresa', owner=cls.owner)
        Membership.objects.create(company=cls.company, user=cls.member)

    def setUp(self):
        self.owner_auth = auth_header(self.owner)
        self.member_auth = auth_header(self.member)
        self.stranger_auth = auth_header(self.stranger)
        self.invites_url = f'/api/companies/{self.company.pk}/invites'

    def accept_url(self, token: str) -> str:
        return f'/api/companies/invites/{token}/accept'

    # criação

    def test_owner_creates_invite(self):
        response = self.client.post(self.invites_url, headers=self.owner_auth)
        self.assertEqual(response.status_code, 201)
        token = response.json()['token']
        self.assertTrue(Invite.objects.filter(company=self.company, token=token).exists())

    def test_member_cannot_create_invite(self):
        response = self.client.post(self.invites_url, headers=self.member_auth)
        self.assertEqual(response.status_code, 404)
        self.assertFalse(Invite.objects.exists())

    # aceite

    def test_accept_invite(self):
        invite = Invite.objects.create(company=self.company)
        response = self.client.post(self.accept_url(invite.token), headers=self.stranger_auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['id'], self.company.pk)
        self.assertTrue(Membership.objects.filter(company=self.company, user=self.stranger).exists())
        self.assertFalse(Invite.objects.filter(pk=invite.pk).exists())  # single-use

    def test_accept_used_invite_returns_404(self):
        invite = Invite.objects.create(company=self.company)
        self.client.post(self.accept_url(invite.token), headers=self.stranger_auth)
        response = self.client.post(self.accept_url(invite.token), headers=auth_header(self.member))
        self.assertEqual(response.status_code, 404)

    def test_accept_expired_invite_returns_404(self):
        invite = Invite.objects.create(company=self.company)
        Invite.objects.filter(pk=invite.pk).update(created_at=timezone.now() - timedelta(days=8))
        response = self.client.post(self.accept_url(invite.token), headers=self.stranger_auth)
        self.assertEqual(response.status_code, 404)
        self.assertFalse(Membership.objects.filter(user=self.stranger).exists())

    def test_accept_invalid_token_returns_404(self):
        response = self.client.post(self.accept_url('token-que-nao-existe'), headers=self.stranger_auth)
        self.assertEqual(response.status_code, 404)

    def test_owner_accepting_own_invite_creates_no_membership(self):
        invite = Invite.objects.create(company=self.company)
        response = self.client.post(self.accept_url(invite.token), headers=self.owner_auth)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Membership.objects.filter(user=self.owner).exists())
        self.assertFalse(Invite.objects.filter(pk=invite.pk).exists())

    def test_member_accepting_again_keeps_single_membership(self):
        invite = Invite.objects.create(company=self.company)
        response = self.client.post(self.accept_url(invite.token), headers=self.member_auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Membership.objects.filter(company=self.company, user=self.member).count(), 1)

    # listagem e revogação

    def test_list_invites_owner_only(self):
        Invite.objects.create(company=self.company)
        response = self.client.get(self.invites_url, headers=self.owner_auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)
        response = self.client.get(self.invites_url, headers=self.member_auth)
        self.assertEqual(response.status_code, 404)

    def test_revoke_invite(self):
        invite = Invite.objects.create(company=self.company)
        response = self.client.delete(f'{self.invites_url}/{invite.pk}', headers=self.owner_auth)
        self.assertEqual(response.status_code, 204)
        # link revogado não entra mais
        response = self.client.post(self.accept_url(invite.token), headers=self.stranger_auth)
        self.assertEqual(response.status_code, 404)

    def test_member_cannot_revoke_invite(self):
        invite = Invite.objects.create(company=self.company)
        response = self.client.delete(f'{self.invites_url}/{invite.pk}', headers=self.member_auth)
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Invite.objects.filter(pk=invite.pk).exists())


@override_settings(BILLING_ENABLED=True, TRIAL_DAYS=14)
class PlanTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(email='owner@a.com', password='strongpass123')
        cls.other = User.objects.create_user(email='other@a.com', password='strongpass123')

    def test_new_company_starts_trial(self):
        response = self.client.post('/api/companies/', data={'name': 'Nova'}, content_type='application/json', headers=auth_header(self.user))
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['plan'], 'trial')
        self.assertTrue(response.json()['plan_active'])
        company = Company.objects.get(name='Nova')
        self.assertAlmostEqual(company.plan_until, timezone.now() + timedelta(days=14), delta=timedelta(minutes=1))

    def test_starter_is_owner_only(self):
        company = Company.objects.create(name='S', owner=self.user, plan='starter')
        response = self.client.post(f'/api/companies/{company.pk}/invites', headers=auth_header(self.user))
        self.assertEqual(response.status_code, 402)
        invite = Invite.objects.create(company=company)  # convite de antes do downgrade
        response = self.client.post(f'/api/companies/invites/{invite.token}/accept', headers=auth_header(self.other))
        self.assertEqual(response.status_code, 402)
        self.assertFalse(Membership.objects.filter(company=company).exists())

    def test_expired_blocks_creation(self):
        company = Company.objects.create(name='V', owner=self.user, plan='business', plan_until=timezone.now() - timedelta(days=1))
        response = self.client.post(f'/api/companies/{company.pk}/invites', headers=auth_header(self.user))
        self.assertEqual(response.status_code, 402)
        self.assertIn('expirou', response.json()['detail'])
