from unittest.mock import MagicMock, patch

import httpx
from django.db import connection
from django.test import TestCase

from apps.accounts.models import User
from apps.agents.models import Agent, Credential
from apps.automations.models import Flow, FlowRun
from apps.chats.models import Contact, WhatsAppAccount
from apps.companies.models import Company, Membership
from apps.crm import pipedrive
from apps.crm.models import PipedriveAccount
from apps.testing import auth_header
from core.exceptions import NotFound, PipedriveError, ValidationError


def _resp(json_data=None, status=200):
    resp = MagicMock()
    resp.json.return_value = json_data or {}
    if status >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            'err', request=MagicMock(), response=MagicMock(status_code=status)
        )
    return resp


@patch('apps.crm.pipedrive.httpx.request')
class PipedriveRequestTests(TestCase):
    def test_sends_api_token_header(self, mock_request):
        mock_request.return_value = _resp({'data': {'id': 1}})
        pipedrive.validate('minha-chave')
        kwargs = mock_request.call_args.kwargs
        self.assertEqual(kwargs['headers'], {'x-api-token': 'minha-chave'})
        self.assertEqual(kwargs['timeout'], 10)

    def test_401_raises_pipedrive_error(self, mock_request):
        mock_request.return_value = _resp(status=401)
        with self.assertRaises(PipedriveError) as ctx:
            pipedrive.validate('chave-ruim')
        self.assertEqual(ctx.exception.status_code, 502)
        self.assertIn('invalid', ctx.exception.message)

    def test_404_raises_not_found(self, mock_request):
        mock_request.return_value = _resp(status=404)
        with self.assertRaises(NotFound):
            pipedrive.get_person('key', 999)

    def test_429_raises_rate_limit(self, mock_request):
        mock_request.return_value = _resp(status=429)
        with self.assertRaises(PipedriveError) as ctx:
            pipedrive.validate('key')
        self.assertIn('rate limit', ctx.exception.message)

    def test_other_http_error_raises_generic(self, mock_request):
        mock_request.return_value = _resp(status=500)
        with self.assertRaises(PipedriveError) as ctx:
            pipedrive.validate('key')
        self.assertIn('500', ctx.exception.message)

    def test_network_error_raises_pipedrive_error(self, mock_request):
        mock_request.side_effect = httpx.ConnectError('boom')
        with self.assertRaises(PipedriveError) as ctx:
            pipedrive.validate('key')
        self.assertIn('could not reach', ctx.exception.message)

    def test_paginate_follows_cursor(self, mock_request):
        mock_request.side_effect = [
            _resp({'data': [{'id': 1}], 'additional_data': {'next_cursor': 'abc'}}),
            _resp({'data': [{'id': 2}], 'additional_data': {'next_cursor': None}}),
        ]
        result = pipedrive.list_pipelines('key')
        self.assertEqual([p['id'] for p in result], [1, 2])
        second_call_params = mock_request.call_args_list[1].kwargs['params']
        self.assertEqual(second_call_params['cursor'], 'abc')

    def test_list_stages_filters_deleted(self, mock_request):
        mock_request.return_value = _resp({'data': [{'id': 1, 'is_deleted': False}, {'id': 2, 'is_deleted': True}]})
        result = pipedrive.list_stages('key')
        self.assertEqual([s['id'] for s in result], [1])

    def test_search_persons_unwraps_items(self, mock_request):
        mock_request.return_value = _resp({'data': {'items': [{'item': {'id': 7, 'name': 'Ana'}}]}})
        result = pipedrive.search_persons('key', 'Ana')
        self.assertEqual(result, [{'id': 7, 'name': 'Ana'}])

    def test_search_persons_empty(self, mock_request):
        mock_request.return_value = _resp({'data': {'items': None}})
        self.assertEqual(pipedrive.search_persons('key', 'zz'), [])

    def test_create_person_with_phone(self, mock_request):
        mock_request.return_value = _resp({'data': {'id': 5}})
        pipedrive.create_person('key', 'Ana', '5511999999999')
        payload = mock_request.call_args.kwargs['json']
        self.assertEqual(payload['phones'], [{'value': '5511999999999', 'primary': True}])

    def test_list_notes_none_data_returns_empty(self, mock_request):
        mock_request.return_value = _resp({'data': None})
        self.assertEqual(pipedrive.list_notes('key', 1), [])

    def test_update_deal_partial_payload(self, mock_request):
        mock_request.return_value = _resp({'data': {'id': 1}})
        pipedrive.update_deal('key', 1, status='won')
        self.assertEqual(mock_request.call_args.kwargs['json'], {'status': 'won'})

    def test_list_open_activities_uses_v1_deal_path(self, mock_request):
        mock_request.return_value = _resp({'data': [{'id': 3}]})
        pipedrive.list_open_activities('key', 42)
        url = mock_request.call_args.args[1]
        self.assertEqual(url, f'{pipedrive.V1_URL}/deals/42/activities')
        self.assertEqual(mock_request.call_args.kwargs['params']['done'], 0)


