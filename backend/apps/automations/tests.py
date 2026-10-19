import json
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.agents.models import Agent, Credential
from apps.automations import services
from apps.automations.models import Flow, FlowRun, LeadSource
from apps.chats.models import Contact, ContactField, Message, WhatsAppAccount
from apps.companies.models import Company, Membership
from apps.testing import auth_header
from core.exceptions import ValidationError


def _graph(nodes: list[tuple], edges: list[tuple]) -> dict:
    """nodes=[(id, type, data)], edges=[(source, target, sourceHandle)]."""
    return {
        'nodes': [{'id': i, 'type': t, 'position': {'x': 0, 'y': 0}, 'data': d} for i, t, d in nodes],
        'edges': [{'id': f'e{n}', 'source': s, 'target': tg, 'sourceHandle': h} for n, (s, tg, h) in enumerate(edges)],
    }


class BaseAutomationsTestCase(TestCase):
    """Owner + membro + outsider, uma conta WhatsApp no tenant principal."""

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
        # conta de outra empresa, pra provar que não pode ser referenciada
        other_cred = Credential.objects.create(company=cls.other_company, provider='anthropic', api_key='sk-y')
        other_agent = Agent.objects.create(company=cls.other_company, name='W', prompt='.', credential=other_cred)
        cls.other_account = WhatsAppAccount.objects.create(
            company=cls.other_company, agent=other_agent, phone_number_id='222', waba_id='w2',
            access_token='t', app_secret='s', verify_token='v',
        )

    def setUp(self):
        self.auth = auth_header(self.owner)
        self.member_auth = auth_header(self.member)
        self.outsider_auth = auth_header(self.outsider)
        self.base = f'/api/automations/{self.company.id}'

    def make_flow(self, **kwargs) -> Flow:
        defaults = dict(
            company=self.company, account=self.account, name='Fluxo', trigger=Flow.Trigger.LEAD,
            trigger_config={}, graph={'nodes': [], 'edges': []},
        )
        defaults.update(kwargs)
        return Flow.objects.create(**defaults)

    def make_source(self, **kwargs) -> LeadSource:
        defaults = dict(company=self.company, account=self.account, name='Site')
        defaults.update(kwargs)
        return LeadSource.objects.create(**defaults)


class FlowCrudTests(BaseAutomationsTestCase):
    def test_create_flow(self):
        graph = {'nodes': [{'id': 'n1', 'type': 'ai', 'position': {'x': 0, 'y': 0}, 'data': {}}], 'edges': []}
        response = self.client.post(
            f'{self.base}/flows',
            {'name': 'Boas-vindas', 'account_id': self.account.id, 'trigger': 'lead', 'trigger_config': {}, 'graph': graph},
            content_type='application/json', headers=self.auth,
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body['name'], 'Boas-vindas')
        # o account serializa como id (regressão do bug account: int no ModelSchema)
        self.assertEqual(body['account'], self.account.id)
        self.assertEqual(body['run_count'], 0)
        self.assertFalse(body['active'])
        self.assertEqual(body['graph'], graph)

    def test_create_flow_rejects_invalid_trigger(self):
        response = self.client.post(
            f'{self.base}/flows',
            {'name': 'X', 'account_id': self.account.id, 'trigger': 'nope'},
            content_type='application/json', headers=self.auth,
        )
        self.assertEqual(response.status_code, 400)

    def test_create_flow_rejects_account_from_other_company(self):
        response = self.client.post(
            f'{self.base}/flows',
            {'name': 'X', 'account_id': self.other_account.id, 'trigger': 'lead'},
            content_type='application/json', headers=self.auth,
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(Flow.objects.filter(name='X').exists())

    def test_list_flows_only_from_company(self):
        self.make_flow(name='Meu')
        Flow.objects.create(
            company=self.other_company, account=self.other_account, name='Alheio', trigger=Flow.Trigger.LEAD,
        )
        response = self.client.get(f'{self.base}/flows', headers=self.auth)
        self.assertEqual(response.status_code, 200)
        names = [f['name'] for f in response.json()]
        self.assertEqual(names, ['Meu'])

    def test_list_flows_run_count(self):
        flow = self.make_flow()
        contact = Contact.objects.create(account=self.account, wa_id='5511988887777')
        FlowRun.objects.create(flow=flow, contact=contact)
        response = self.client.get(f'{self.base}/flows', headers=self.auth)
        self.assertEqual(response.json()[0]['run_count'], 1)

    def test_get_flow(self):
        flow = self.make_flow(name='Detalhe')
        response = self.client.get(f'{self.base}/flows/{flow.id}', headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['name'], 'Detalhe')

    def test_get_flow_other_company_is_404(self):
        flow = Flow.objects.create(company=self.other_company, account=self.other_account, name='X', trigger='lead')
        response = self.client.get(f'{self.base}/flows/{flow.id}', headers=self.auth)
        self.assertEqual(response.status_code, 404)

    def test_update_flow_saves_graph(self):
        flow = self.make_flow()
        graph = {
            'nodes': [{'id': 'n1', 'type': 'template', 'position': {'x': 10, 'y': 20}, 'data': {'template_name': 'oi'}}],
            'edges': [],
        }
        response = self.client.put(
            f'{self.base}/flows/{flow.id}',
            {'name': 'Renomeado', 'account_id': self.account.id, 'trigger': 'lead', 'trigger_config': {}, 'graph': graph},
            content_type='application/json', headers=self.auth,
        )
        self.assertEqual(response.status_code, 200)
        flow.refresh_from_db()
        self.assertEqual(flow.name, 'Renomeado')
        self.assertEqual(flow.graph, graph)

    def test_delete_flow(self):
        flow = self.make_flow()
        response = self.client.delete(f'{self.base}/flows/{flow.id}', headers=self.auth)
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Flow.objects.filter(pk=flow.id).exists())

    def test_activate_flow(self):
        # graph válido (início -> ai) e trigger sem exigência de template: isola o toggle da validação
        graph = {
            'nodes': [{'id': 'start', 'type': 'start', 'position': {'x': 0, 'y': 0}, 'data': {}},
                      {'id': 'a', 'type': 'ai', 'position': {'x': 0, 'y': 0}, 'data': {}}],
            'edges': [{'id': 'e0', 'source': 'start', 'target': 'a', 'sourceHandle': None}],
        }
        flow = self.make_flow(trigger=Flow.Trigger.FIRST_MESSAGE, graph=graph)
        response = self.client.post(
            f'{self.base}/flows/{flow.id}/activate', {'active': True},
            content_type='application/json', headers=self.auth,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['active'])
        flow.refresh_from_db()
        self.assertTrue(flow.active)

    def test_deactivate_flow(self):
        flow = self.make_flow(active=True)
        response = self.client.post(
            f'{self.base}/flows/{flow.id}/activate', {'active': False},
            content_type='application/json', headers=self.auth,
        )
        self.assertEqual(response.status_code, 200)
        flow.refresh_from_db()
        self.assertFalse(flow.active)


class FlowRunListTests(BaseAutomationsTestCase):
    def test_list_runs_paginated_with_contact_and_steps(self):
        flow = self.make_flow()
        contact = Contact.objects.create(account=self.account, wa_id='5511900001111', name='Fulano')
        FlowRun.objects.create(
            flow=flow, contact=contact, status=FlowRun.Status.WAITING, node_id='n3',
            steps=[{'node': 'n1', 'type': 'template', 'at': '2026-07-24T10:00:00Z', 'result': 'ok'}],
        )
        response = self.client.get(f'{self.base}/flows/{flow.id}/runs', headers=self.auth)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['count'], 1)
        run = body['items'][0]
        self.assertEqual(run['contact_id'], contact.id)
        self.assertEqual(run['contact_name'], 'Fulano')
        self.assertEqual(run['status'], 'waiting')
        self.assertEqual(run['node_id'], 'n3')
        self.assertEqual(len(run['steps']), 1)

    def test_contact_name_falls_back_to_wa_id(self):
        flow = self.make_flow()
        contact = Contact.objects.create(account=self.account, wa_id='5511922223333', name='')
        FlowRun.objects.create(flow=flow, contact=contact)
        response = self.client.get(f'{self.base}/flows/{flow.id}/runs', headers=self.auth)
        self.assertEqual(response.json()['items'][0]['contact_name'], '5511922223333')

    def test_list_runs_other_company_is_404(self):
        flow = Flow.objects.create(company=self.other_company, account=self.other_account, name='X', trigger='lead')
        response = self.client.get(f'{self.base}/flows/{flow.id}/runs', headers=self.auth)
        self.assertEqual(response.status_code, 404)


