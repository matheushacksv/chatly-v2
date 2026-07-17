from unittest.mock import MagicMock, patch

import httpx
from django.db import connection
from django.test import TestCase

from apps.accounts.models import User
from apps.agents.models import Agent, Credential
from apps.chats.models import Contact, WhatsAppAccount
from apps.companies.models import Company, Membership
from apps.crm import pipedrive
from apps.crm.models import PipedriveAccount
from apps.testing import auth_header
from core.exceptions import NotFound, PipedriveError


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