class PipedriveAccountModelTests(TestCase):
    def test_api_key_encrypted_at_rest(self):
        user = User.objects.create_user(email='owner@a.com', password='strongpass123')
        company = Company.objects.create(name='Empresa', owner=user)
        account = PipedriveAccount.objects.create(company=company, api_key='chave-secreta-123')

        with connection.cursor() as cursor:
            cursor.execute('SELECT api_key FROM crm_pipedriveaccount WHERE id = %s', [account.id])
            raw = cursor.fetchone()[0]
        self.assertNotIn('chave-secreta-123', raw)

        account.refresh_from_db()
        self.assertEqual(account.api_key, 'chave-secreta-123')


class BaseCrmTestCase(TestCase):
    """Owner + membro + outsider, contact no tenant principal, Pipedrive conectado."""

    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(email='owner@a.com', password='strongpass123')
        cls.member = User.objects.create_user(email='member@a.com', password='strongpass123')
        cls.outsider = User.objects.create_user(email='out@a.com', password='strongpass123')
        cls.company = Company.objects.create(name='Minha Empresa', owner=cls.owner)
        cls.other_company = Company.objects.create(name='Empresa Alheia', owner=cls.outsider)
        Membership.objects.create(company=cls.company, user=cls.member)
        credential = Credential.objects.create(company=cls.company, provider='anthropic', api_key='sk-x')
        agent = Agent.objects.create(company=cls.company, name='V', prompt='.', credential=credential)
        cls.account = WhatsAppAccount.objects.create(
            company=cls.company, agent=agent, phone_number_id='111', waba_id='w1',
            access_token='t', app_secret='s', verify_token='v',
        )
        cls.contact = Contact.objects.create(account=cls.account, wa_id='5511999999999', name='Cliente A')
        cls.pd = PipedriveAccount.objects.create(
            company=cls.company, api_key='pd-key', default_pipeline_id=1, default_stage_id=2
        )

    def setUp(self):
        self.auth = auth_header(self.owner)
        self.member_auth = auth_header(self.member)
        self.outsider_auth = auth_header(self.outsider)
        self.base = f'/api/companies/{self.company.id}'


class PipedriveConfigTests(BaseCrmTestCase):
    @patch('apps.crm.pipedrive.validate')
    def test_connect_validates_and_saves(self, mock_validate):
        self.pd.delete()
        response = self.client.post(
            f'{self.base}/pipedrive', {'api_key': 'nova-chave'}, content_type='application/json', headers=self.auth
        )
        self.assertEqual(response.status_code, 201)
        mock_validate.assert_called_once_with('nova-chave')
        self.assertEqual(PipedriveAccount.objects.get(company=self.company).api_key, 'nova-chave')
        self.assertNotIn('api_key', response.json())

    @patch('apps.crm.pipedrive.validate', side_effect=PipedriveError('invalid Pipedrive api key'))
    def test_connect_invalid_key_saves_nothing(self, mock_validate):
        self.pd.delete()
        response = self.client.post(
            f'{self.base}/pipedrive', {'api_key': 'ruim'}, content_type='application/json', headers=self.auth
        )
        self.assertEqual(response.status_code, 502)
        self.assertFalse(PipedriveAccount.objects.filter(company=self.company).exists())

    @patch('apps.crm.pipedrive.validate')
    def test_reconnect_keeps_defaults(self, mock_validate):
        response = self.client.post(
            f'{self.base}/pipedrive', {'api_key': 'trocada'}, content_type='application/json', headers=self.auth
        )
        self.assertEqual(response.status_code, 201)
        self.pd.refresh_from_db()
        self.assertEqual(self.pd.api_key, 'trocada')
        self.assertEqual(self.pd.default_pipeline_id, 1)

    def test_connect_requires_owner(self):
        response = self.client.post(
            f'{self.base}/pipedrive', {'api_key': 'x'}, content_type='application/json', headers=self.member_auth
        )
        self.assertEqual(response.status_code, 404)

    def test_get_config_as_member(self):
        response = self.client.get(f'{self.base}/pipedrive', headers=self.member_auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['default_pipeline_id'], 1)

    def test_get_config_not_connected(self):
        self.pd.delete()
        response = self.client.get(f'{self.base}/pipedrive', headers=self.auth)
        self.assertEqual(response.status_code, 404)

    def test_get_config_outsider_blocked(self):
        response = self.client.get(f'{self.base}/pipedrive', headers=self.outsider_auth)
        self.assertEqual(response.status_code, 404)

    def test_patch_defaults(self):
        response = self.client.patch(
            f'{self.base}/pipedrive', {'pipeline_id': 9, 'stage_id': 8}, content_type='application/json', headers=self.auth
        )
        self.assertEqual(response.status_code, 200)
        self.pd.refresh_from_db()
        self.assertEqual((self.pd.default_pipeline_id, self.pd.default_stage_id), (9, 8))

    def test_disconnect(self):
        response = self.client.delete(f'{self.base}/pipedrive', headers=self.auth)
        self.assertEqual(response.status_code, 204)
        self.assertFalse(PipedriveAccount.objects.filter(company=self.company).exists())

    @patch('apps.crm.pipedrive.list_stages')
    @patch('apps.crm.pipedrive.list_pipelines')
    def test_pipelines_aggregates_stages(self, mock_pipelines, mock_stages):
        mock_pipelines.return_value = [{'id': 1, 'name': 'Vendas'}, {'id': 2, 'name': 'Pós'}]
        mock_stages.return_value = [
            {'id': 10, 'name': 'Lead', 'pipeline_id': 1},
            {'id': 20, 'name': 'Entrega', 'pipeline_id': 2},
        ]
        response = self.client.get(f'{self.base}/pipedrive/pipelines', headers=self.member_auth)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual([s['id'] for s in data[0]['stages']], [10])
        self.assertEqual([s['id'] for s in data[1]['stages']], [20])