class LeadSourceCrudTests(BaseAutomationsTestCase):
    def test_create_source_generates_token(self):
        response = self.client.post(
            f'{self.base}/lead-sources', {'name': 'Formulário', 'account_id': self.account.id},
            content_type='application/json', headers=self.auth,
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body['name'], 'Formulário')
        self.assertEqual(body['account'], self.account.id)
        self.assertTrue(body['active'])
        self.assertTrue(body['token'])

    def test_create_source_tokens_are_unique(self):
        a = self.make_source()
        b = self.make_source(name='Outra')
        self.assertNotEqual(a.token, b.token)

    def test_create_source_rejects_account_from_other_company(self):
        response = self.client.post(
            f'{self.base}/lead-sources', {'name': 'X', 'account_id': self.other_account.id},
            content_type='application/json', headers=self.auth,
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(LeadSource.objects.filter(name='X').exists())

    def test_list_sources_only_from_company(self):
        self.make_source(name='Minha')
        LeadSource.objects.create(company=self.other_company, account=self.other_account, name='Alheia')
        response = self.client.get(f'{self.base}/lead-sources', headers=self.auth)
        names = [s['name'] for s in response.json()]
        self.assertEqual(names, ['Minha'])

    def test_delete_source(self):
        source = self.make_source()
        response = self.client.delete(f'{self.base}/lead-sources/{source.id}', headers=self.auth)
        self.assertEqual(response.status_code, 204)
        self.assertFalse(LeadSource.objects.filter(pk=source.id).exists())

    def test_delete_source_other_company_is_404(self):
        source = LeadSource.objects.create(company=self.other_company, account=self.other_account, name='X')
        response = self.client.delete(f'{self.base}/lead-sources/{source.id}', headers=self.auth)
        self.assertEqual(response.status_code, 404)
        self.assertTrue(LeadSource.objects.filter(pk=source.id).exists())


class AutomationsAuthTests(BaseAutomationsTestCase):
    """Módulo é config de dono: membro e outsider não entram."""

    def test_member_cannot_list_flows(self):
        response = self.client.get(f'{self.base}/flows', headers=self.member_auth)
        self.assertEqual(response.status_code, 404)

    def test_outsider_cannot_list_flows(self):
        response = self.client.get(f'{self.base}/flows', headers=self.outsider_auth)
        self.assertEqual(response.status_code, 404)

    def test_member_cannot_create_source(self):
        response = self.client.post(
            f'{self.base}/lead-sources', {'name': 'X', 'account_id': self.account.id},
            content_type='application/json', headers=self.member_auth,
        )
        self.assertEqual(response.status_code, 404)

    def test_unauthenticated_is_401(self):
        response = self.client.get(f'{self.base}/flows')
        self.assertEqual(response.status_code, 401)


class ActiveRunConstraintTests(BaseAutomationsTestCase):
    def test_only_one_active_run_per_contact(self):
        from django.db import IntegrityError, transaction

        flow = self.make_flow()
        contact = Contact.objects.create(account=self.account, wa_id='5511911112222')
        FlowRun.objects.create(flow=flow, contact=contact, status=FlowRun.Status.WAITING)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                FlowRun.objects.create(flow=flow, contact=contact, status=FlowRun.Status.RUNNING)

    def test_finished_run_does_not_block_new_one(self):
        flow = self.make_flow()
        contact = Contact.objects.create(account=self.account, wa_id='5511933334444')
        FlowRun.objects.create(flow=flow, contact=contact, status=FlowRun.Status.DONE)
        # run encerrado não conta pro constraint parcial: novo run entra normal
        FlowRun.objects.create(flow=flow, contact=contact, status=FlowRun.Status.RUNNING)
        self.assertEqual(FlowRun.objects.filter(flow=flow, contact=contact).count(), 2)


# * ------ Runtime (Fatia A: start/template/message/ai/handoff) ------

# grafos reusados
_LINEAR = _graph(
    [('start', 'start', {}), ('t', 'template', {'template_name': 'boas', 'language': 'pt_BR'}),
     ('m', 'message', {'text': 'Oi {{nome}}'}), ('a', 'ai', {})],
    [('start', 't', None), ('t', 'm', None), ('m', 'a', None)],
)
_LEAD_FLOW = _graph(
    [('start', 'start', {}), ('t', 'template', {'template_name': 'boas', 'language': 'pt_BR'}), ('a', 'ai', {})],
    [('start', 't', None), ('t', 'a', None)],
)
_AI_ONLY = _graph([('start', 'start', {}), ('a', 'ai', {})], [('start', 'a', None)])


@patch('apps.chats.events.publish')
@patch('apps.chats.whatsapp.send_text', return_value='wamid-1')
@patch('apps.chats.services.send_template_message')
class RuntimeAdvanceTests(BaseAutomationsTestCase):
    def _contact(self, name='Fulano'):
        return Contact.objects.create(account=self.account, wa_id='5511999998888', name=name)

    def test_advance_linear(self, mock_template, mock_send_text, mock_publish):
        flow = self.make_flow(graph=_LINEAR, active=True)
        contact = self._contact()
        run = services.start_flow(flow, contact)
        run.refresh_from_db()
        self.assertEqual(run.status, FlowRun.Status.DONE)
        self.assertEqual([s['type'] for s in run.steps], ['start', 'template', 'message', 'ai'])
        mock_template.assert_called_once()
        # {{nome}} resolvido pelo nome do contato
        mock_send_text.assert_called_once_with(self.account, '5511999998888', 'Oi Fulano')

    def test_message_falls_back_to_wa_id_when_no_name(self, mock_template, mock_send_text, mock_publish):
        flow = self.make_flow(graph=_graph([('start', 'start', {}), ('m', 'message', {'text': 'Oi {{nome}}'})],
                                           [('start', 'm', None)]), active=True)
        run = services.start_flow(flow, self._contact(name=''))
        mock_send_text.assert_called_once_with(self.account, '5511999998888', 'Oi 5511999998888')
        run.refresh_from_db()
        self.assertEqual(run.status, FlowRun.Status.DONE)  # saída solta = fim

    def test_handoff_pauses_ai_and_publishes(self, mock_template, mock_send_text, mock_publish):
        flow = self.make_flow(graph=_graph([('start', 'start', {}), ('h', 'handoff', {})], [('start', 'h', None)]), active=True)
        contact = self._contact()
        run = services.start_flow(flow, contact)
        run.refresh_from_db()
        contact.refresh_from_db()
        self.assertEqual(run.status, FlowRun.Status.DONE)
        self.assertTrue(contact.ai_paused)
        self.assertIsNotNone(contact.ai_resume_at)  # prazo do agente (24h padrão)
        types = [c.args[1]['type'] for c in mock_publish.call_args_list]
        self.assertEqual(types.count('handoff'), 1)

    def test_pause_pauses_ai_and_continues(self, mock_template, mock_send_text, mock_publish):
        # "Pausar IA" é ação: pausa e o fluxo segue pro próximo bloco, sem avisar a equipe
        flow = self.make_flow(
            graph=_graph([('start', 'start', {}), ('p', 'pause', {}), ('m', 'message', {'text': 'Oi'})],
                         [('start', 'p', None), ('p', 'm', None)]),
            active=True,
        )
        contact = self._contact()
        run = services.start_flow(flow, contact)
        run.refresh_from_db()
        contact.refresh_from_db()
        self.assertEqual(run.status, FlowRun.Status.DONE)
        self.assertEqual([s['type'] for s in run.steps], ['start', 'pause', 'message'])
        self.assertTrue(contact.ai_paused)      # pausou
        mock_send_text.assert_called_once()     # e seguiu pro message
        # silencioso: nenhum evento de handoff (o nó message publica um 'message', esse é esperado)
        published = [c.args[1].get('type') for c in mock_publish.call_args_list]
        self.assertNotIn('handoff', published)

    def test_pause_at_end_leaves_ai_paused(self, mock_template, mock_send_text, mock_publish):
        # pause como último bloco (sem saída): fluxo acaba ali, IA fica pausada
        flow = self.make_flow(
            graph=_graph([('start', 'start', {}), ('m', 'message', {'text': 'Oi'}), ('p', 'pause', {})],
                         [('start', 'm', None), ('m', 'p', None)]),
            active=True,
        )
        contact = self._contact()
        run = services.start_flow(flow, contact)
        run.refresh_from_db()
        contact.refresh_from_db()
        self.assertEqual(run.status, FlowRun.Status.DONE)
        self.assertEqual(run.steps[-1]['type'], 'pause')
        self.assertTrue(contact.ai_paused)

    def test_ai_node_unpauses_after_pause(self, mock_template, mock_send_text, mock_publish):
        # "Entregar pra IA" despausa: pausar antes e entregar pra IA depois deixa a IA ativa
        flow = self.make_flow(
            graph=_graph([('start', 'start', {}), ('p', 'pause', {}), ('a', 'ai', {})],
                         [('start', 'p', None), ('p', 'a', None)]),
            active=True,
        )
        contact = self._contact()
        run = services.start_flow(flow, contact)
        run.refresh_from_db()
        contact.refresh_from_db()
        self.assertEqual(run.status, FlowRun.Status.DONE)
        self.assertFalse(contact.ai_paused)     # ai despausou o que o pause pausou

    def test_cycle_guard_marks_failed(self, mock_template, mock_send_text, mock_publish):
        # loop start -> x -> x (x = tipo sem efeito, cai no return '' default): estoura MAX_STEPS
        flow = self.make_flow(graph=_graph([('start', 'start', {}), ('x', 'noop', {})],
                                           [('start', 'x', None), ('x', 'x', None)]), active=True)
        run = services.start_flow(flow, self._contact())
        run.refresh_from_db()
        self.assertEqual(run.status, FlowRun.Status.FAILED)
        self.assertGreaterEqual(len(run.steps), services.MAX_STEPS)

    def test_start_flow_is_idempotent_on_active_run(self, mock_template, mock_send_text, mock_publish):
        flow = self.make_flow(graph=_AI_ONLY, active=True)
        contact = self._contact()
        existing = FlowRun.objects.create(flow=flow, contact=contact, status=FlowRun.Status.WAITING, node_id='x')
        run = services.start_flow(flow, contact)
        self.assertEqual(run.pk, existing.pk)  # devolveu o ativo, não criou outro
        self.assertEqual(FlowRun.objects.filter(flow=flow, contact=contact).count(), 1)


@patch('apps.chats.services.send_template_message')
class CreateLeadTests(BaseAutomationsTestCase):
    def test_normalizes_number_and_starts_flow(self, mock_template):
        self.make_flow(graph=_LEAD_FLOW, active=True, trigger=Flow.Trigger.LEAD)
        source = self.make_source()
        contact, started, _ = services.create_lead(source, {'telefone': '55 11 99999-8888', 'nome': 'Ana'})
        self.assertTrue(started)
        self.assertEqual(contact.wa_id, '5511999998888')
        self.assertEqual(contact.name, 'Ana')
        self.assertTrue(FlowRun.objects.filter(contact=contact).exists())
        mock_template.assert_called_once()

    def test_lead_data_goes_to_ai_memory(self, mock_template):
        source = self.make_source(name='Site')
        payload = {'telefone': '5511999998888', 'origem': 'google', 'email': 'ana@x.com', 'extras': {'produto': 'piscina', 'vazio': ''}}
        contact, _, _ = services.create_lead(source, payload)
        contact.refresh_from_db()
        self.assertIn('Chegou como lead pela fonte "Site"', contact.memory)
        self.assertIn('origem: google, email: ana@x.com, produto: piscina', contact.memory)
        self.assertNotIn('vazio', contact.memory)

        # lead repetido acrescenta, não apaga o que a IA já anotou
        Contact.objects.filter(pk=contact.pk).update(memory='Cliente prefere à tarde')
        services.create_lead(source, {'telefone': '5511999998888', 'extras': {'texto': 'x' * 2000}})
        contact.refresh_from_db()
        self.assertTrue(contact.memory.startswith('Cliente prefere à tarde\n'))
        self.assertLess(len(contact.memory), 600)

    def test_no_active_lead_flow_creates_contact_only(self, mock_template):
        source = self.make_source()
        contact, started, _ = services.create_lead(source, {'telefone': '5511999998888'})
        self.assertFalse(started)
        self.assertFalse(FlowRun.objects.filter(contact=contact).exists())

    def test_campos_fill_known_custom_fields_only(self, mock_template):
        ContactField.objects.create(company=self.company, key='plano', label='Plano')
        source = self.make_source()
        contact, _, ignored = services.create_lead(source, {'telefone': '5511999998888', 'campos': {'plano': ' Ouro ', 'cpf': '123', 'vazio': ''}})
        contact.refresh_from_db()
        self.assertEqual(contact.custom_fields, {'plano': 'Ouro'})
        self.assertEqual(ignored, ['cpf', 'vazio'])

    def test_invalid_number_raises(self, mock_template):
        source = self.make_source()
        with self.assertRaises(ValidationError):
            services.create_lead(source, {'telefone': '123'})


class DispatchIncomingTests(BaseAutomationsTestCase):
    def _msg(self, contact, content='oi', mid='w-in-1'):
        return Message.objects.create(contact=contact, direction='in', content=content, wa_message_id=mid)

    def test_active_run_silences_ai(self):
        flow = self.make_flow()
        contact = Contact.objects.create(account=self.account, wa_id='5511999998888')
        FlowRun.objects.create(flow=flow, contact=contact, status=FlowRun.Status.RUNNING)
        self.assertTrue(services.dispatch_incoming(self._msg(contact)))
        self.assertEqual(FlowRun.objects.filter(contact=contact).count(), 1)  # não abriu novo run

    def test_first_message_trigger_starts_flow(self):
        self.make_flow(graph=_AI_ONLY, active=True, trigger=Flow.Trigger.FIRST_MESSAGE)
        contact = Contact.objects.create(account=self.account, wa_id='5511999998888')
        self.assertTrue(services.dispatch_incoming(self._msg(contact, content='olá')))
        run = FlowRun.objects.get(contact=contact)
        self.assertEqual(run.status, FlowRun.Status.DONE)  # start -> ai encerra

    def test_reply_to_our_template_is_not_first_message(self):
        # lead recebeu o template do fluxo de lead: a resposta dele vai pra IA, não pro fluxo de primeira mensagem
        self.make_flow(graph=_AI_ONLY, active=True, trigger=Flow.Trigger.FIRST_MESSAGE)
        contact = Contact.objects.create(account=self.account, wa_id='5511999998888')
        Message.objects.create(contact=contact, direction='out', content='Oi, recebemos seu contato', wa_message_id='w-out-1')
        self.assertFalse(services.dispatch_incoming(self._msg(contact, content='oi')))
        self.assertFalse(FlowRun.objects.filter(contact=contact).exists())

    def test_keyword_trigger_starts_flow(self):
        self.make_flow(graph=_AI_ONLY, active=True, trigger=Flow.Trigger.KEYWORD, trigger_config={'keyword': 'orçamento'})
        contact = Contact.objects.create(account=self.account, wa_id='5511999998888')
        self.assertTrue(services.dispatch_incoming(self._msg(contact, content='quero um orçamento')))
        self.assertTrue(FlowRun.objects.filter(contact=contact).exists())

    def test_no_trigger_returns_false(self):
        contact = Contact.objects.create(account=self.account, wa_id='5511999998888')
        self.assertFalse(services.dispatch_incoming(self._msg(contact, content='mensagem qualquer')))
        self.assertFalse(FlowRun.objects.filter(contact=contact).exists())


class ValidateGraphTests(BaseAutomationsTestCase):
    def test_lead_without_leading_template_rejected(self):
        flow = self.make_flow(graph=_graph([('start', 'start', {}), ('m', 'message', {'text': 'oi'})], [('start', 'm', None)]),
                              trigger=Flow.Trigger.LEAD)
        with self.assertRaises(ValidationError):
            services.validate_graph(flow)

    def test_lead_with_leading_template_ok(self):
        flow = self.make_flow(graph=_LEAD_FLOW, trigger=Flow.Trigger.LEAD)
        services.validate_graph(flow)  # não levanta

    def test_lead_with_window_condition_needs_template_only_on_nao(self):
        def flow_with(nao_type):
            nodes = [('start', 'start', {}), ('c', 'condition', {'op': 'window_open'}), ('a', 'ai', {}),
                     ('n', nao_type, {'text': 'oi', 'template_name': 'boas_vindas'}), ('a2', 'ai', {})]
            edges = [('start', 'c', None), ('c', 'a', 'sim'), ('c', 'n', 'nao'), ('n', 'a2', None)]
            return self.make_flow(graph=_graph(nodes, edges), trigger=Flow.Trigger.LEAD)

        services.validate_graph(flow_with('template'))  # não levanta
        with self.assertRaises(ValidationError):
            services.validate_graph(flow_with('message'))  # sem janela, texto livre não sai

        # condição comum na frente continua barrada
        flow = self.make_flow(graph=_cond_graph('contains', 'x'), trigger=Flow.Trigger.LEAD)
        with self.assertRaises(ValidationError):
            services.validate_graph(flow)

    def test_activate_lead_without_template_returns_400(self):
        flow = self.make_flow(graph=_graph([('start', 'start', {}), ('m', 'message', {'text': 'oi'})], [('start', 'm', None)]),
                              trigger=Flow.Trigger.LEAD)
        response = self.client.post(
            f'{self.base}/flows/{flow.id}/activate', {'active': True},
            content_type='application/json', headers=self.auth,
        )
        self.assertEqual(response.status_code, 400)
        flow.refresh_from_db()
        self.assertFalse(flow.active)


@patch('apps.chats.services.send_template_message')
class LeadWebhookTests(BaseAutomationsTestCase):
    def _url(self, token):
        return f'/api/automations/leads/{token}'

    def test_bad_token_is_404(self, mock_template):
        response = self.client.post(self._url('inexistente'), {'telefone': '5511999998888'}, content_type='application/json')
        self.assertEqual(response.status_code, 404)

    def test_invalid_number_is_400(self, mock_template):
        source = self.make_source()
        response = self.client.post(self._url(source.token), {'telefone': '123'}, content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_inactive_source_is_404(self, mock_template):
        source = self.make_source(active=False)
        response = self.client.post(self._url(source.token), {'telefone': '5511999998888'}, content_type='application/json')
        self.assertEqual(response.status_code, 404)

    def test_happy_path_starts_flow(self, mock_template):
        self.make_flow(graph=_LEAD_FLOW, active=True, trigger=Flow.Trigger.LEAD)
        source = self.make_source()
        response = self.client.post(
            self._url(source.token), {'telefone': '55 11 99999-8888', 'nome': 'Ana'}, content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body['flow_started'])
        contact = Contact.objects.get(wa_id='5511999998888')
        self.assertEqual(body['contact_id'], contact.id)
        mock_template.assert_called_once()


# * ------ Runtime (Fatia B: buttons + roteamento) ------

_BUTTONS = _graph(
    [('start', 'start', {}),
     ('q', 'buttons', {'text': 'Escolha', 'buttons': [{'id': 'b1', 'title': 'Sim'}, {'id': 'b2', 'title': 'Não'}]}),
     ('h', 'handoff', {}), ('a', 'ai', {})],
    [('start', 'q', None), ('q', 'h', 'b1'), ('q', 'a', 'b2')],
)


@patch('apps.chats.events.publish')
@patch('apps.chats.whatsapp.send_interactive', return_value='wamid-btn')
class ButtonsRuntimeTests(BaseAutomationsTestCase):
    def _start(self, graph=_BUTTONS, name='Fulano'):
        flow = self.make_flow(graph=graph, active=True, trigger=Flow.Trigger.FIRST_MESSAGE)
        contact = Contact.objects.create(account=self.account, wa_id='5511999998888', name=name)
        run = services.start_flow(flow, contact)
        run.refresh_from_db()
        return contact, run

    def _reply(self, contact, button_id, mid='w-btn-1'):
        return Message.objects.create(contact=contact, direction='in', content='x', button_id=button_id, wa_message_id=mid)

    def test_buttons_node_waits(self, mock_send, mock_pub):
        contact, run = self._start()
        self.assertEqual(run.status, FlowRun.Status.WAITING)
        self.assertEqual(run.node_id, 'q')  # parou no nó buttons
        self.assertEqual([s['type'] for s in run.steps], ['start', 'buttons'])
        mock_send.assert_called_once_with(
            self.account, '5511999998888', 'Escolha', [{'id': 'b1', 'title': 'Sim'}, {'id': 'b2', 'title': 'Não'}]
        )

    def test_routes_matching_button(self, mock_send, mock_pub):
        contact, run = self._start()
        self.assertTrue(services.dispatch_incoming(self._reply(contact, 'b1')))
        run.refresh_from_db()
        contact.refresh_from_db()
        self.assertEqual(run.status, FlowRun.Status.DONE)
        self.assertTrue(contact.ai_paused)  # b1 -> handoff
        self.assertEqual(run.steps[-1]['type'], 'handoff')

    def test_other_button_other_branch(self, mock_send, mock_pub):
        contact, run = self._start()
        self.assertTrue(services.dispatch_incoming(self._reply(contact, 'b2')))
        run.refresh_from_db()
        contact.refresh_from_db()
        self.assertEqual(run.status, FlowRun.Status.DONE)
        self.assertFalse(contact.ai_paused)  # b2 -> ai
        self.assertEqual(run.steps[-1]['type'], 'ai')

    def test_same_title_routes_by_id(self, mock_send, mock_pub):
        # dois botões de MESMO título, saídas diferentes: casa por id, não por texto
        graph = _graph(
            [('start', 'start', {}),
             ('q', 'buttons', {'text': '?', 'buttons': [{'id': 'b1', 'title': 'Ok'}, {'id': 'b2', 'title': 'Ok'}]}),
             ('h', 'handoff', {}), ('a', 'ai', {})],
            [('start', 'q', None), ('q', 'h', 'b1'), ('q', 'a', 'b2')],
        )
        contact, run = self._start(graph=graph)
        self.assertTrue(services.dispatch_incoming(self._reply(contact, 'b2')))
        contact.refresh_from_db()
        self.assertFalse(contact.ai_paused)  # b2 -> ai, não o handoff do b1

    def test_unmatched_button_keeps_waiting(self, mock_send, mock_pub):
        contact, run = self._start()
        self.assertTrue(services.dispatch_incoming(self._reply(contact, 'bX')))  # id que não é aresta
        run.refresh_from_db()
        self.assertEqual(run.status, FlowRun.Status.WAITING)
        self.assertEqual(run.node_id, 'q')

    def test_free_text_while_waiting_keeps_waiting(self, mock_send, mock_pub):
        contact, run = self._start()
        msg = Message.objects.create(contact=contact, direction='in', content='texto livre', button_id='', wa_message_id='w-free')
        self.assertTrue(services.dispatch_incoming(msg))  # sem button_id: IA calada, segue esperando
        run.refresh_from_db()
        self.assertEqual(run.status, FlowRun.Status.WAITING)


@patch('apps.chats.events.publish')
@patch('apps.chats.whatsapp.send_interactive', return_value='wamid-btn')
@patch('apps.chats.services.send_template_message')
class LeadIdempotencyTests(BaseAutomationsTestCase):
    def test_second_lead_does_not_duplicate_run(self, mock_tpl, mock_send, mock_pub):
        # flow que termina em buttons segura o run ativo, então o 2º lead reusa a mesma conversa
        graph = _graph(
            [('start', 'start', {}), ('t', 'template', {'template_name': 'boas', 'language': 'pt_BR'}),
             ('q', 'buttons', {'text': '?', 'buttons': [{'id': 'b1', 'title': 'Sim'}]})],
            [('start', 't', None), ('t', 'q', None)],
        )
        self.make_flow(graph=graph, active=True, trigger=Flow.Trigger.LEAD)
        source = self.make_source()
        c1, s1, _ = services.create_lead(source, {'telefone': '5511999998888'})
        c2, s2, _ = services.create_lead(source, {'telefone': '5511999998888'})
        self.assertTrue(s1)
        self.assertTrue(s2)
        self.assertEqual(c1.pk, c2.pk)
        self.assertEqual(FlowRun.objects.filter(contact=c1).count(), 1)  # run ativo (WAITING) não duplica


# * ------ Runtime (Marco 2: condition/wait/delay/crm + validate_graph) ------


def _cond_graph(op, value, source='last_message'):
    return _graph(
        [('start', 'start', {}), ('c', 'condition', {'source': source, 'op': op, 'value': value}),
         ('a', 'ai', {}), ('h', 'handoff', {})],
        [('start', 'c', None), ('c', 'a', 'sim'), ('c', 'h', 'nao')],
    )


def _crm_graph(action, data=None):
    return _graph(
        [('start', 'start', {}), ('crm', 'crm', {'action': action, **(data or {})}), ('a', 'ai', {})],
        [('start', 'crm', None), ('crm', 'a', None)],
    )


@patch('apps.chats.events.publish')
class ConditionRuntimeTests(BaseAutomationsTestCase):
    def _route(self, op, value, last_message):
        flow = self.make_flow(graph=_cond_graph(op, value), active=True, trigger=Flow.Trigger.FIRST_MESSAGE)
        contact = Contact.objects.create(account=self.account, wa_id='5511999998888')
        run = services.start_flow(flow, contact, {'last_message': last_message})
        run.refresh_from_db()
        contact.refresh_from_db()
        return run, contact

    def test_contains_true_routes_sim(self, mock_pub):
        run, contact = self._route('contains', 'comprar', 'quero comprar agora')
        self.assertEqual(run.steps[-1]['type'], 'ai')  # sim -> ai
        self.assertFalse(contact.ai_paused)

    def test_window_open_routes_by_last_inbound(self, mock_pub):
        flow = self.make_flow(graph=_cond_graph('window_open', ''), active=True, trigger=Flow.Trigger.LEAD)
        contact = Contact.objects.create(account=self.account, wa_id='5511999998888')
        cases = [
            (None, 'handoff'),  # nunca escreveu: fechada
            (timedelta(hours=25), 'handoff'),
            (timedelta(hours=1), 'ai'),
        ]
        for age, expected in cases:
            Message.objects.filter(contact=contact).delete()
            FlowRun.objects.filter(contact=contact).delete()
            if age:
                msg = Message.objects.create(contact=contact, direction='in', content='oi')
                Message.objects.filter(pk=msg.pk).update(created_at=timezone.now() - age)
            run = services.start_flow(flow, contact)
            run.refresh_from_db()
            self.assertEqual(run.steps[-1]['type'], expected, age)

    def test_contains_false_routes_nao(self, mock_pub):
        run, contact = self._route('contains', 'comprar', 'só olhando')
        self.assertEqual(run.steps[-1]['type'], 'handoff')  # nao -> handoff
        self.assertTrue(contact.ai_paused)

    def test_equals_is_case_insensitive(self, mock_pub):
        run, _ = self._route('equals', 'sim', 'SIM')
        self.assertEqual(run.steps[-1]['type'], 'ai')

    def test_contact_field_tag_and_filled(self, mock_pub):
        from apps.chats.models import Tag

        contact = Contact.objects.create(account=self.account, wa_id='5511999997777', custom_fields={'cad': 'Civil 3D'})
        vip = Tag.objects.create(company=self.company, name='vip')
        cases = [
            (('equals', 'civil 3d', 'cad'), 'ai'),  # campo do contato
            (('filled', '', 'cad'), 'ai'),
            (('filled', '', 'plano'), 'handoff'),  # campo vazio
            (('has_tag', str(vip.pk), ''), 'handoff'),
        ]
        for (op, value, source), expected in cases:
            FlowRun.objects.filter(contact=contact).delete()
            Contact.objects.filter(pk=contact.pk).update(ai_paused=False)
            flow = self.make_flow(graph=_cond_graph(op, value, source), active=True, trigger=Flow.Trigger.FIRST_MESSAGE)
            run = services.start_flow(flow, contact)
            run.refresh_from_db()
            self.assertEqual(run.steps[-1]['type'], expected, (op, source))
            flow.delete()
        contact.tags.add(vip)
        FlowRun.objects.filter(contact=contact).delete()
        flow = self.make_flow(graph=_cond_graph('has_tag', str(vip.pk)), active=True, trigger=Flow.Trigger.FIRST_MESSAGE)
        run = services.start_flow(flow, contact)
        run.refresh_from_db()
        self.assertEqual(run.steps[-1]['type'], 'ai')

    def test_regex_match(self, mock_pub):
        run, _ = self._route('regex', r'\d{4}', 'meu cep 1234')
        self.assertEqual(run.steps[-1]['type'], 'ai')

    def test_regex_no_match(self, mock_pub):
        run, _ = self._route('regex', r'\d{4}', 'sem numero')
        self.assertEqual(run.steps[-1]['type'], 'handoff')

    def test_match_condition_unit(self, mock_pub):
        self.assertTrue(services._match_condition('Quero Comprar', 'contains', 'comprar'))
        self.assertTrue(services._match_condition('SIM', 'equals', 'sim'))
        self.assertFalse(services._match_condition('ola', 'equals', 'oi'))
        self.assertTrue(services._match_condition('cep 1234', 'regex', r'\d{4}'))
        self.assertFalse(services._match_condition('x', 'regex', '['))  # regex inválido não quebra


_WAIT = _graph(
    [('start', 'start', {}), ('w', 'wait', {'var': 'resposta'}), ('a', 'ai', {})],
    [('start', 'w', None), ('w', 'a', None)],
)
_WAIT_TIMEOUT = _graph(
    [('start', 'start', {}), ('w', 'wait', {'var': 'resposta', 'timeout_minutes': 30}), ('a', 'ai', {}), ('h', 'handoff', {})],
    [('start', 'w', None), ('w', 'a', None), ('w', 'h', 'timeout')],
)


@patch('apps.chats.events.publish')
@patch('apps.automations.tasks.resume_flow.apply_async')
class WaitRuntimeTests(BaseAutomationsTestCase):
    def _contact(self):
        return Contact.objects.create(account=self.account, wa_id='5511999998888')

    def test_pauses_then_reply_stores_var(self, mock_sched, mock_pub):
        flow = self.make_flow(graph=_WAIT, active=True, trigger=Flow.Trigger.FIRST_MESSAGE)
        contact = self._contact()
        run = services.start_flow(flow, contact)
        run.refresh_from_db()
        self.assertEqual(run.status, FlowRun.Status.WAITING)
        self.assertEqual(run.node_id, 'w')
        msg = Message.objects.create(contact=contact, direction='in', content='minha resposta', wa_message_id='w1')
        self.assertTrue(services.dispatch_incoming(msg))
        run.refresh_from_db()
        self.assertEqual(run.status, FlowRun.Status.DONE)
        self.assertEqual(run.context.get('resposta'), 'minha resposta')
        self.assertEqual(run.context.get('last_message'), 'minha resposta')

    def test_timeout_is_scheduled(self, mock_sched, mock_pub):
        flow = self.make_flow(graph=_WAIT_TIMEOUT, active=True, trigger=Flow.Trigger.FIRST_MESSAGE)
        services.start_flow(flow, self._contact())
        mock_sched.assert_called_once()  # agendou resume_flow com countdown

    def test_timeout_routes_via_resume(self, mock_sched, mock_pub):
        flow = self.make_flow(graph=_WAIT_TIMEOUT, active=True, trigger=Flow.Trigger.FIRST_MESSAGE)
        contact = self._contact()
        run = services.start_flow(flow, contact)
        run.refresh_from_db()
        self.assertEqual(run.status, FlowRun.Status.WAITING)
        services.resume(run.pk)  # o timer dispara
        run.refresh_from_db()
        contact.refresh_from_db()
        self.assertEqual(run.steps[-1]['type'], 'handoff')  # saída 'timeout'
        self.assertTrue(contact.ai_paused)


_DELAY = _graph(
    [('start', 'start', {}), ('d', 'delay', {'minutes': 5}), ('a', 'ai', {})],
    [('start', 'd', None), ('d', 'a', None)],
)


@patch('apps.automations.tasks.resume_flow.apply_async')
class DelayRuntimeTests(BaseAutomationsTestCase):
    def _contact(self):
        return Contact.objects.create(account=self.account, wa_id='5511999998888')

    def test_pauses_and_schedules(self, mock_sched):
        flow = self.make_flow(graph=_DELAY, active=True, trigger=Flow.Trigger.FIRST_MESSAGE)
        run = services.start_flow(flow, self._contact())
        run.refresh_from_db()
        self.assertEqual(run.status, FlowRun.Status.WAITING)
        self.assertEqual(run.node_id, 'd')
        mock_sched.assert_called_once()

    def test_resume_continues(self, mock_sched):
        flow = self.make_flow(graph=_DELAY, active=True, trigger=Flow.Trigger.FIRST_MESSAGE)
        run = services.start_flow(flow, self._contact())
        services.resume(run.pk)
        run.refresh_from_db()
        self.assertEqual(run.status, FlowRun.Status.DONE)
        self.assertEqual(run.steps[-1]['type'], 'ai')

    def test_resume_noop_when_not_waiting(self, mock_sched):
        flow = self.make_flow(graph=_DELAY, active=True, trigger=Flow.Trigger.FIRST_MESSAGE)
        run = FlowRun.objects.create(flow=flow, contact=self._contact(), node_id='d', status=FlowRun.Status.DONE)
        services.resume(run.pk)  # já encerrado: filter(status=WAITING) não acha, no-op
        run.refresh_from_db()
        self.assertEqual(run.status, FlowRun.Status.DONE)


@patch('apps.crm.pipedrive.update_deal')
@patch('apps.crm.pipedrive.create_note')
@patch('apps.crm.services.create_deal_for_contact', return_value={'id': 77})
@patch('apps.crm.services.find_or_create_person', return_value=42)
class CrmRuntimeTests(BaseAutomationsTestCase):
    def setUp(self):
        super().setUp()
        patcher = patch('apps.crm.pipedrive.list_open_deals', return_value=[])
        self.mock_open_deals = patcher.start()
        self.addCleanup(patcher.stop)
        from apps.crm.models import PipedriveAccount
        PipedriveAccount.objects.create(company=self.company, api_key='x', default_pipeline_id=1, default_stage_id=2)

    def _run(self, action, data=None, context=None):
        flow = self.make_flow(graph=_crm_graph(action, data), active=True, trigger=Flow.Trigger.FIRST_MESSAGE)
        contact = Contact.objects.create(account=self.account, wa_id='5511999998888', name='Ana')
        run = services.start_flow(flow, contact, context)
        run.refresh_from_db()
        return run, contact

    def test_person(self, mock_person, mock_deal, mock_note, mock_stage):
        self._run('person')
        mock_person.assert_called_once()

    def test_deal_stores_deal_id(self, mock_person, mock_deal, mock_note, mock_stage):
        run, _ = self._run('deal', {'title': 'Novo negócio'})
        mock_deal.assert_called_once()
        self.assertEqual(run.context.get('deal_id'), 77)

    def test_deal_title_renders_variables(self, mock_person, mock_deal, mock_note, mock_stage):
        self._run('deal', {'title': 'Lead {{nome}} - {{campanha}}'}, context={'campanha': 'Outubro'})
        self.assertEqual(mock_deal.call_args.args[2], 'Lead Ana - Outubro')

    def test_deal_reuses_open_deal(self, mock_person, mock_deal, mock_note, mock_stage):
        self.mock_open_deals.return_value = [{'id': 5, 'title': 'Aberto'}]
        run, _ = self._run('deal', {'title': 'Novo negócio'})
        mock_deal.assert_not_called()
        self.assertEqual(run.context.get('deal_id'), 5)  # bloco de etapa seguinte move o negócio existente

    def test_note(self, mock_person, mock_deal, mock_note, mock_stage):
        self._run('note', {'text': 'anotação de {{nome}}'})
        mock_person.assert_called_once()
        mock_note.assert_called_once()

    def test_stage_uses_deal_id_from_context(self, mock_person, mock_deal, mock_note, mock_stage):
        self._run('stage', {'stage_id': 5}, context={'deal_id': 77})
        mock_stage.assert_called_once()

    def test_no_pipedrive_is_noop(self, mock_person, mock_deal, mock_note, mock_stage):
        from apps.crm.models import PipedriveAccount
        PipedriveAccount.objects.all().delete()
        self._run('deal', {'title': 'X'})
        mock_deal.assert_not_called()


class ValidateGraphCompleteTests(BaseAutomationsTestCase):
    def test_no_start_rejected(self):
        flow = self.make_flow(graph=_graph([('a', 'ai', {})], []), trigger=Flow.Trigger.FIRST_MESSAGE)
        with self.assertRaises(ValidationError):
            services.validate_graph(flow)

    def test_start_without_edge_rejected(self):
        flow = self.make_flow(graph=_graph([('start', 'start', {}), ('a', 'ai', {})], []), trigger=Flow.Trigger.FIRST_MESSAGE)
        with self.assertRaises(ValidationError):
            services.validate_graph(flow)

    def test_node_without_output_rejected(self):
        # 'm' (message) tem entrada mas nenhuma saída ligada: não é terminal, recusa
        flow = self.make_flow(
            graph=_graph([('start', 'start', {}), ('m', 'message', {'text': 'oi'})], [('start', 'm', None)]),
            trigger=Flow.Trigger.FIRST_MESSAGE,
        )
        with self.assertRaises(ValidationError):
            services.validate_graph(flow)

    def test_valid_flow_passes(self):
        flow = self.make_flow(
            graph=_graph([('start', 'start', {}), ('a', 'ai', {})], [('start', 'a', None)]),
            trigger=Flow.Trigger.FIRST_MESSAGE,
        )
        services.validate_graph(flow)  # terminal (ai) não precisa de saída; não levanta


class AdvanceFailureTests(BaseAutomationsTestCase):
    """Envio/executor que falha não pode travar o contato: run vira FAILED, não fica preso RUNNING."""

    @patch('apps.chats.services.send_template_message', side_effect=ValidationError('whatsapp invalid send'))
    def test_executor_failure_marks_failed_with_error_step(self, mock_tpl):
        flow = self.make_flow(graph=_LEAD_FLOW, active=True, trigger=Flow.Trigger.LEAD)
        contact = Contact.objects.create(account=self.account, wa_id='5511999998888')
        run = services.start_flow(flow, contact)
        run.refresh_from_db()
        self.assertEqual(run.status, FlowRun.Status.FAILED)          # não ficou RUNNING
        self.assertEqual(run.steps[-1]['type'], 'error')             # motivo gravado na trilha
        self.assertIn('invalid send', run.steps[-1]['result'])

    @patch('apps.chats.services.send_template_message', side_effect=ValidationError('whatsapp invalid send'))
    def test_failed_run_does_not_block_retrigger(self, mock_tpl):
        # regressão do bug real: run preso barrava re-disparo por keyword
        flow = self.make_flow(graph=_LEAD_FLOW, active=True, trigger=Flow.Trigger.KEYWORD, trigger_config={'keyword': 'oi'})
        contact = Contact.objects.create(account=self.account, wa_id='5511999998888')
        self.assertTrue(services.dispatch_incoming(
            Message.objects.create(contact=contact, direction='in', content='oi', wa_message_id='m1')))
        self.assertTrue(services.dispatch_incoming(
            Message.objects.create(contact=contact, direction='in', content='oi', wa_message_id='m2')))
        # dois runs (ambos failed): o 1º FAILED não bloqueou o 2º disparo
        self.assertEqual(FlowRun.objects.filter(contact=contact).count(), 2)
        self.assertEqual(FlowRun.objects.filter(contact=contact, status=FlowRun.Status.FAILED).count(), 2)


# graph mínimo válido pro trigger Pipedrive: start -> template -> end (template precisa de saída)
_PD_FLOW = _graph(
    [('start', 'start', {}), ('t', 'template', {'template_name': 'oi', 'language': 'pt_BR'}), ('e', 'end', {})],
    [('start', 't', None), ('t', 'e', None)],
)


class PipedriveMatchTests(TestCase):
    def _deal(self, stage_id=20, pipeline_id=7):
        return {'id': 1, 'title': 'D', 'pipeline_id': pipeline_id, 'stage_id': stage_id}

    def test_deal_added_matches_target_stage(self):
        cfg = {'event': 'deal_added', 'stage_id': 20}
        self.assertTrue(services._pipedrive_match(cfg, 'create', self._deal(20), None))
        self.assertFalse(services._pipedrive_match(cfg, 'create', self._deal(99), None))

    def test_deal_added_ignores_change_action(self):
        cfg = {'event': 'deal_added', 'stage_id': 20}
        self.assertFalse(services._pipedrive_match(cfg, 'change', self._deal(20), {'stage_id': 19}))

    def test_deal_added_no_stage_matches_any_create(self):
        self.assertTrue(services._pipedrive_match({'event': 'deal_added'}, 'create', self._deal(5), None))

    def test_stage_changed_matches_entry_into_target(self):
        cfg = {'event': 'stage_changed', 'stage_id': 20}
        self.assertTrue(services._pipedrive_match(cfg, 'change', self._deal(20), {'stage_id': 19}))

    def test_stage_changed_rejects_when_not_entering_target(self):
        cfg = {'event': 'stage_changed', 'stage_id': 20}
        self.assertFalse(services._pipedrive_match(cfg, 'change', self._deal(19), {'stage_id': 18}))  # entrou na 19, não na 20
        self.assertFalse(services._pipedrive_match(cfg, 'change', self._deal(20), {'title': 'x'}))  # stage não mudou

    def test_stage_changed_no_stage_matches_any_move(self):
        self.assertTrue(services._pipedrive_match({'event': 'stage_changed'}, 'change', self._deal(20), {'stage_id': 19}))

    def test_pipeline_filter_narrows(self):
        cfg = {'event': 'deal_added', 'stage_id': 20, 'pipeline_id': 7}
        self.assertTrue(services._pipedrive_match(cfg, 'create', self._deal(20, 7), None))
        self.assertFalse(services._pipedrive_match(cfg, 'create', self._deal(20, 99), None))

    def test_unknown_event_is_false(self):
        self.assertFalse(services._pipedrive_match({'event': 'nope'}, 'create', self._deal(), None))


@patch('apps.chats.services.send_template_message')
class PipedriveDispatchTests(BaseAutomationsTestCase):
    def _contact(self, account=None, wa='5511999990000'):
        return Contact.objects.create(account=account or self.account, wa_id=wa)

    def _deal(self, stage_id=20):
        return {'id': 1, 'title': 'Deal', 'pipeline_id': 7, 'stage_id': stage_id}

    def test_dispatch_starts_matching_flow(self, mock_tpl):
        self.make_flow(trigger=Flow.Trigger.PIPEDRIVE, trigger_config={'event': 'stage_changed', 'stage_id': 20}, graph=_PD_FLOW, active=True)
        contact = self._contact()
        services.dispatch_pipedrive_event(contact, 'change', self._deal(20), {'stage_id': 19})
        self.assertEqual(FlowRun.objects.filter(contact=contact).count(), 1)
        mock_tpl.assert_called_once()

    def test_dispatch_once_per_contact_flow(self, mock_tpl):
        self.make_flow(trigger=Flow.Trigger.PIPEDRIVE, trigger_config={'event': 'stage_changed', 'stage_id': 20}, graph=_PD_FLOW, active=True)
        contact = self._contact()
        services.dispatch_pipedrive_event(contact, 'change', self._deal(20), {'stage_id': 19})
        services.dispatch_pipedrive_event(contact, 'change', self._deal(20), {'stage_id': 19})  # reentrada
        self.assertEqual(FlowRun.objects.filter(contact=contact).count(), 1)

    def test_dispatch_skips_non_matching_stage(self, mock_tpl):
        self.make_flow(trigger=Flow.Trigger.PIPEDRIVE, trigger_config={'event': 'stage_changed', 'stage_id': 20}, graph=_PD_FLOW, active=True)
        contact = self._contact()
        services.dispatch_pipedrive_event(contact, 'change', self._deal(99), {'stage_id': 19})
        self.assertEqual(FlowRun.objects.filter(contact=contact).count(), 0)

    def test_dispatch_ignores_other_account_flow(self, mock_tpl):
        self.make_flow(company=self.other_company, account=self.other_account, trigger=Flow.Trigger.PIPEDRIVE,
                       trigger_config={'event': 'deal_added'}, graph=_PD_FLOW, active=True)
        services.dispatch_pipedrive_event(self._contact(), 'create', self._deal(20), None)
        self.assertEqual(FlowRun.objects.count(), 0)

    def test_dispatch_ignores_inactive_flow(self, mock_tpl):
        self.make_flow(trigger=Flow.Trigger.PIPEDRIVE, trigger_config={'event': 'deal_added'}, graph=_PD_FLOW, active=False)
        services.dispatch_pipedrive_event(self._contact(), 'create', self._deal(20), None)
        self.assertEqual(FlowRun.objects.count(), 0)


class PipedriveValidateGraphTests(BaseAutomationsTestCase):
    def test_pipedrive_requires_template_first(self):
        flow = self.make_flow(
            trigger=Flow.Trigger.PIPEDRIVE,
            graph=_graph([('start', 'start', {}), ('m', 'message', {'text': 'oi'})], [('start', 'm', None)]),
        )
        with self.assertRaises(ValidationError):
            services.set_active(self.company, flow.id, True)

    def test_pipedrive_template_first_activates(self):
        flow = self.make_flow(trigger=Flow.Trigger.PIPEDRIVE, graph=_PD_FLOW)
        self.assertTrue(services.set_active(self.company, flow.id, True).active)


# * ------ campanha com fluxo no clique -----


def _click_graph() -> dict:
    # botão = Confirmar -> http (sucesso/falha) ; senão -> etiqueta
    return _graph(
        [
            ('start', 'start', {}),
            ('c1', 'condition', {'source': 'button', 'op': 'equals', 'value': 'Confirmar'}),
            ('h1', 'http', {'method': 'POST', 'url': 'https://exemplo.com/hook', 'headers': [{'key': 'Authorization', 'value': 'Bearer x'}],
                            'body': '{"nome": "{{nome}}", "botao": "{{botao}}", "campanha": "{{campanha}}"}'}),
            ('ok', 'end', {}),
            ('fail', 'handoff', {}),
            ('t1', 'tag', {'action': 'add', 'tag_id': None}),
        ],
        [('start', 'c1', ''), ('c1', 'h1', 'sim'), ('c1', 't1', 'nao'), ('h1', 'ok', 'sucesso'), ('h1', 'fail', 'falha')],
    )


@patch('apps.chats.events.publish')
@patch('apps.automations.outbound.request', return_value=200)
class CampaignClickTests(BaseAutomationsTestCase):
    def setUp(self):
        super().setUp()
        from apps.chats.models import Campaign, Tag

        self.tag = Tag.objects.create(company=self.company, name='bloqueado')
        graph = _click_graph()
        next(n for n in graph['nodes'] if n['id'] == 't1')['data']['tag_id'] = self.tag.pk
        self.flow = self.make_flow(trigger=Flow.Trigger.CAMPAIGN, graph=graph, active=True)
        self.campaign = Campaign.objects.create(
            account=self.account, name='Consultas', template_name='t', template_language='pt_BR', flow=self.flow
        )
        self.contact = Contact.objects.create(account=self.account, wa_id='5511999998888', name='Ana "Bia"')

    def click(self, text, wamid='wamid.click', campaign=True):
        return Message.objects.create(
            contact=self.contact, direction='in', content=text, button_id=text, wa_message_id=wamid,
            campaign=self.campaign if campaign else None,
        )

    def test_click_starts_flow_with_button_context(self, mock_http, mock_pub):
        self.assertTrue(services.dispatch_incoming(self.click('Confirmar')))
        run = FlowRun.objects.get(flow=self.flow, contact=self.contact)
        self.assertEqual(run.context['button'], 'Confirmar')
        self.assertEqual(run.context['campanha'], 'Consultas')
        self.assertEqual(run.status, FlowRun.Status.DONE)
        self.assertEqual(run.steps[-1]['node'], 'ok')
        method, url, headers, body = mock_http.call_args.args
        self.assertEqual((method, url), ('POST', 'https://exemplo.com/hook'))
        self.assertEqual(headers['Authorization'], 'Bearer x')
        self.assertEqual(headers['Content-Type'], 'application/json')
        # aspas no nome não quebram o JSON
        self.assertEqual(json.loads(body), {'nome': 'Ana "Bia"', 'botao': 'Confirmar', 'campanha': 'Consultas'})
        self.assertEqual(run.context['http_status'], 200)

    def test_http_error_status_goes_to_falha(self, mock_http, mock_pub):
        mock_http.return_value = 500
        services.dispatch_incoming(self.click('Confirmar'))
        self.assertEqual(FlowRun.objects.get(flow=self.flow).steps[-1]['node'], 'fail')

    def test_http_exception_goes_to_falha(self, mock_http, mock_pub):
        mock_http.side_effect = ValidationError('a URL aponta pra um endereço interno')
        services.dispatch_incoming(self.click('Confirmar'))
        run = FlowRun.objects.get(flow=self.flow)
        self.assertEqual(run.steps[-1]['node'], 'fail')
        self.assertEqual(run.context['http_status'], 0)

    def test_other_button_adds_tag(self, mock_http, mock_pub):
        services.dispatch_incoming(self.click('Bloquear'))
        self.assertTrue(self.contact.tags.filter(pk=self.tag.pk).exists())
        mock_http.assert_not_called()

    def test_tag_remove_action(self, mock_http, mock_pub):
        self.contact.tags.add(self.tag)
        node = next(n for n in self.flow.graph['nodes'] if n['id'] == 't1')
        node['data']['action'] = 'remove'
        self.flow.save()
        services.dispatch_incoming(self.click('Bloquear'))
        self.assertFalse(self.contact.tags.exists())

    def test_deleted_tag_does_not_fail_flow(self, mock_http, mock_pub):
        self.tag.delete()
        services.dispatch_incoming(self.click('Bloquear'))
        self.assertEqual(FlowRun.objects.get(flow=self.flow).status, FlowRun.Status.DONE)

    def test_same_button_twice_runs_once_other_button_runs(self, mock_http, mock_pub):
        services.dispatch_incoming(self.click('Confirmar', 'wamid.1'))
        self.assertTrue(services.dispatch_incoming(self.click('Confirmar', 'wamid.2')))
        self.assertEqual(FlowRun.objects.filter(flow=self.flow).count(), 1)
        services.dispatch_incoming(self.click('Bloquear', 'wamid.3'))
        self.assertEqual(FlowRun.objects.filter(flow=self.flow).count(), 2)

    def test_inactive_flow_lets_message_reach_ai(self, mock_http, mock_pub):
        self.flow.active = False
        self.flow.save()
        self.assertFalse(services.dispatch_incoming(self.click('Confirmar')))
        self.assertFalse(FlowRun.objects.exists())

    def test_campaign_without_flow_lets_message_reach_ai(self, mock_http, mock_pub):
        self.campaign.flow = None
        self.campaign.save()
        self.assertFalse(services.dispatch_incoming(self.click('Confirmar')))

    def test_click_cancels_other_active_run(self, mock_http, mock_pub):
        waiting = self.make_flow(
            trigger=Flow.Trigger.KEYWORD, active=True,
            graph=_graph([('start', 'start', {}), ('w', 'wait', {'var': 'r'}), ('e', 'end', {})], [('start', 'w', ''), ('w', 'e', '')]),
        )
        old = services.start_flow(waiting, self.contact)
        self.assertEqual(old.status, FlowRun.Status.WAITING)
        services.dispatch_incoming(self.click('Confirmar'))
        old.refresh_from_db()
        self.assertEqual(old.status, FlowRun.Status.DONE)
        self.assertEqual(old.steps[-1]['type'], 'cancel')
        self.assertTrue(FlowRun.objects.filter(flow=self.flow).exists())

    def test_validate_graph_campaign_without_template(self, mock_http, mock_pub):
        services.validate_graph(self.flow)  # não levanta: o clique abriu a janela de 24h


class RenderTests(BaseAutomationsTestCase):
    def test_render_vars(self):
        contact = Contact.objects.create(account=self.account, wa_id='5511999998888', name='')
        run = FlowRun(flow=self.make_flow(), contact=contact, context={'cidade': 'BH', 'deal_id': 7})
        self.assertEqual(
            services._render('{{nome}} {{telefone}} {{cidade}} {{deal_id}} {{ desconhecida }}', run),
            '5511999998888 5511999998888 BH 7 {{ desconhecida }}',
        )


class OutboundCheckTests(TestCase):
    def _addr(self, ip):
        return [(2, 1, 6, '', (ip, 443))]

    def test_rejects_non_https(self):
        from apps.automations import outbound

        with self.assertRaises(ValidationError):
            outbound.check_url('http://exemplo.com')

    def test_rejects_internal_addresses(self):
        from apps.automations import outbound

        for ip in ('127.0.0.1', '10.0.0.5', '172.16.3.4', '192.168.1.1', '169.254.169.254', '::1', '172.18.0.4'):
            with self.subTest(ip=ip), patch('apps.automations.outbound.socket.getaddrinfo', return_value=self._addr(ip)):
                with self.assertRaises(ValidationError):
                    outbound.check_url('https://minio:9000/x')

    def test_accepts_public_address(self):
        from apps.automations import outbound

        with patch('apps.automations.outbound.socket.getaddrinfo', return_value=self._addr('93.184.216.34')):
            outbound.check_url('https://exemplo.com/hook')

    def test_request_refuses_internal_before_calling(self):
        from apps.automations import outbound

        with patch('apps.automations.outbound.socket.getaddrinfo', return_value=self._addr('127.0.0.1')), \
                patch('apps.automations.outbound.httpx.request') as mock_req:
            with self.assertRaises(ValidationError):
                outbound.request('POST', 'https://localhost/x', {}, '')
            mock_req.assert_not_called()