class PipedrivePersonTests(BaseCrmTestCase):
    @patch('apps.crm.pipedrive.get_person')
    def test_resolve_linked(self, mock_get):
        self.contact.pipedrive_person_id = 77
        self.contact.save()
        mock_get.return_value = {'id': 77, 'name': 'Ana', 'phones': [{'value': '5511999999999'}]}
        response = self.client.get(f'{self.base}/contacts/{self.contact.id}/pipedrive/person', headers=self.member_auth)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['person']['id'], 77)
        self.assertEqual(data['person']['phones'], ['5511999999999'])

    @patch('apps.crm.pipedrive.search_persons')
    @patch('apps.crm.pipedrive.get_person', side_effect=NotFound('not found on Pipedrive'))
    def test_resolve_deleted_person_clears_link(self, mock_get, mock_search):
        self.contact.pipedrive_person_id = 77
        self.contact.save()
        mock_search.return_value = [{'id': 5, 'name': 'Ana'}]
        response = self.client.get(f'{self.base}/contacts/{self.contact.id}/pipedrive/person', headers=self.auth)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIsNone(data['person'])
        self.assertEqual(data['suggestions'][0]['id'], 5)
        self.contact.refresh_from_db()
        self.assertIsNone(self.contact.pipedrive_person_id)

    @patch('apps.crm.pipedrive.search_persons', return_value=[])
    def test_resolve_unlinked_no_matches(self, mock_search):
        response = self.client.get(f'{self.base}/contacts/{self.contact.id}/pipedrive/person', headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'person': None, 'suggestions': []})
        mock_search.assert_called_once_with('pd-key', '5511999999999')

    @patch('apps.crm.pipedrive.get_person')
    def test_link_person(self, mock_get):
        mock_get.return_value = {'id': 42, 'name': 'Ana', 'phones': []}
        response = self.client.put(
            f'{self.base}/contacts/{self.contact.id}/pipedrive/person',
            {'person_id': 42}, content_type='application/json', headers=self.member_auth,
        )
        self.assertEqual(response.status_code, 200)
        self.contact.refresh_from_db()
        self.assertEqual(self.contact.pipedrive_person_id, 42)

    @patch('apps.crm.pipedrive.get_person', side_effect=NotFound('not found on Pipedrive'))
    def test_link_missing_person_saves_nothing(self, mock_get):
        response = self.client.put(
            f'{self.base}/contacts/{self.contact.id}/pipedrive/person',
            {'person_id': 999}, content_type='application/json', headers=self.auth,
        )
        self.assertEqual(response.status_code, 404)
        self.contact.refresh_from_db()
        self.assertIsNone(self.contact.pipedrive_person_id)

    @patch('apps.crm.pipedrive.create_person')
    def test_create_person_uses_wa_id_as_phone(self, mock_create):
        mock_create.return_value = {'id': 51, 'name': 'Cliente A', 'phones': [{'value': '5511999999999'}]}
        response = self.client.post(
            f'{self.base}/contacts/{self.contact.id}/pipedrive/person',
            {'name': 'Cliente A'}, content_type='application/json', headers=self.member_auth,
        )
        self.assertEqual(response.status_code, 201)
        mock_create.assert_called_once_with('pd-key', 'Cliente A', '5511999999999')
        self.contact.refresh_from_db()
        self.assertEqual(self.contact.pipedrive_person_id, 51)

    def test_contact_of_other_company_blocked(self):
        response = self.client.get(f'{self.base}/contacts/{self.contact.id}/pipedrive/person', headers=self.outsider_auth)
        self.assertEqual(response.status_code, 404)


class PipedriveDealTests(BaseCrmTestCase):
    def test_list_deals_unlinked_returns_empty(self):
        response = self.client.get(f'{self.base}/contacts/{self.contact.id}/pipedrive/deals', headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    @patch('apps.crm.pipedrive.list_open_deals')
    def test_list_deals_linked(self, mock_list):
        self.contact.pipedrive_person_id = 77
        self.contact.save()
        mock_list.return_value = [{'id': 3, 'title': 'Negócio', 'value': 100.0, 'status': 'open'}]
        response = self.client.get(f'{self.base}/contacts/{self.contact.id}/pipedrive/deals', headers=self.member_auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]['title'], 'Negócio')
        mock_list.assert_called_once_with('pd-key', 77)

    @patch('apps.crm.pipedrive.create_deal')
    def test_create_deal_uses_defaults(self, mock_create):
        self.contact.pipedrive_person_id = 77
        self.contact.save()
        mock_create.return_value = {'id': 9, 'title': 'Novo'}
        response = self.client.post(
            f'{self.base}/contacts/{self.contact.id}/pipedrive/deals',
            {'title': 'Novo'}, content_type='application/json', headers=self.member_auth,
        )
        self.assertEqual(response.status_code, 201)
        mock_create.assert_called_once_with('pd-key', 'Novo', 77, 1, 2, None)

    @patch('apps.crm.pipedrive.create_deal')
    def test_create_deal_explicit_pipeline_overrides_defaults(self, mock_create):
        self.contact.pipedrive_person_id = 77
        self.contact.save()
        mock_create.return_value = {'id': 9, 'title': 'Novo'}
        response = self.client.post(
            f'{self.base}/contacts/{self.contact.id}/pipedrive/deals',
            {'title': 'Novo', 'pipeline_id': 5, 'stage_id': 6, 'value': 250.0},
            content_type='application/json', headers=self.auth,
        )
        self.assertEqual(response.status_code, 201)
        mock_create.assert_called_once_with('pd-key', 'Novo', 77, 5, 6, 250.0)

    def test_create_deal_without_defaults_fails(self):
        self.pd.default_pipeline_id = None
        self.pd.default_stage_id = None
        self.pd.save()
        response = self.client.post(
            f'{self.base}/contacts/{self.contact.id}/pipedrive/deals',
            {'title': 'Novo'}, content_type='application/json', headers=self.auth,
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('pipeline', response.json()['detail'])

    @patch('apps.crm.pipedrive.create_deal')
    @patch('apps.crm.pipedrive.search_persons', return_value=[{'id': 88, 'name': 'Ana'}])
    def test_create_deal_finds_person_when_unlinked(self, mock_search, mock_create):
        mock_create.return_value = {'id': 9, 'title': 'Novo'}
        response = self.client.post(
            f'{self.base}/contacts/{self.contact.id}/pipedrive/deals',
            {'title': 'Novo'}, content_type='application/json', headers=self.auth,
        )
        self.assertEqual(response.status_code, 201)
        self.contact.refresh_from_db()
        self.assertEqual(self.contact.pipedrive_person_id, 88)

    @patch('apps.crm.pipedrive.update_deal')
    def test_update_deal_status(self, mock_update):
        mock_update.return_value = {'id': 3, 'title': 'N', 'status': 'won'}
        response = self.client.patch(
            f'{self.base}/pipedrive/deals/3', {'status': 'won'}, content_type='application/json', headers=self.member_auth,
        )
        self.assertEqual(response.status_code, 200)
        mock_update.assert_called_once_with('pd-key', 3, stage_id=None, status='won')

    def test_update_deal_invalid_status_rejected(self):
        response = self.client.patch(
            f'{self.base}/pipedrive/deals/3', {'status': 'ganhou'}, content_type='application/json', headers=self.auth,
        )
        self.assertEqual(response.status_code, 422)


class PipedriveNoteActivityTests(BaseCrmTestCase):
    @patch('apps.crm.pipedrive.list_notes')
    def test_list_notes(self, mock_list):
        mock_list.return_value = [{'id': 1, 'content': 'nota', 'add_time': '2026-07-17 10:00:00'}]
        response = self.client.get(f'{self.base}/pipedrive/deals/3/notes', headers=self.member_auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]['content'], 'nota')

    @patch('apps.crm.pipedrive.create_note')
    def test_create_note(self, mock_create):
        mock_create.return_value = {'id': 2, 'content': 'nova nota'}
        response = self.client.post(
            f'{self.base}/pipedrive/deals/3/notes', {'content': 'nova nota'}, content_type='application/json', headers=self.member_auth,
        )
        self.assertEqual(response.status_code, 201)
        mock_create.assert_called_once_with('pd-key', 'nova nota', deal_id=3)

    @patch('apps.crm.pipedrive.list_open_activities')
    def test_list_activities(self, mock_list):
        mock_list.return_value = [{'id': 4, 'subject': 'Ligar', 'type': 'call', 'due_date': '2026-07-20', 'done': False}]
        response = self.client.get(f'{self.base}/pipedrive/deals/3/activities', headers=self.member_auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]['subject'], 'Ligar')
        mock_list.assert_called_once_with('pd-key', 3)

    @patch('apps.crm.pipedrive.mark_activity_done')
    def test_mark_activity_done(self, mock_done):
        mock_done.return_value = {'id': 4, 'subject': 'Ligar', 'done': True}
        response = self.client.post(f'{self.base}/pipedrive/activities/4/done', headers=self.member_auth)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['done'])
        mock_done.assert_called_once_with('pd-key', 4)

    def test_not_connected_returns_404(self):
        self.pd.delete()
        response = self.client.get(f'{self.base}/pipedrive/deals/3/notes', headers=self.auth)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['detail'], 'pipedrive not connected')


class PipedriveLeadAccountTests(BaseCrmTestCase):
    def test_patch_sets_lead_account(self):
        response = self.client.patch(
            f'{self.base}/pipedrive',
            {'pipeline_id': 1, 'stage_id': 2, 'lead_account_id': self.account.id},
            content_type='application/json', headers=self.auth,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['lead_account_id'], self.account.id)
        self.pd.refresh_from_db()
        self.assertEqual(self.pd.lead_account, self.account)

    def test_patch_foreign_lead_account_rejected(self):
        response = self.client.patch(
            f'{self.base}/pipedrive', {'lead_account_id': 999999},
            content_type='application/json', headers=self.auth,
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('lead account', response.json()['detail'])

    def test_out_dto_reports_receiving_events(self):
        self.pd.webhook_ids = [1, 2]
        self.pd.save(update_fields=['webhook_ids'])
        response = self.client.get(f'{self.base}/pipedrive', headers=self.member_auth)
        self.assertTrue(response.json()['receiving_events'])


class PipedriveWebhookRegistrationTests(BaseCrmTestCase):
    @patch('apps.crm.pipedrive.register_webhook', side_effect=[111, 222])
    @patch('apps.crm.pipedrive.validate')
    def test_connect_registers_two_webhooks_and_sets_lead_account(self, mock_validate, mock_register):
        self.pd.delete()  # first connect: lead_account auto = único número da company
        response = self.client.post(
            f'{self.base}/pipedrive', {'api_key': 'nova'}, content_type='application/json', headers=self.auth
        )
        self.assertEqual(response.status_code, 201)
        pd = PipedriveAccount.objects.get(company=self.company)
        self.assertEqual(pd.webhook_ids, [111, 222])
        self.assertEqual(pd.lead_account, self.account)
        self.assertEqual([c.args[2] for c in mock_register.call_args_list], ['create', 'change'])
        self.assertEqual([c.args[3] for c in mock_register.call_args_list], ['deal', 'deal'])

    @patch('apps.crm.pipedrive.validate')
    @patch('apps.crm.pipedrive.register_webhook', side_effect=PipedriveError('boom'))
    def test_connect_survives_webhook_failure(self, mock_register, mock_validate):
        self.pd.delete()
        response = self.client.post(
            f'{self.base}/pipedrive', {'api_key': 'nova'}, content_type='application/json', headers=self.auth
        )
        self.assertEqual(response.status_code, 201)  # best-effort: registro do webhook falhou, connect não
        self.assertEqual(PipedriveAccount.objects.get(company=self.company).webhook_ids, [])

    @patch('apps.crm.pipedrive.delete_webhook')
    def test_disconnect_deletes_webhooks(self, mock_delete):
        self.pd.webhook_ids = [10, 20]
        self.pd.save(update_fields=['webhook_ids'])
        response = self.client.delete(f'{self.base}/pipedrive', headers=self.auth)
        self.assertEqual(response.status_code, 204)
        self.assertEqual([c.args for c in mock_delete.call_args_list], [('pd-key', 10), ('pd-key', 20)])


class PipedriveWebhookIngestTests(BaseCrmTestCase):
    """Pipedrive -> Chatly: webhook v2 sincroniza contato sem duplicar (dedup por pipedrive_person_id + telefone)."""

    def setUp(self):
        super().setUp()
        self.pd.lead_account = self.account
        self.pd.save(update_fields=['lead_account'])
        self.webhook_url = f'/api/companies/pipedrive/webhook/{self.pd.webhook_token}'

    def _post(self, action='create', data=None, previous=None, url=None):
        body = {'meta': {'action': action, 'entity': 'deal'}, 'data': data or {}, 'previous': previous}
        return self.client.post(url or self.webhook_url, body, content_type='application/json')

    @patch('apps.crm.pipedrive.get_person')
    def test_deal_added_creates_linked_contact(self, mock_get_person):
        # phones: escolhe o primary, ignora o não-primary (exercita _first_phone)
        mock_get_person.return_value = {
            'id': 500, 'name': 'Novo Lead',
            'phones': [{'value': 'lixo', 'primary': False}, {'value': '+55 11 88888-8888', 'primary': True}],
        }
        before = Contact.objects.count()
        response = self._post(data={'id': 90, 'person_id': 500})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Contact.objects.count(), before + 1)
        contact = Contact.objects.get(account=self.account, wa_id='5511888888888')
        self.assertEqual(contact.pipedrive_person_id, 500)
        self.assertEqual(contact.name, 'Novo Lead')
        mock_get_person.assert_called_once_with('pd-key', 500)

    @patch('apps.crm.pipedrive.get_person')
    def test_existing_phone_links_without_duplicate(self, mock_get_person):
        mock_get_person.return_value = {'id': 501, 'name': 'Cliente A', 'phones': [{'value': '5511999999999', 'primary': True}]}
        before = Contact.objects.count()
        response = self._post(data={'id': 91, 'person_id': 501})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Contact.objects.count(), before)  # linkou o existente, não criou novo
        self.contact.refresh_from_db()
        self.assertEqual(self.contact.pipedrive_person_id, 501)

    @patch('apps.crm.pipedrive.get_person')
    def test_phone_without_ninth_digit_links_existing(self, mock_get_person):
        # Pipedrive com o celular antigo (sem o 9) casa com o contato de 13 dígitos
        mock_get_person.return_value = {'id': 504, 'name': 'Cliente A', 'phones': [{'value': '(11) 9999-9999', 'primary': True}]}
        before = Contact.objects.count()
        response = self._post(data={'id': 94, 'person_id': 504})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Contact.objects.count(), before)
        self.contact.refresh_from_db()
        self.assertEqual(self.contact.pipedrive_person_id, 504)

    @patch('apps.crm.pipedrive.get_person')
    def test_already_linked_person_is_noop(self, mock_get_person):
        self.contact.pipedrive_person_id = 502  # eco do próprio create do Chatly
        self.contact.save(update_fields=['pipedrive_person_id'])
        before = Contact.objects.count()
        response = self._post(data={'id': 92, 'person_id': 502})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Contact.objects.count(), before)
        mock_get_person.assert_not_called()  # dedup passo 1: nem busca telefone

    @patch('apps.crm.pipedrive.get_person')
    def test_deal_change_also_syncs(self, mock_get_person):
        mock_get_person.return_value = {'id': 503, 'name': 'Muda', 'phones': [{'value': '5511777777777', 'primary': True}]}
        response = self._post(action='change', data={'id': 93, 'person_id': 503}, previous={'stage_id': 1})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Contact.objects.filter(account=self.account, wa_id='5511777777777', pipedrive_person_id=503).exists())

    @patch('apps.crm.pipedrive.get_person')
    def test_invalid_phone_no_contact(self, mock_get_person):
        mock_get_person.return_value = {'id': 504, 'name': 'Sem Fone', 'phones': []}
        before = Contact.objects.count()
        response = self._post(data={'id': 94, 'person_id': 504})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Contact.objects.count(), before)

    @patch('apps.crm.pipedrive.get_deal')
    def test_deal_without_person_no_contact(self, mock_get_deal):
        mock_get_deal.return_value = {'id': 95, 'person_id': None}
        before = Contact.objects.count()
        response = self._post(data={'id': 95})  # sem person_id -> fallback get_deal
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Contact.objects.count(), before)
        mock_get_deal.assert_called_once_with('pd-key', 95)

    def test_unknown_token_returns_404(self):
        response = self._post(data={'id': 96, 'person_id': 1}, url='/api/companies/pipedrive/webhook/nao-existe')
        self.assertEqual(response.status_code, 404)

    def test_malformed_payload_returns_200(self):
        response = self.client.post(self.webhook_url, {}, content_type='application/json')  # sem meta/data
        self.assertEqual(response.status_code, 200)

    @patch('apps.crm.pipedrive.get_person')
    def test_idempotent_repeated_event(self, mock_get_person):
        mock_get_person.return_value = {'id': 505, 'name': 'Dup', 'phones': [{'value': '5511666666666', 'primary': True}]}
        before = Contact.objects.count()
        self._post(data={'id': 97, 'person_id': 505})
        self._post(data={'id': 97, 'person_id': 505})
        self.assertEqual(Contact.objects.count(), before + 1)

    # ---- seam: webhook dispara fluxo de automação ----

    def _pd_flow(self, cfg):
        graph = {
            'nodes': [
                {'id': 'start', 'type': 'start', 'position': {'x': 0, 'y': 0}, 'data': {}},
                {'id': 't', 'type': 'template', 'position': {'x': 0, 'y': 0}, 'data': {'template_name': 'oi', 'language': 'pt_BR'}},
                {'id': 'e', 'type': 'end', 'position': {'x': 0, 'y': 0}, 'data': {}},
            ],
            'edges': [
                {'id': 'e0', 'source': 'start', 'target': 't', 'sourceHandle': None},
                {'id': 'e1', 'source': 't', 'target': 'e', 'sourceHandle': None},
            ],
        }
        return Flow.objects.create(
            company=self.company, account=self.account, name='PD', trigger='pipedrive', trigger_config=cfg, graph=graph, active=True
        )

    @patch('apps.chats.services.send_template_message')
    @patch('apps.crm.pipedrive.get_person')
    def test_stage_change_fires_flow(self, mock_get_person, mock_tpl):
        self._pd_flow({'event': 'stage_changed', 'stage_id': 20})
        mock_get_person.return_value = {'id': 600, 'name': 'Lead', 'phones': [{'value': '5511555550000', 'primary': True}]}
        body = {
            'meta': {'action': 'change', 'entity': 'deal', 'change_source': 'app'},
            'data': {'id': 70, 'person_id': 600, 'stage_id': 20, 'pipeline_id': 7},
            'previous': {'stage_id': 19},
        }
        response = self.client.post(self.webhook_url, body, content_type='application/json')
        self.assertEqual(response.status_code, 200)
        contact = Contact.objects.get(account=self.account, pipedrive_person_id=600)
        self.assertEqual(FlowRun.objects.filter(contact=contact).count(), 1)
        mock_tpl.assert_called_once()

    @patch('apps.chats.services.send_template_message')
    @patch('apps.crm.pipedrive.get_person')
    def test_api_source_does_not_fire_flow(self, mock_get_person, mock_tpl):
        self._pd_flow({'event': 'stage_changed', 'stage_id': 20})
        mock_get_person.return_value = {'id': 601, 'name': 'Lead', 'phones': [{'value': '5511555551111', 'primary': True}]}
        pipedrive.mark_own_change(71)
        body = {
            'meta': {'action': 'change', 'entity': 'deal', 'change_source': 'api'},  # eco do próprio Chatly
            'data': {'id': 71, 'person_id': 601, 'stage_id': 20, 'pipeline_id': 7},
            'previous': {'stage_id': 19},
        }
        response = self.client.post(self.webhook_url, body, content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(FlowRun.objects.count(), 0)
        mock_tpl.assert_not_called()


@patch('apps.crm.services.send_template_message')
class PipedriveAutoOpenTests(BaseCrmTestCase):
    """Negócio entra numa etapa configurada: template de abertura uma vez por negócio, só no horário da IA."""

    TEMPLATE = {'template_name': 'boas_vindas', 'language': 'pt_BR', 'body_params': ['{{nome}}'], 'header_media_id': ''}

    def setUp(self):
        super().setUp()
        self.contact.pipedrive_person_id = 600
        self.contact.save(update_fields=['pipedrive_person_id'])
        PipedriveAccount.objects.filter(pk=self.pd.pk).update(
            lead_account=self.account, auto_stage_ids=[7, 8], auto_template=self.TEMPLATE, auto_only_in_hours=True,
        )
        self.url = f'/api/companies/pipedrive/webhook/{self.pd.webhook_token}'

    def _post(self, action='create', stage=7, deal_id=90, previous=None, source='app', status='open'):
        body = {
            'meta': {'action': action, 'entity': 'deal', 'change_source': source},
            'data': {'id': deal_id, 'person_id': 600, 'stage_id': stage, 'status': status},
            'previous': previous,
        }
        return self.client.post(self.url, body, content_type='application/json')

    def test_deal_created_in_stage_sends_once_per_deal(self, mock_send):
        self._post()
        mock_send.assert_called_once_with(self.company, self.contact.pk, 'boas_vindas', 'pt_BR', ['{{nome}}'], '')
        # foi pra outra etapa configurada: mesmo negócio, não manda de novo
        self._post(action='change', stage=8, previous={'stage_id': 7})
        self.assertEqual(mock_send.call_count, 1)
        # negócio novo da mesma pessoa: manda
        self._post(deal_id=91)
        self.assertEqual(mock_send.call_count, 2)

    def test_moved_into_stage_sends(self, mock_send):
        self._post(stage=1)
        self._post(action='change', stage=7, previous={'stage_id': 1})
        mock_send.assert_called_once()

    def test_ignored_events(self, mock_send):
        self._post(stage=1)                                             # etapa fora da lista
        self._post(action='change', previous={'title': 'x'})            # mudou outra coisa
        self._post(status='won')                                        # já fechado
        pipedrive.mark_own_change(92)
        self._post(source='api', deal_id=92)                            # o próprio chatly mexeu
        mock_send.assert_not_called()

    def test_deal_from_other_integration_sends(self, mock_send):
        # formulário do site cria o negócio pela API: change_source 'api', mas não foi o chatly
        self._post(source='api', deal_id=93)
        mock_send.assert_called_once()

    def test_outside_ai_hours_is_left_out(self, mock_send):
        with patch('apps.crm.services.next_ai_opening', return_value='amanhã'):
            self._post()
        mock_send.assert_not_called()
        # não ficou marcado: entrar de novo numa etapa no horário manda
        self._post(action='change', stage=8, previous={'stage_id': 7})
        mock_send.assert_called_once()

    def test_only_in_hours_off_ignores_schedule(self, mock_send):
        PipedriveAccount.objects.filter(pk=self.pd.pk).update(auto_only_in_hours=False)
        with patch('apps.crm.services.next_ai_opening', return_value='amanhã'):
            self._post()
        mock_send.assert_called_once()

    def test_failed_send_can_retry(self, mock_send):
        mock_send.side_effect = ValidationError('Cliente A está sem valor para {{cpf}}')
        response = self._post()
        self.assertEqual(response.status_code, 200)  # Pipedrive não precisa saber
        mock_send.side_effect = None
        self._post(action='change', stage=8, previous={'stage_id': 7})
        self.assertEqual(mock_send.call_count, 2)

    @patch('apps.crm.services.get_template_send_info', return_value=('Oi {{1}}', 'NONE'))
    def test_save_config(self, mock_info, mock_send):
        url = f'{self.base}/pipedrive/auto-open'
        body = {'stage_ids': [3, 3, 4], 'template': self.TEMPLATE, 'only_in_hours': False}
        response = self.client.put(url, body, content_type='application/json', headers=self.member_auth)
        self.assertEqual(response.status_code, 404)  # só o dono (membro não enxerga)

        response = self.client.put(url, body, content_type='application/json', headers=self.auth)
        self.assertEqual(response.status_code, 200, response.json())
        self.assertEqual(response.json()['auto_stage_ids'], [3, 4])
        self.assertFalse(response.json()['auto_only_in_hours'])
        mock_info.assert_called_once_with(self.account, 'boas_vindas', 'pt_BR')

        body = {'stage_ids': [3], 'template': {}, 'only_in_hours': True}
        response = self.client.put(url, body, content_type='application/json', headers=self.auth)
        self.assertEqual(response.status_code, 400)  # etapa sem template

        response = self.client.put(url, {'stage_ids': []}, content_type='application/json', headers=self.auth)
        self.assertEqual(response.status_code, 200)  # desligar não precisa de template


    DEAL_KEY = 'a' * 40
    PERSON_KEY = 'b' * 40

    def _map_fields(self):
        from apps.chats.models import ContactField

        ContactField.objects.create(company=self.company, key='conteudo', label='Conteúdo')
        ContactField.objects.create(company=self.company, key='cargo', label='Cargo')
        PipedriveAccount.objects.filter(pk=self.pd.pk).update(field_map=[
            {'source': 'deal', 'key': self.DEAL_KEY, 'name': 'Identificador', 'options': {}, 'contact_field': 'conteudo'},
            {'source': 'person', 'key': self.PERSON_KEY, 'name': 'Cargo', 'options': {'3': 'Projetista'}, 'contact_field': 'cargo'},
        ])

    @patch('apps.crm.pipedrive.get_person', return_value={'custom_fields': {'b' * 40: 3}})
    @patch('apps.crm.pipedrive.get_deal')
    def test_rule_picks_template_by_mapped_field(self, mock_deal, mock_person, mock_send):
        self._map_fields()
        demo = {'template_name': 'demo_sinc', 'language': 'pt_BR', 'body_params': [], 'header_media_id': ''}
        PipedriveAccount.objects.filter(pk=self.pd.pk).update(auto_rules=[
            {'field': 'conteudo', 'values': ['pagina-de-captura-seabra-assista-demo', 'software-demonstracao-sinc'], 'template': demo},
        ])
        mock_deal.return_value = {'custom_fields': {self.DEAL_KEY: 'Pagina-de-captura-seabra-assista-demo '}}
        self._post()  # webhook sem custom_fields: busca o negócio
        mock_send.assert_called_once_with(self.company, self.contact.pk, 'demo_sinc', 'pt_BR', [], '')
        self.contact.refresh_from_db()
        self.assertEqual(self.contact.custom_fields['cargo'], 'Projetista')  # opção da pessoa vira o nome

        # identificador sem regra: template padrão
        mock_deal.return_value = {'custom_fields': {self.DEAL_KEY: 'blocos-cad'}}
        self._post(deal_id=91)
        self.assertEqual(mock_send.call_args.args[2], 'boas_vindas')
        self.contact.refresh_from_db()
        self.assertEqual(self.contact.custom_fields['conteudo'], 'blocos-cad')

    @patch('apps.crm.pipedrive.list_fields')
    def test_save_field_map(self, mock_fields, mock_send):
        from apps.chats.models import ContactField

        ContactField.objects.create(company=self.company, key='conteudo', label='Conteúdo')
        mock_fields.side_effect = lambda key, source: [
            {'key': 'title', 'name': 'Título'},  # nativo: fica de fora
            {'key': {'deal': 'd', 'person': 'e'}[source] * 40, 'name': f'Campo {source}', 'options': [{'id': 1, 'label': 'Um'}]},
        ]
        url = f'{self.base}/pipedrive/fields'
        response = self.client.get(url, headers=self.auth)
        self.assertEqual([(f['source'], f['name']) for f in response.json()], [('deal', 'Campo deal'), ('person', 'Campo person')])

        url = f'{self.base}/pipedrive/field-map'
        item = {'source': 'deal', 'key': 'd' * 40, 'contact_field': 'conteudo'}
        response = self.client.put(url, [item], content_type='application/json', headers=self.auth)
        self.assertEqual(response.status_code, 200, response.json())
        self.assertEqual(response.json()['field_map'][0]['options'], {'1': 'Um'})
        response = self.client.put(url, [{**item, 'contact_field': 'nao_existe'}], content_type='application/json', headers=self.auth)
        self.assertEqual(response.status_code, 400)
        response = self.client.put(url, [{**item, 'key': 'x' * 40}], content_type='application/json', headers=self.auth)
        self.assertEqual(response.status_code, 400)

    @patch('apps.crm.services.get_template_send_info', return_value=('Oi', 'NONE'))
    def test_save_rules_without_default(self, mock_info, mock_send):
        url = f'{self.base}/pipedrive/auto-open'
        rule = {'field': 'conteudo', 'values': [' blocos-cad ', ''], 'template': self.TEMPLATE}
        response = self.client.put(url, {'stage_ids': [3], 'rules': [rule]}, content_type='application/json', headers=self.auth)
        self.assertEqual(response.status_code, 200, response.json())
        self.assertEqual(response.json()['auto_rules'][0]['values'], ['blocos-cad'])
        response = self.client.put(url, {'stage_ids': [3], 'rules': [{**rule, 'values': []}]}, content_type='application/json', headers=self.auth)
        self.assertEqual(response.status_code, 400)
