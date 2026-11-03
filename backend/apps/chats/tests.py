import hashlib
import hmac
import json
import tempfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import ANY, MagicMock, call, patch

from django.utils import timezone

import httpx
from agno.run.base import RunStatus
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase, override_settings

from apps import ai
from apps.chats import ai_tools, services, tasks, whatsapp

from django.core.files.base import ContentFile

from apps.accounts.models import User
from apps.agents.models import Agent, Credential, Document, DocumentChunk
from apps.chats.models import Campaign, Contact, ContactField, FollowUp, Message, ToolCall, Sticker, Tag, TemplateHeaderMedia, WhatsAppAccount
from apps.companies import plans
from apps.companies.models import Company, Membership
from apps.crm.models import PipedriveAccount
from core.exceptions import NotFound, PaymentRequired, PipedriveError, ValidationError
from apps.testing import auth_header

SP = ZoneInfo('America/Sao_Paulo')

# storage default é MinIO - em teste troca por filesystem local num tempdir,
# senão cada rodada sobe arquivo real pro bucket
LOCAL_STORAGE_SETTINGS = dict(
    MEDIA_ROOT=tempfile.mkdtemp(),
    MEDIA_URL='media/',
    STORAGES={
        'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    },
)


def close_service_window(contact):
    """Envelhece todas as inbound do contact além das 24h (created_at é auto_now_add, só via update)."""
    Message.objects.filter(contact=contact, direction='in').update(created_at=timezone.now() - timedelta(hours=25))


class BaseChatsTestCase(TestCase):
    """Fixture de dois tenants: user + company + credential + agent + account de cada lado."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(email='owner@a.com', password='strongpass123')
        cls.other_user = User.objects.create_user(email='other@a.com', password='strongpass123')
        cls.company = Company.objects.create(name='Minha Empresa', owner=cls.user)
        cls.other_company = Company.objects.create(name='Empresa Alheia', owner=cls.other_user)
        cls.credential = Credential.objects.create(
            company=cls.company, provider='anthropic', api_key='sk-minha-chave'
        )
        cls.other_credential = Credential.objects.create(
            company=cls.other_company, provider='anthropic', api_key='sk-chave-alheia'
        )
        cls.agent = Agent.objects.create(
            company=cls.company, name='Vendedor', prompt='Você vende.', credential=cls.credential,
            model='claude-opus-4-8',
        )
        cls.other_agent = Agent.objects.create(
            company=cls.other_company, name='Alheio', prompt='...', credential=cls.other_credential
        )
        cls.account = WhatsAppAccount.objects.create(
            company=cls.company,
            agent=cls.agent,
            phone_number_id='111111',
            waba_id='waba-1',
            access_token='EAAG-meu-token-secreto',
            app_secret='meu-app-secret',
            verify_token='verify-1',
        )
        cls.other_account = WhatsAppAccount.objects.create(
            company=cls.other_company,
            agent=cls.other_agent,
            phone_number_id='222222',
            waba_id='waba-2',
            access_token='EAAG-token-alheio',
            app_secret='app-secret-alheio',
            verify_token='verify-2',
        )
        cls.contact = Contact.objects.create(account=cls.account, wa_id='5511999999999', name='Cliente A')
        cls.other_contact = Contact.objects.create(account=cls.other_account, wa_id='5511888888888')
        # criação sequencial: created_at crescente garante ordem no order_by
        Message.objects.create(contact=cls.contact, direction='in', content='Oi', wa_message_id='wamid.1')
        Message.objects.create(contact=cls.contact, direction='out', content='Olá! Como posso ajudar?', status='sent')
        Message.objects.create(contact=cls.other_contact, direction='in', content='Mensagem alheia', wa_message_id='wamid.2')

    def setUp(self):
        self.auth = auth_header(self.user)
        # publish (SSE) mockado globalmente: testes não falam com Redis real
        patcher = patch('apps.chats.events.publish')
        self.mock_events = patcher.start()
        self.addCleanup(patcher.stop)


class WhatsAppAccountTests(BaseChatsTestCase):
    def url(self, company_id: int, account_id: int | None = None) -> str:
        base = f'/api/companies/{company_id}/whatsapp-accounts'
        return f'{base}/{account_id}' if account_id else base

    def account_payload(self, **overrides) -> dict:
        payload = {
            'agent_id': self.agent.pk,
            'phone_number_id': '333333',
            'waba_id': 'waba-3',
            'access_token': 'EAAG-token-novo',
            'app_secret': 'app-secret-novo',
            'verify_token': 'verify-3',
        }
        payload.update(overrides)
        return payload

    # create

    def test_create_account_with_app_id(self):
        response = self.client.post(
            self.url(self.company.pk),
            data=self.account_payload(app_id='app-123'),
            content_type='application/json',
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 201, response.json())
        self.assertEqual(response.json()['app_id'], 'app-123')
        account = WhatsAppAccount.objects.get(phone_number_id='333333')
        self.assertEqual(account.app_id, 'app-123')

    def test_update_account_app_id(self):
        response = self.client.patch(
            self.url(self.company.pk, self.account.pk),
            data={'app_id': 'app-novo'},
            content_type='application/json',
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 200, response.json())
        self.account.refresh_from_db()
        self.assertEqual(self.account.app_id, 'app-novo')

    def test_create_account(self):
        response = self.client.post(
            self.url(self.company.pk),
            data=self.account_payload(),
            content_type='application/json',
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 201, response.json())
        account = WhatsAppAccount.objects.get(phone_number_id='333333')
        self.assertEqual(account.company, self.company)
        self.assertEqual(account.agent, self.agent)

    def test_create_account_does_not_leak_secrets(self):
        response = self.client.post(
            self.url(self.company.pk),
            data=self.account_payload(),
            content_type='application/json',
            headers=self.auth,
        )
        body = response.json()
        self.assertNotIn('access_token', body)
        self.assertNotIn('app_secret', body)
        content = response.content.decode()
        self.assertNotIn('EAAG-token-novo', content)
        self.assertNotIn('app-secret-novo', content)

    def test_create_account_with_foreign_agent_returns_404(self):
        # cross-tenant: agent de outra company não pode ser anexado
        response = self.client.post(
            self.url(self.company.pk),
            data=self.account_payload(agent_id=self.other_agent.pk),
            content_type='application/json',
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(WhatsAppAccount.objects.filter(phone_number_id='333333').exists())

    def test_create_account_with_nonexistent_agent_returns_404(self):
        response = self.client.post(
            self.url(self.company.pk),
            data=self.account_payload(agent_id=99999),
            content_type='application/json',
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 404)

    def test_create_account_with_duplicate_phone_number_returns_409(self):
        # phone_number_id é unique GLOBAL - número da other_company também conflita
        response = self.client.post(
            self.url(self.company.pk),
            data=self.account_payload(phone_number_id=self.other_account.phone_number_id),
            content_type='application/json',
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 409)

    def test_create_account_in_foreign_company_returns_404(self):
        response = self.client.post(
            self.url(self.other_company.pk),
            data=self.account_payload(),
            content_type='application/json',
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(WhatsAppAccount.objects.filter(phone_number_id='333333').exists())

    # list / detail

    def test_list_accounts_only_mine_and_without_secrets(self):
        response = self.client.get(self.url(self.company.pk), headers=self.auth)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]['phone_number_id'], '111111')
        self.assertNotIn('access_token', body[0])
        self.assertNotIn('app_secret', body[0])

    def test_get_account(self):
        response = self.client.get(self.url(self.company.pk, self.account.pk), headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['phone_number_id'], '111111')

    def test_get_foreign_account_returns_404(self):
        response = self.client.get(self.url(self.company.pk, self.other_account.pk), headers=self.auth)
        self.assertEqual(response.status_code, 404)

    # update

    def test_update_account_partial_keeps_unsent_fields(self):
        # PATCH só com waba_id não pode apagar os demais (exclude_unset)
        response = self.client.patch(
            self.url(self.company.pk, self.account.pk),
            data={'waba_id': 'waba-novo'},
            content_type='application/json',
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 200, response.json())
        self.account.refresh_from_db()
        self.assertEqual(self.account.waba_id, 'waba-novo')
        self.assertEqual(self.account.access_token, 'EAAG-meu-token-secreto')
        self.assertEqual(self.account.agent, self.agent)

    def test_update_account_ignores_phone_number_id(self):
        # mass assignment: campo fora do PatchDTO, pydantic descarta - valor não muda
        response = self.client.patch(
            self.url(self.company.pk, self.account.pk),
            data={'phone_number_id': '999999', 'waba_id': 'waba-x'},
            content_type='application/json',
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 200)
        self.account.refresh_from_db()
        self.assertEqual(self.account.phone_number_id, '111111')

    def test_update_account_with_foreign_agent_returns_404(self):
        response = self.client.patch(
            self.url(self.company.pk, self.account.pk),
            data={'agent_id': self.other_agent.pk},
            content_type='application/json',
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 404)
        self.account.refresh_from_db()
        self.assertEqual(self.account.agent, self.agent)

    # delete

    def test_delete_account(self):
        response = self.client.delete(self.url(self.company.pk, self.account.pk), headers=self.auth)
        self.assertEqual(response.status_code, 204)
        self.assertFalse(WhatsAppAccount.objects.filter(pk=self.account.pk).exists())

    def test_delete_foreign_account_returns_404(self):
        response = self.client.delete(self.url(self.company.pk, self.other_account.pk), headers=self.auth)
        self.assertEqual(response.status_code, 404)
        self.assertTrue(WhatsAppAccount.objects.filter(pk=self.other_account.pk).exists())

    def test_delete_agent_in_use_by_account_returns_409(self):
        # agent é PROTECT de WhatsAppAccount - delete_agent precisa tratar ProtectedError
        response = self.client.delete(
            f'/api/companies/{self.company.pk}/agents/{self.agent.pk}', headers=self.auth
        )
        self.assertEqual(response.status_code, 409)
        self.assertTrue(Agent.objects.filter(pk=self.agent.pk).exists())

    # criptografia

    def test_secrets_are_encrypted_at_rest(self):
        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT access_token, app_secret FROM chats_whatsappaccount WHERE id = %s', [self.account.pk]
            )
            access_token, app_secret = cursor.fetchone()
        self.assertNotIn('EAAG-meu-token-secreto', access_token)
        self.assertNotIn('meu-app-secret', app_secret)

    def test_secrets_decrypt_on_read(self):
        self.account.refresh_from_db()
        self.assertEqual(self.account.access_token, 'EAAG-meu-token-secreto')
        self.assertEqual(self.account.app_secret, 'meu-app-secret')


class RegisterNumberTests(BaseChatsTestCase):
    """Ativação do número na Cloud API (POST /{phone_number_id}/register)."""

    def url(self, company_id: int, account_id: int) -> str:
        return f'/api/companies/{company_id}/whatsapp-accounts/{account_id}/register'

    def register(self, company_id: int, account_id: int, pin: str = '123456'):
        return self.client.post(
            self.url(company_id, account_id),
            data={'pin': pin},
            content_type='application/json',
            headers=self.auth,
        )

    # boundary

    @patch('apps.chats.whatsapp.httpx.post')
    def test_register_number_calls_meta(self, mock_post):
        mock_post.return_value = MagicMock(is_success=True)

        whatsapp.register_number(self.account, '654321')

        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], f'{whatsapp.GRAPH_URL}/111111/register')
        self.assertEqual(kwargs['headers']['Authorization'], 'Bearer EAAG-meu-token-secreto')
        self.assertEqual(kwargs['json'], {'messaging_product': 'whatsapp', 'pin': '654321'})

    @patch('apps.chats.whatsapp.httpx.post')
    def test_register_number_raises_meta_message(self, mock_post):
        # Meta recusa (número não verificado, PIN errado): sobe a msg real
        mock_post.return_value = MagicMock(
            is_success=False, json=MagicMock(return_value={'error': {'error_user_msg': 'PIN incorreto'}})
        )

        with self.assertRaises(ValidationError) as ctx:
            whatsapp.register_number(self.account, '000000')
        self.assertEqual(ctx.exception.message, 'PIN incorreto')

    # endpoint

    @patch('apps.chats.whatsapp.register_number')
    def test_register_endpoint_ok(self, mock_register):
        response = self.register(self.company.pk, self.account.pk)

        self.assertEqual(response.status_code, 200, response.json())
        mock_register.assert_called_once_with(self.account, '123456')

    @patch('apps.chats.whatsapp.register_number')
    def test_register_endpoint_foreign_account_returns_404(self, mock_register):
        response = self.register(self.company.pk, self.other_account.pk)

        self.assertEqual(response.status_code, 404)
        mock_register.assert_not_called()

    @patch('apps.chats.whatsapp.register_number')
    def test_register_endpoint_rejects_non_6_digit_pin(self, mock_register):
        for bad in ('12345', '1234567', 'abcdef', ''):
            response = self.register(self.company.pk, self.account.pk, pin=bad)
            self.assertEqual(response.status_code, 422, bad)
        mock_register.assert_not_called()


class ContactTests(BaseChatsTestCase):
    def url(self, company_id: int, account_id: int) -> str:
        return f'/api/companies/{company_id}/whatsapp-accounts/{account_id}/contacts'

    def test_list_contacts(self):
        response = self.client.get(self.url(self.company.pk, self.account.pk), headers=self.auth)
        self.assertEqual(response.status_code, 200, response.json())
        body = response.json()
        # @paginate embrulha em items/count
        self.assertEqual(body['count'], 1)
        self.assertEqual(body['items'][0]['wa_id'], '5511999999999')
        self.assertEqual(body['items'][0]['name'], 'Cliente A')

    def test_list_contacts_exposes_ai_opens_at(self):
        item = self.client.get(self.url(self.company.pk, self.account.pk), headers=self.auth).json()['items'][0]
        self.assertIsNone(item['ai_opens_at'])  # sem horário = IA sempre atende

        now = timezone.localtime()
        opens = (now + timedelta(hours=2)).replace(second=0, microsecond=0)
        schedule = [[] for _ in range(7)]
        schedule[opens.weekday()] = [[f'{opens:%H:%M}', '00:00']]
        Agent.objects.filter(pk=self.agent.pk).update(ai_schedule=schedule)
        item = self.client.get(self.url(self.company.pk, self.account.pk), headers=self.auth).json()['items'][0]
        self.assertEqual(datetime.fromisoformat(item['ai_opens_at']), opens)

    def test_list_contacts_of_foreign_account_returns_404(self):
        response = self.client.get(self.url(self.company.pk, self.other_account.pk), headers=self.auth)
        self.assertEqual(response.status_code, 404)

    def test_list_contacts_search_by_name_and_number(self):
        Contact.objects.create(account=self.account, wa_id='5511955555555', name='Fulano de Tal')
        by_name = self.client.get(self.url(self.company.pk, self.account.pk) + '?search=fulano', headers=self.auth)
        self.assertEqual([c['name'] for c in by_name.json()['items']], ['Fulano de Tal'])
        by_number = self.client.get(self.url(self.company.pk, self.account.pk) + '?search=99999', headers=self.auth)
        self.assertEqual([c['name'] for c in by_number.json()['items']], ['Cliente A'])

    def test_rename_contact(self):
        response = self.client.patch(
            f'/api/companies/{self.company.pk}/contacts/{self.contact.pk}',
            data=json.dumps({'name': 'Renomeado'}), content_type='application/json', headers=self.auth,
        )
        self.assertEqual(response.status_code, 200, response.json())
        self.contact.refresh_from_db()
        self.assertEqual(self.contact.name, 'Renomeado')
        self.assertFalse(self.contact.ai_paused)

    def test_delete_contact_cascades_messages(self):
        response = self.client.delete(
            f'/api/companies/{self.company.pk}/contacts/{self.contact.pk}', headers=self.auth
        )
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Contact.objects.filter(pk=self.contact.pk).exists())
        self.assertFalse(Message.objects.filter(contact_id=self.contact.pk).exists())

    def test_delete_foreign_contact_returns_404(self):
        response = self.client.delete(
            f'/api/companies/{self.company.pk}/contacts/{self.other_contact.pk}', headers=self.auth
        )
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Contact.objects.filter(pk=self.other_contact.pk).exists())

    def test_member_cannot_delete_contact(self):
        # apagar conversa é só do dono (get_owned_company); membro comum recebe 404 e nada some
        member = User.objects.create_user(email='member@a.com', password='strongpass123')
        Membership.objects.create(company=self.company, user=member)
        response = self.client.delete(
            f'/api/companies/{self.company.pk}/contacts/{self.contact.pk}', headers=auth_header(member)
        )
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Contact.objects.filter(pk=self.contact.pk).exists())

    # memória

    def memory_url(self, company_id: int, contact_id: int) -> str:
        return f'/api/companies/{company_id}/contacts/{contact_id}/memory'

    def test_list_contacts_exposes_memory(self):
        Contact.objects.filter(pk=self.contact.pk).update(memory='Nome: Matheus')
        response = self.client.get(self.url(self.company.pk, self.account.pk), headers=self.auth)
        self.assertEqual(response.json()['items'][0]['memory'], 'Nome: Matheus')

    def test_clear_contact_memory(self):
        Contact.objects.filter(pk=self.contact.pk).update(memory='Nome: Matheus')
        response = self.client.delete(self.memory_url(self.company.pk, self.contact.pk), headers=self.auth)
        self.assertEqual(response.status_code, 204)
        self.contact.refresh_from_db()
        self.assertEqual(self.contact.memory, '')

    def test_clear_foreign_contact_memory_returns_404(self):
        Contact.objects.filter(pk=self.other_contact.pk).update(memory='segredo alheio')
        response = self.client.delete(self.memory_url(self.company.pk, self.other_contact.pk), headers=self.auth)
        self.assertEqual(response.status_code, 404)
        self.other_contact.refresh_from_db()
        self.assertEqual(self.other_contact.memory, 'segredo alheio')

    # handoff

    def contact_url(self, company_id: int, contact_id: int) -> str:
        return f'/api/companies/{company_id}/contacts/{contact_id}'

    def test_pause_contact_ai(self):
        response = self.client.patch(
            self.contact_url(self.company.pk, self.contact.pk),
            data=json.dumps({'ai_paused': True}), content_type='application/json', headers=self.auth,
        )
        self.assertEqual(response.status_code, 200, response.json())
        self.assertTrue(response.json()['ai_paused'])
        self.contact.refresh_from_db()
        self.assertTrue(self.contact.ai_paused)

    def test_resume_contact_ai(self):
        Contact.objects.filter(pk=self.contact.pk).update(ai_paused=True)
        response = self.client.patch(
            self.contact_url(self.company.pk, self.contact.pk),
            data=json.dumps({'ai_paused': False}), content_type='application/json', headers=self.auth,
        )
        self.assertEqual(response.status_code, 200)
        self.contact.refresh_from_db()
        self.assertFalse(self.contact.ai_paused)

    def test_patch_foreign_contact_returns_404(self):
        response = self.client.patch(
            self.contact_url(self.company.pk, self.other_contact.pk),
            data=json.dumps({'ai_paused': True}), content_type='application/json', headers=self.auth,
        )
        self.assertEqual(response.status_code, 404)
        self.other_contact.refresh_from_db()
        self.assertFalse(self.other_contact.ai_paused)

    def test_list_contacts_exposes_ai_paused(self):
        response = self.client.get(self.url(self.company.pk, self.account.pk), headers=self.auth)
        self.assertFalse(response.json()['items'][0]['ai_paused'])

    # última mensagem (preview + ordenação estilo WhatsApp)

    def test_list_contacts_exposes_last_message(self):
        response = self.client.get(self.url(self.company.pk, self.account.pk), headers=self.auth)
        item = response.json()['items'][0]
        self.assertEqual(item['last_message'], 'Olá! Como posso ajudar?')
        self.assertIsNotNone(item['last_message_at'])

    def test_list_contacts_ordered_by_last_message(self):
        # contato com mensagem mais recente vem primeiro; sem mensagem vai pro fim
        recente = Contact.objects.create(account=self.account, wa_id='5511777777777', name='Cliente B')
        Message.objects.create(contact=recente, direction='in', content='Cheguei agora', wa_message_id='wamid.9')
        Contact.objects.create(account=self.account, wa_id='5511666666666', name='Cliente Mudo')
        response = self.client.get(self.url(self.company.pk, self.account.pk), headers=self.auth)
        items = response.json()['items']
        self.assertEqual([item['name'] for item in items], ['Cliente B', 'Cliente A', 'Cliente Mudo'])
        self.assertIsNone(items[2]['last_message'])


class CreateContactTests(BaseChatsTestCase):
    def url(self, company_id: int, account_id: int) -> str:
        return f'/api/companies/{company_id}/whatsapp-accounts/{account_id}/contacts'

    def post(self, company_id: int, account_id: int, payload: dict):
        return self.client.post(
            self.url(company_id, account_id), data=json.dumps(payload), content_type='application/json', headers=self.auth
        )

    def test_create_contact_normalizes_number(self):
        response = self.post(self.company.pk, self.account.pk, {'number': '+55 (11) 97777-7777', 'name': 'Novo Cliente'})
        self.assertEqual(response.status_code, 201, response.json())
        body = response.json()
        self.assertEqual(body['wa_id'], '5511977777777')
        self.assertEqual(body['name'], 'Novo Cliente')
        self.assertTrue(Contact.objects.filter(account=self.account, wa_id='5511977777777').exists())

    def test_create_contact_without_name(self):
        response = self.post(self.company.pk, self.account.pk, {'number': '5511977777777'})
        self.assertEqual(response.status_code, 201, response.json())
        self.assertEqual(response.json()['name'], '')

    def test_create_contact_invalid_number_returns_400(self):
        before = Contact.objects.count()
        response = self.post(self.company.pk, self.account.pk, {'number': '119999'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Contact.objects.count(), before)

    def test_create_contact_existing_number_returns_same_contact(self):
        # get_or_create: número repetido devolve o contato existente, nome original preservado
        response = self.post(self.company.pk, self.account.pk, {'number': '+55 11 99999-9999', 'name': 'Outro Nome'})
        self.assertEqual(response.status_code, 201, response.json())
        body = response.json()
        self.assertEqual(body['id'], self.contact.pk)
        self.assertEqual(body['name'], 'Cliente A')
        self.assertEqual(Contact.objects.filter(account=self.account, wa_id='5511999999999').count(), 1)

    def test_create_contact_on_foreign_company_returns_404(self):
        response = self.post(self.other_company.pk, self.other_account.pk, {'number': '5511977777777'})
        self.assertEqual(response.status_code, 404)

    def test_create_contact_on_foreign_account_returns_404(self):
        response = self.post(self.company.pk, self.other_account.pk, {'number': '5511977777777'})
        self.assertEqual(response.status_code, 404)


class ImportContactsTests(BaseChatsTestCase):
    def url(self, company_id: int, account_id: int) -> str:
        return f'/api/companies/{company_id}/whatsapp-accounts/{account_id}/contacts/import'

    def post(self, company_id: int, account_id: int, numbers: list[dict]):
        return self.client.post(
            self.url(company_id, account_id),
            data=json.dumps({'numbers': numbers}), content_type='application/json', headers=self.auth,
        )

    def test_import_counts_created_and_existing(self):
        response = self.post(self.company.pk, self.account.pk, [
            {'number': '+55 (11) 97777-7777', 'name': 'Novo A'},
            {'number': '5511966666666'},
            {'number': '+55 11 99999-9999', 'name': 'Já Existe'},  # wa_id do self.contact
        ])
        self.assertEqual(response.status_code, 200, response.json())
        self.assertEqual(response.json(), {'created': 2, 'existing': 1})
        self.assertTrue(Contact.objects.filter(account=self.account, wa_id='5511977777777', name='Novo A').exists())
        # existente preserva nome original
        self.contact.refresh_from_db()
        self.assertEqual(self.contact.name, 'Cliente A')

    def test_import_invalid_number_creates_nothing(self):
        # duas passadas: valida tudo antes de criar - nada de import parcial
        before = Contact.objects.count()
        response = self.post(self.company.pk, self.account.pk, [
            {'number': '5511977777777', 'name': 'Válido'},
            {'number': '123', 'name': 'Inválido'},
        ])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Contact.objects.count(), before)

    def test_import_empty_list_returns_400(self):
        response = self.post(self.company.pk, self.account.pk, [])
        self.assertEqual(response.status_code, 400)

    def test_import_on_foreign_account_returns_404(self):
        response = self.post(self.company.pk, self.other_account.pk, [{'number': '5511977777777'}])
        self.assertEqual(response.status_code, 404)

    def test_import_number_without_ninth_digit_matches_existing(self):
        # self.contact é 5511999999999; a variante sem o 9 é o mesmo celular
        response = self.post(self.company.pk, self.account.pk, [{'number': '551199999999', 'name': 'Sem o 9'}])
        self.assertEqual(response.status_code, 200, response.json())
        self.assertEqual(response.json(), {'created': 0, 'existing': 1})
        self.assertFalse(Contact.objects.filter(account=self.account, wa_id='551199999999').exists())

    def test_import_adds_country_code(self):
        response = self.post(self.company.pk, self.account.pk, [{'number': '(21) 97777-6666'}])
        self.assertEqual(response.status_code, 200, response.json())
        self.assertTrue(Contact.objects.filter(account=self.account, wa_id='5521977776666').exists())


class PhoneNumberTests(BaseChatsTestCase):
    """Telefone BR: DDI 55 padrão e 9º dígito (a Meta manda 12 dígitos pra muitos celulares antigos)."""

    def test_normalize_adds_country_code_to_national_number(self):
        self.assertEqual(services.normalize_number('11999998888'), '5511999998888')
        self.assertEqual(services.normalize_number('(11) 99999-8888'), '5511999998888')
        self.assertEqual(services.normalize_number('1133334444'), '551133334444')  # fixo

    def test_normalize_strips_trunk_prefix(self):
        self.assertEqual(services.normalize_number('0 11 99999-8888'), '5511999998888')

    def test_normalize_keeps_number_with_country_code(self):
        self.assertEqual(services.normalize_number('5511999998888'), '5511999998888')
        self.assertEqual(services.normalize_number('+55 11 99999-8888'), '5511999998888')
        self.assertEqual(services.normalize_number('551199998888'), '551199998888')  # formato antigo, a Meta usa

    def test_normalize_ddd_55_is_not_mistaken_for_country_code(self):
        # DDD 55 (RS): 11 dígitos começando com 55 é número nacional
        self.assertEqual(services.normalize_number('55999998888'), '5555999998888')

    def test_normalize_keeps_foreign_number_with_plus(self):
        self.assertEqual(services.normalize_number('+1 415 555 1234'), '14155551234')
        self.assertEqual(services.normalize_number('+44 7911 123456'), '447911123456')

    def test_normalize_rejects_impossible_number(self):
        for number in ['123', '119999', '', 'abc']:
            with self.subTest(number=number), self.assertRaises(ValidationError):
                services.normalize_number(number)

    def test_variants_of_mobile_with_ninth_digit(self):
        self.assertCountEqual(services.number_variants('5511999998888'), ['5511999998888', '551199998888'])

    def test_variants_of_mobile_without_ninth_digit(self):
        self.assertCountEqual(services.number_variants('551199998888'), ['551199998888', '5511999998888'])

    def test_variants_of_landline_is_only_itself(self):
        # fixo começa com 2-5: não existe 9º dígito
        self.assertEqual(services.number_variants('551133334444'), ['551133334444'])

    def test_variants_of_foreign_number_is_only_itself(self):
        self.assertEqual(services.number_variants('14155551234'), ['14155551234'])

    def test_get_or_create_contact_finds_existing_by_variant(self):
        before = Contact.objects.count()
        contact = services.get_or_create_contact(self.account, '551199999999', 'Outro Nome')
        self.assertEqual(contact.pk, self.contact.pk)
        self.assertEqual(contact.name, 'Cliente A')
        self.assertEqual(Contact.objects.count(), before)

    def test_get_or_create_contact_does_not_cross_accounts(self):
        # variante de um contato de outra account não conta
        contact = services.get_or_create_contact(self.other_account, '551199999999')
        self.assertNotEqual(contact.pk, self.contact.pk)
        self.assertEqual(contact.account, self.other_account)

    def test_get_or_create_contact_creates_when_no_variant_exists(self):
        contact = services.get_or_create_contact(self.account, '551188887777', 'Novo')
        self.assertEqual(contact.wa_id, '551188887777')
        self.assertEqual(contact.name, 'Novo')

    def test_create_contact_endpoint_with_variant_returns_existing(self):
        response = self.client.post(
            f'/api/companies/{self.company.pk}/whatsapp-accounts/{self.account.pk}/contacts',
            data=json.dumps({'number': '+55 11 9999-9999'}), content_type='application/json', headers=self.auth,
        )
        self.assertEqual(response.status_code, 201, response.json())
        self.assertEqual(response.json()['id'], self.contact.pk)


class MessageTests(BaseChatsTestCase):
    def url(self, company_id: int, contact_id: int) -> str:
        return f'/api/companies/{company_id}/contacts/{contact_id}/messages'

    def test_list_messages_ordered(self):
        # ordem desc (mais recente primeiro): página 0 da paginação = fim da conversa
        response = self.client.get(self.url(self.company.pk, self.contact.pk), headers=self.auth)
        self.assertEqual(response.status_code, 200, response.json())
        body = response.json()
        self.assertEqual(body['count'], 2)
        contents = [message['content'] for message in body['items']]
        self.assertEqual(contents, ['Olá! Como posso ajudar?', 'Oi'])
        directions = [message['direction'] for message in body['items']]
        self.assertEqual(directions, ['out', 'in'])

    def test_list_messages_of_foreign_contact_returns_404(self):
        response = self.client.get(self.url(self.company.pk, self.other_contact.pk), headers=self.auth)
        self.assertEqual(response.status_code, 404)

    def test_list_messages_exposes_from_human(self):
        response = self.client.get(self.url(self.company.pk, self.contact.pk), headers=self.auth)
        self.assertEqual([m['from_human'] for m in response.json()['items']], [False, False])


class EventsTests(BaseChatsTestCase):
    """SSE: evento é dica (front re-busca via API) e SEMPRE publica após gravar no banco."""

    def close_stream(self, response):
        # close() de StreamingHttpResponse dispara request_finished → close_old_connections,
        # que fecharia a conexão transacional do teste e quebraria o teste seguinte
        from django.core.signals import request_finished
        from django.db import close_old_connections
        request_finished.disconnect(close_old_connections)
        try:
            response.close()
        finally:
            request_finished.connect(close_old_connections)

    def test_record_incoming_publishes_after_insert(self):
        # side_effect fotografa o banco no momento do publish: INSERT já tem que estar visível
        seen = {}
        self.mock_events.side_effect = lambda company_id, event: seen.update(
            exists=Message.objects.filter(wa_message_id='wamid.novo').exists(),
            company_id=company_id, event=event,
        )
        services.record_incoming_message(self.contact, 'wamid.novo', 'Oi de novo')
        self.assertTrue(seen['exists'])
        self.assertEqual(seen['company_id'], self.company.pk)
        self.assertEqual(seen['event'], {'type': 'message', 'contact_id': self.contact.pk})

    def test_record_outgoing_publishes_after_insert(self):
        seen = {}
        self.mock_events.side_effect = lambda company_id, event: seen.update(
            exists=Message.objects.filter(wa_message_id='wamid.out').exists(), event=event,
        )
        services.record_outgoing_message(self.contact, 'wamid.out', 'Resposta')
        self.assertTrue(seen['exists'])
        self.assertEqual(seen['event'], {'type': 'message', 'contact_id': self.contact.pk})

    def test_update_contact_publishes_contact_event(self):
        services.update_contact(self.company, self.contact.pk, {'ai_paused': True})
        self.mock_events.assert_called_once_with(
            self.company.pk, {'type': 'contact', 'contact_id': self.contact.pk}
        )

    def test_stream_endpoint_yields_published_event(self):
        # view chamada direto (sem test client): streaming + test client fecham a
        # conexão de banco do teste via request_finished; aqui não há signal nenhum
        from api.endpoints.events import events_endpoint

        with patch('api.endpoints.events.redis') as mock_redis:
            pubsub = mock_redis.Redis.from_url.return_value.pubsub.return_value
            pubsub.get_message.return_value = {'type': 'message', 'data': b'{"type": "message", "contact_id": 7}'}

            request = MagicMock(auth=self.user)
            response = events_endpoint(request, self.company.pk)

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response['Content-Type'], 'text/event-stream')
            first = next(iter(response.streaming_content))
            self.assertEqual(first, b'data: {"type": "message", "contact_id": 7}\n\n')
            pubsub.subscribe.assert_called_once_with(f'chatly:events:{self.company.pk}')
            self.close_stream(response)  # dispara o finally do generator
            pubsub.close.assert_called_once()

    def test_stream_endpoint_keepalive_when_no_event(self):
        from api.endpoints.events import events_endpoint

        with patch('api.endpoints.events.redis') as mock_redis:
            pubsub = mock_redis.Redis.from_url.return_value.pubsub.return_value
            pubsub.get_message.return_value = None  # timeout sem evento

            response = events_endpoint(MagicMock(auth=self.user), self.company.pk)
            first = next(iter(response.streaming_content))

            self.assertTrue(first.startswith(b':'))  # comentário SSE = keepalive
            self.close_stream(response)

    def test_stream_endpoint_foreign_company_returns_404(self):
        response = self.client.get(f'/api/companies/{self.other_company.pk}/events', headers=self.auth)
        self.assertEqual(response.status_code, 404)


class ManualMessageTests(BaseChatsTestCase):
    """POST do atendente humano: envia na hora e grava com from_human=True."""

    def post(self, company_id: int, contact_id: int, content: str = 'Oi, aqui é o atendente'):
        return self.client.post(
            f'/api/companies/{company_id}/contacts/{contact_id}/messages',
            data=json.dumps({'content': content}), content_type='application/json', headers=self.auth,
        )

    @patch('apps.chats.whatsapp.send_text')
    def test_send_manual_message(self, mock_send):
        mock_send.return_value = 'wamid.manual'

        response = self.post(self.company.pk, self.contact.pk)

        self.assertEqual(response.status_code, 201, response.json())
        mock_send.assert_called_once_with(self.account, '5511999999999', 'Oi, aqui é o atendente')
        message = Message.objects.get(wa_message_id='wamid.manual')
        self.assertEqual(message.direction, 'out')
        self.assertTrue(message.from_human)
        self.assertTrue(response.json()['from_human'])

    @patch('apps.chats.whatsapp.send_text')
    def test_send_manual_message_to_foreign_contact_returns_404(self, mock_send):
        response = self.post(self.company.pk, self.other_contact.pk)
        self.assertEqual(response.status_code, 404)
        mock_send.assert_not_called()

    @patch('apps.chats.whatsapp.send_text')
    def test_send_manual_message_whatsapp_rejection_returns_400(self, mock_send):
        # rejeição síncrona da Meta (número inválido etc.): erro limpo, nada gravado
        mock_send.side_effect = httpx.HTTPStatusError('Bad Request', request=MagicMock(), response=MagicMock())
        out_count = Message.objects.filter(direction='out').count()

        response = self.post(self.company.pk, self.contact.pk)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Message.objects.filter(direction='out').count(), out_count)

    # janela de 24h: a Meta aceita o POST fora da janela e falha async via webhook
    # de status - a fronteira que o usuário vê tem que ser nossa, antes do envio

    @patch('apps.chats.whatsapp.send_text')
    def test_send_manual_message_outside_window_returns_400(self, mock_send):
        close_service_window(self.contact)
        out_count = Message.objects.filter(direction='out').count()

        response = self.post(self.company.pk, self.contact.pk)

        self.assertEqual(response.status_code, 400)
        mock_send.assert_not_called()
        self.assertEqual(Message.objects.filter(direction='out').count(), out_count)

    @patch('apps.chats.whatsapp.send_text')
    def test_send_manual_message_without_any_inbound_returns_400(self, mock_send):
        # contato importado que nunca respondeu: janela nem existe ainda
        contact = Contact.objects.create(account=self.account, wa_id='5511777777777')

        response = self.post(self.company.pk, contact.pk)

        self.assertEqual(response.status_code, 400)
        mock_send.assert_not_called()


class HandoffTests(BaseChatsTestCase):
    """Pausa com prazo, atendente assume ao escrever, atribuição e fila."""

    def setUp(self):
        super().setUp()
        patcher = patch('apps.chats.tasks.update_contact_memory.delay')
        patcher.start()
        self.addCleanup(patcher.stop)

    def patch_contact(self, payload: dict, contact_id=None):
        return self.client.patch(
            f'/api/companies/{self.company.pk}/contacts/{contact_id or self.contact.pk}',
            data=json.dumps(payload), content_type='application/json', headers=self.auth,
        )

    def list_queue(self, queue: str, auth=None):
        return self.client.get(
            f'/api/companies/{self.company.pk}/whatsapp-accounts/{self.account.pk}/contacts',
            {'queue': queue}, headers=auth or self.auth,
        )

    # prazo da pausa

    def test_pause_ai_sets_deadline_from_agent(self):
        services.pause_ai(self.contact)
        self.contact.refresh_from_db()
        self.assertTrue(self.contact.ai_paused)
        expected = timezone.now() + timedelta(hours=24)
        self.assertAlmostEqual(self.contact.ai_resume_at.timestamp(), expected.timestamp(), delta=5)

    def test_pause_ai_without_deadline_when_agent_says_zero(self):
        Agent.objects.filter(pk=self.agent.pk).update(handoff_resume_hours=0)
        self.contact.refresh_from_db()
        services.pause_ai(self.contact)
        self.contact.refresh_from_db()
        self.assertTrue(self.contact.ai_paused)
        self.assertIsNone(self.contact.ai_resume_at)

    @patch('apps.chats.whatsapp.send_text')
    @patch('apps.ai.generate_reply')
    def test_expired_pause_resumes_and_replies(self, mock_generate, mock_send):
        mock_generate.return_value = 'Voltei'
        mock_send.return_value = 'wamid.volta'
        Contact.objects.filter(pk=self.contact.pk).update(ai_paused=True, ai_resume_at=timezone.now() - timedelta(minutes=1))
        incoming = Message.objects.create(contact=self.contact, direction='in', content='Alguém?', wa_message_id='wamid.novo')

        tasks.process_incoming_message(incoming.pk)

        mock_send.assert_called_once()
        self.contact.refresh_from_db()
        self.assertFalse(self.contact.ai_paused)
        self.assertIsNone(self.contact.ai_resume_at)

    @patch('apps.chats.whatsapp.send_text')
    @patch('apps.ai.generate_reply')
    def test_pause_within_deadline_stays_silent(self, mock_generate, mock_send):
        Contact.objects.filter(pk=self.contact.pk).update(ai_paused=True, ai_resume_at=timezone.now() + timedelta(hours=1))
        incoming = Message.objects.create(contact=self.contact, direction='in', content='Alguém?', wa_message_id='wamid.novo')

        tasks.process_incoming_message(incoming.pk)

        mock_generate.assert_not_called()
        self.contact.refresh_from_db()
        self.assertTrue(self.contact.ai_paused)

    @patch('apps.chats.whatsapp.send_text')
    @patch('apps.ai.generate_reply')
    def test_pause_without_deadline_never_resumes(self, mock_generate, mock_send):
        Contact.objects.filter(pk=self.contact.pk).update(ai_paused=True, ai_resume_at=None)
        incoming = Message.objects.create(contact=self.contact, direction='in', content='Alguém?', wa_message_id='wamid.novo')

        tasks.process_incoming_message(incoming.pk)

        mock_generate.assert_not_called()

    @patch('apps.chats.whatsapp.send_text')
    @patch('apps.ai.generate_reply')
    def test_handoff_tool_sets_deadline_and_notifies(self, mock_generate, mock_send):
        def call_handoff(*args, tools, **kwargs):
            next(t for t in tools if t.__name__ == 'transferir_para_atendente')(motivo='cliente pediu humano')
            return 'Vou chamar um atendente.'

        mock_generate.side_effect = call_handoff
        mock_send.return_value = 'wamid.tchau'
        tasks.process_incoming_message(Message.objects.get(wa_message_id='wamid.1').pk)

        self.contact.refresh_from_db()
        self.assertTrue(self.contact.ai_paused)
        self.assertIsNotNone(self.contact.ai_resume_at)
        types = [c.args[1]['type'] for c in self.mock_events.call_args_list]
        self.assertIn('handoff', types)

    # atendente escreveu

    @patch('apps.chats.whatsapp.send_text')
    def test_manual_message_pauses_ai_and_assigns_sender(self, mock_send):
        mock_send.return_value = 'wamid.manual'
        response = self.client.post(
            f'/api/companies/{self.company.pk}/contacts/{self.contact.pk}/messages',
            data=json.dumps({'content': 'Oi, aqui é o atendente'}), content_type='application/json', headers=self.auth,
        )
        self.assertEqual(response.status_code, 201, response.json())
        self.contact.refresh_from_db()
        self.assertTrue(self.contact.ai_paused)
        self.assertIsNotNone(self.contact.ai_resume_at)
        self.assertEqual(self.contact.assigned_to, self.user)
        self.assertIn({'type': 'contact', 'contact_id': self.contact.pk}, [c.args[1] for c in self.mock_events.call_args_list])

    @patch('apps.chats.whatsapp.send_text')
    def test_manual_message_keeps_existing_assignee(self, mock_send):
        mock_send.return_value = 'wamid.manual'
        colleague = User.objects.create_user(email='colega@a.com', password='strongpass123')
        Membership.objects.create(company=self.company, user=colleague)
        Contact.objects.filter(pk=self.contact.pk).update(assigned_to=colleague)

        services.send_manual_message(self.company, self.contact.pk, 'Oi', self.user)

        self.contact.refresh_from_db()
        self.assertEqual(self.contact.assigned_to, colleague)

    @patch('apps.chats.whatsapp.send_text')
    def test_manual_message_failure_does_not_pause(self, mock_send):
        mock_send.side_effect = httpx.HTTPStatusError('Bad Request', request=MagicMock(), response=MagicMock())
        with self.assertRaises(ValidationError):
            services.send_manual_message(self.company, self.contact.pk, 'Oi', self.user)
        self.contact.refresh_from_db()
        self.assertFalse(self.contact.ai_paused)
        self.assertIsNone(self.contact.assigned_to)

    # PATCH: pausa manual e atribuição

    def test_patch_pause_sets_deadline_and_unpause_clears(self):
        self.assertEqual(self.patch_contact({'ai_paused': True}).status_code, 200)
        self.contact.refresh_from_db()
        self.assertIsNotNone(self.contact.ai_resume_at)

        response = self.patch_contact({'ai_paused': False})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()['ai_resume_at'])

    def test_assign_to_member(self):
        colleague = User.objects.create_user(email='colega@a.com', password='strongpass123')
        Membership.objects.create(company=self.company, user=colleague)
        response = self.patch_contact({'assigned_to': colleague.pk})
        self.assertEqual(response.status_code, 200, response.json())
        self.assertEqual(response.json()['assigned_to'], colleague.pk)

    def test_assign_to_owner(self):
        response = self.patch_contact({'assigned_to': self.user.pk})
        self.assertEqual(response.status_code, 200, response.json())
        self.assertEqual(response.json()['assigned_to'], self.user.pk)

    def test_assign_to_outsider_returns_400(self):
        response = self.patch_contact({'assigned_to': self.other_user.pk})
        self.assertEqual(response.status_code, 400)
        self.contact.refresh_from_db()
        self.assertIsNone(self.contact.assigned_to)

    def test_unassign_with_null(self):
        Contact.objects.filter(pk=self.contact.pk).update(assigned_to=self.user)
        response = self.patch_contact({'assigned_to': None})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()['assigned_to'])

    def test_patch_without_assigned_to_keeps_it(self):
        Contact.objects.filter(pk=self.contact.pk).update(assigned_to=self.user)
        self.patch_contact({'name': 'Novo nome'})
        self.contact.refresh_from_db()
        self.assertEqual(self.contact.assigned_to, self.user)

    # fila

    def test_queue_waiting_lists_paused_without_assignee(self):
        waiting = Contact.objects.create(account=self.account, wa_id='5511911112222', ai_paused=True)
        Contact.objects.create(account=self.account, wa_id='5511933334444', ai_paused=True, assigned_to=self.user)
        response = self.list_queue('waiting')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([c['id'] for c in response.json()['items']], [waiting.pk])

    def test_queue_mine_lists_only_my_contacts(self):
        colleague = User.objects.create_user(email='colega@a.com', password='strongpass123')
        Membership.objects.create(company=self.company, user=colleague)
        mine = Contact.objects.create(account=self.account, wa_id='5511911112222', assigned_to=self.user)
        Contact.objects.create(account=self.account, wa_id='5511933334444', assigned_to=colleague)
        response = self.list_queue('mine')
        self.assertEqual([c['id'] for c in response.json()['items']], [mine.pk])

    def test_queue_invalid_value_returns_422(self):
        self.assertEqual(self.list_queue('tudo').status_code, 422)


@override_settings(**LOCAL_STORAGE_SETTINGS)
class MediaMessageTests(BaseChatsTestCase):
    """POST multipart do atendente + proxy autenticado de mídia recebida."""

    def post_media(self, contact_id: int, file, caption: str = ''):
        data = {'file': file}
        if caption:
            data['caption'] = caption
        return self.client.post(
            f'/api/companies/{self.company.pk}/contacts/{contact_id}/messages/media',
            data=data, headers=self.auth,
        )

    @patch('apps.chats.whatsapp.send_media')
    @patch('apps.chats.whatsapp.upload_media')
    def test_send_image_with_caption(self, mock_upload, mock_send):
        mock_upload.return_value = 'media-up'
        mock_send.return_value = 'wamid.media-img'
        file = SimpleUploadedFile('foto.jpg', b'jpeg-bytes', content_type='image/jpeg')

        response = self.post_media(self.contact.pk, file, caption='Olha essa foto')

        self.assertEqual(response.status_code, 201, response.json())
        mock_upload.assert_called_once_with(self.account, b'jpeg-bytes', 'image/jpeg', 'foto.jpg')
        mock_send.assert_called_once_with(self.account, '5511999999999', 'image', 'media-up', 'Olha essa foto', 'foto.jpg')
        message = Message.objects.get(wa_message_id='wamid.media-img')
        self.assertEqual(message.message_type, 'image')
        self.assertEqual(message.content, 'Olha essa foto')
        self.assertTrue(message.from_human)
        # regressão do seek(0): read() do upload deixa o ponteiro no fim - sem
        # seek o FileField salvaria arquivo vazio
        self.assertEqual(message.media_file.read(), b'jpeg-bytes')
        self.contact.refresh_from_db()
        self.assertTrue(self.contact.ai_paused)  # atendente mandou mídia: IA pausa
        body = response.json()
        self.assertEqual(body['message_type'], 'image')
        self.assertIsNotNone(body['media_url'])

    @patch('apps.chats.whatsapp.upload_media')
    def test_send_media_outside_window_returns_400(self, mock_upload):
        close_service_window(self.contact)
        file = SimpleUploadedFile('foto.jpg', b'jpeg-bytes', content_type='image/jpeg')

        response = self.post_media(self.contact.pk, file)

        self.assertEqual(response.status_code, 400)
        mock_upload.assert_not_called()

    @patch('apps.chats.whatsapp.send_media')
    @patch('apps.chats.whatsapp.upload_media')
    def test_send_image_without_caption_gets_placeholder(self, mock_upload, mock_send):
        # content vazio quebraria o build_history (text block vazio)
        mock_upload.return_value = 'media-up2'
        mock_send.return_value = 'wamid.media-img2'
        file = SimpleUploadedFile('foto.png', b'png-bytes', content_type='image/png')
        self.post_media(self.contact.pk, file)
        self.assertEqual(Message.objects.get(wa_message_id='wamid.media-img2').content, '[imagem]')

    @patch('apps.chats.whatsapp.send_media')
    @patch('apps.chats.whatsapp.upload_media')
    def test_send_document_without_caption_gets_filename_placeholder(self, mock_upload, mock_send):
        mock_upload.return_value = 'media-doc'
        mock_send.return_value = 'wamid.media-doc'
        file = SimpleUploadedFile('contrato.pdf', b'pdf-bytes', content_type='application/pdf')
        response = self.post_media(self.contact.pk, file)
        self.assertEqual(response.status_code, 201, response.json())
        message = Message.objects.get(wa_message_id='wamid.media-doc')
        self.assertEqual(message.message_type, 'document')
        self.assertEqual(message.content, '[documento: contrato.pdf]')

    @patch('apps.chats.whatsapp.upload_media')
    def test_gif_returns_400_without_uploading(self, mock_upload):
        # Meta rejeita gif como image - barra antes de gastar upload
        file = SimpleUploadedFile('anim.gif', b'gif-bytes', content_type='image/gif')
        response = self.post_media(self.contact.pk, file)
        self.assertEqual(response.status_code, 400)
        mock_upload.assert_not_called()
        self.assertFalse(Message.objects.filter(direction='out', message_type='image').exists())

    @patch('apps.chats.whatsapp.upload_media')
    def test_oversized_image_returns_400(self, mock_upload):
        file = SimpleUploadedFile('grande.jpg', b'x' * (5 * 2**20 + 1), content_type='image/jpeg')
        response = self.post_media(self.contact.pk, file)
        self.assertEqual(response.status_code, 400)
        mock_upload.assert_not_called()

    @patch('apps.chats.whatsapp.send_media')
    @patch('apps.chats.whatsapp.upload_media')
    def test_send_webp_goes_as_sticker(self, mock_upload, mock_send):
        # webp cai como sticker, não image - e sticker não leva caption
        mock_upload.return_value = 'media-stk'
        mock_send.return_value = 'wamid.media-stk'
        file = SimpleUploadedFile('figurinha.webp', b'webp-bytes', content_type='image/webp')

        response = self.post_media(self.contact.pk, file)

        self.assertEqual(response.status_code, 201, response.json())
        mock_send.assert_called_once_with(self.account, '5511999999999', 'sticker', 'media-stk', '', 'figurinha.webp')
        message = Message.objects.get(wa_message_id='wamid.media-stk')
        self.assertEqual(message.message_type, 'sticker')
        self.assertEqual(message.content, '[figurinha]')

    @patch('apps.chats.whatsapp.upload_media')
    def test_oversized_sticker_returns_400(self, mock_upload):
        file = SimpleUploadedFile('grande.webp', b'x' * (500 * 2**10 + 1), content_type='image/webp')
        response = self.post_media(self.contact.pk, file)
        self.assertEqual(response.status_code, 400)
        mock_upload.assert_not_called()

    @patch('apps.chats.whatsapp.upload_media')
    def test_send_media_to_foreign_contact_returns_404(self, mock_upload):
        file = SimpleUploadedFile('foto.jpg', b'jpeg-bytes', content_type='image/jpeg')
        response = self.post_media(self.other_contact.pk, file)
        self.assertEqual(response.status_code, 404)
        mock_upload.assert_not_called()

    @patch('apps.chats.whatsapp.send_media')
    @patch('apps.chats.whatsapp.upload_media')
    def test_whatsapp_rejection_returns_400_and_records_nothing(self, mock_upload, mock_send):
        mock_upload.return_value = 'media-up3'
        mock_send.side_effect = httpx.HTTPStatusError('Bad Request', request=MagicMock(), response=MagicMock())
        out_count = Message.objects.filter(direction='out').count()
        file = SimpleUploadedFile('foto.jpg', b'jpeg-bytes', content_type='image/jpeg')

        response = self.post_media(self.contact.pk, file)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Message.objects.filter(direction='out').count(), out_count)

    # proxy de mídia recebida (baixa da Meta na hora, media_id vale ~30 dias)

    def media_url(self, message_id: int) -> str:
        return f'/api/companies/{self.company.pk}/messages/{message_id}/media'

    @patch('apps.chats.whatsapp.download_media')
    def test_proxy_streams_received_media(self, mock_download):
        mock_download.return_value = (b'jpeg-da-meta', 'image/jpeg')
        message = Message.objects.create(
            contact=self.contact, direction='in', content='[imagem]',
            message_type='image', media_id='media-in', wa_message_id='wamid.in-img',
        )

        response = self.client.get(self.media_url(message.pk), headers=self.auth)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'jpeg-da-meta')
        self.assertEqual(response['Content-Type'], 'image/jpeg')
        self.assertEqual(response['Cache-Control'], 'private, max-age=86400')
        mock_download.assert_called_once_with(self.account, 'media-in')

    def test_proxy_foreign_message_returns_404(self):
        foreign = Message.objects.create(
            contact=self.other_contact, direction='in', content='[imagem]',
            message_type='image', media_id='media-alheia', wa_message_id='wamid.in-foreign',
        )
        response = self.client.get(self.media_url(foreign.pk), headers=self.auth)
        self.assertEqual(response.status_code, 404)

    def test_proxy_message_without_media_returns_404(self):
        message = Message.objects.get(wa_message_id='wamid.1')  # texto puro
        response = self.client.get(self.media_url(message.pk), headers=self.auth)
        self.assertEqual(response.status_code, 404)

    @patch('apps.chats.whatsapp.download_media')
    def test_proxy_expired_media_returns_404(self, mock_download):
        mock_download.side_effect = httpx.HTTPStatusError('Bad Request', request=MagicMock(), response=MagicMock())
        message = Message.objects.create(
            contact=self.contact, direction='in', content='[imagem]',
            message_type='image', media_id='media-exp', wa_message_id='wamid.in-exp',
        )
        response = self.client.get(self.media_url(message.pk), headers=self.auth)
        self.assertEqual(response.status_code, 404)


@override_settings(**LOCAL_STORAGE_SETTINGS)
class StickerTests(BaseChatsTestCase):
    """Galeria de figurinhas da empresa: salvar do chat, listar, deletar, reenviar."""

    def url(self, company_id: int, sticker_id: int | None = None) -> str:
        base = f'/api/companies/{company_id}/stickers'
        return f'{base}/{sticker_id}' if sticker_id else base

    def save(self, company_id: int, message_id: int):
        return self.client.post(
            self.url(company_id), data=json.dumps({'message_id': message_id}),
            content_type='application/json', headers=self.auth,
        )

    def received_sticker(self, contact=None, wamid='wamid.stk-recv'):
        return Message.objects.create(
            contact=contact or self.contact, direction='in', content='[figurinha]',
            message_type='sticker', media_id='media-stk', wa_message_id=wamid,
        )

    # salvar

    @patch('apps.chats.whatsapp.download_media')
    def test_save_sticker_from_received_message(self, mock_download):
        mock_download.return_value = (b'webp-da-meta', 'image/webp')
        message = self.received_sticker()

        response = self.save(self.company.pk, message.pk)

        self.assertEqual(response.status_code, 201, response.json())
        sticker = Sticker.objects.get(pk=response.json()['id'])
        self.assertEqual(sticker.company, self.company)
        self.assertEqual(sticker.file.read(), b'webp-da-meta')
        self.assertTrue(sticker.file.name.endswith('.webp'))
        self.assertTrue(response.json()['url'])
        mock_download.assert_called_once_with(self.account, 'media-stk')

    @patch('apps.chats.whatsapp.download_media')
    def test_save_sticker_from_sent_message_reads_local_file(self, mock_download):
        # enviada tem cópia local - não gasta chamada na Meta
        message = Message.objects.create(
            contact=self.contact, direction='out', content='[figurinha]', message_type='sticker',
            wa_message_id='wamid.stk-out', media_file=SimpleUploadedFile('f.webp', b'webp-local', content_type='image/webp'),
        )

        response = self.save(self.company.pk, message.pk)

        self.assertEqual(response.status_code, 201, response.json())
        self.assertEqual(Sticker.objects.get(pk=response.json()['id']).file.read(), b'webp-local')
        mock_download.assert_not_called()

    @patch('apps.chats.whatsapp.download_media', return_value=(b'webp', 'image/webp'))
    def test_saving_twice_returns_same_sticker_and_marks_message(self, mock_download):
        message = self.received_sticker()
        first = self.save(self.company.pk, message.pk).json()['id']
        second = self.save(self.company.pk, message.pk).json()['id']
        self.assertEqual(first, second)
        self.assertEqual(mock_download.call_count, 1)  # segunda vez nem baixa

        url = f'/api/companies/{self.company.pk}/contacts/{self.contact.pk}/messages'
        saved = {m['id']: m['sticker_saved'] for m in self.client.get(url, headers=self.auth).json()['items']}
        self.assertTrue(saved[message.pk])
        self.assertFalse(saved[Message.objects.get(wa_message_id='wamid.1').pk])

        Sticker.objects.get(pk=first).delete()  # tirou da galeria: deixa de estar marcada
        saved = {m['id']: m['sticker_saved'] for m in self.client.get(url, headers=self.auth).json()['items']}
        self.assertFalse(saved[message.pk])

    def test_save_non_sticker_message_returns_404(self):
        message = Message.objects.get(wa_message_id='wamid.1')  # texto
        response = self.save(self.company.pk, message.pk)
        self.assertEqual(response.status_code, 404)
        self.assertFalse(Sticker.objects.exists())

    def test_save_foreign_message_returns_404(self):
        message = self.received_sticker(contact=self.other_contact, wamid='wamid.stk-foreign')
        response = self.save(self.company.pk, message.pk)
        self.assertEqual(response.status_code, 404)

    @patch('apps.chats.whatsapp.download_media')
    def test_save_expired_media_returns_404(self, mock_download):
        mock_download.side_effect = httpx.HTTPStatusError('Bad Request', request=MagicMock(), response=MagicMock())
        message = self.received_sticker(wamid='wamid.stk-exp')
        response = self.save(self.company.pk, message.pk)
        self.assertEqual(response.status_code, 404)
        self.assertFalse(Sticker.objects.exists())

    # listar / deletar

    def test_list_stickers_only_mine(self):
        Sticker.objects.create(company=self.company, file=ContentFile(b'meu', name='a.webp'))
        Sticker.objects.create(company=self.other_company, file=ContentFile(b'alheio', name='b.webp'))

        response = self.client.get(self.url(self.company.pk), headers=self.auth)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body), 1)
        self.assertTrue(body[0]['url'])

    def test_delete_sticker(self):
        sticker = Sticker.objects.create(company=self.company, file=ContentFile(b'x', name='a.webp'))
        response = self.client.delete(self.url(self.company.pk, sticker.pk), headers=self.auth)
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Sticker.objects.filter(pk=sticker.pk).exists())

    def test_delete_foreign_sticker_returns_404(self):
        sticker = Sticker.objects.create(company=self.other_company, file=ContentFile(b'x', name='b.webp'))
        response = self.client.delete(self.url(self.company.pk, sticker.pk), headers=self.auth)
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Sticker.objects.filter(pk=sticker.pk).exists())

    # reenviar

    def send_sticker(self, contact_id: int, sticker_id: int):
        return self.client.post(
            f'/api/companies/{self.company.pk}/contacts/{contact_id}/messages/sticker',
            data=json.dumps({'sticker_id': sticker_id}), content_type='application/json', headers=self.auth,
        )

    @patch('apps.chats.whatsapp.send_media')
    @patch('apps.chats.whatsapp.upload_media')
    def test_send_saved_sticker(self, mock_upload, mock_send):
        mock_upload.return_value = 'media-up-stk'
        mock_send.return_value = 'wamid.stk-sent'
        sticker = Sticker.objects.create(company=self.company, file=ContentFile(b'webp-salvo', name='s.webp'))

        response = self.send_sticker(self.contact.pk, sticker.pk)

        self.assertEqual(response.status_code, 201, response.json())
        mock_upload.assert_called_once_with(self.account, b'webp-salvo', 'image/webp', 'sticker.webp')
        mock_send.assert_called_once_with(self.account, '5511999999999', 'sticker', 'media-up-stk')
        message = Message.objects.get(wa_message_id='wamid.stk-sent')
        self.assertEqual(message.message_type, 'sticker')
        self.assertEqual(message.content, '[figurinha]')
        self.assertTrue(message.from_human)
        self.assertEqual(message.media_file.read(), b'webp-salvo')
        self.contact.refresh_from_db()
        self.assertTrue(self.contact.ai_paused)

    @patch('apps.chats.whatsapp.upload_media')
    def test_send_saved_sticker_outside_window_returns_400(self, mock_upload):
        close_service_window(self.contact)
        sticker = Sticker.objects.create(company=self.company, file=ContentFile(b'webp-salvo', name='s.webp'))

        response = self.send_sticker(self.contact.pk, sticker.pk)

        self.assertEqual(response.status_code, 400)
        mock_upload.assert_not_called()

    @patch('apps.chats.whatsapp.upload_media')
    def test_send_foreign_sticker_returns_404(self, mock_upload):
        sticker = Sticker.objects.create(company=self.other_company, file=ContentFile(b'x', name='b.webp'))
        response = self.send_sticker(self.contact.pk, sticker.pk)
        self.assertEqual(response.status_code, 404)
        mock_upload.assert_not_called()

    @patch('apps.chats.whatsapp.upload_media')
    def test_send_sticker_to_foreign_contact_returns_404(self, mock_upload):
        sticker = Sticker.objects.create(company=self.company, file=ContentFile(b'x', name='a.webp'))
        response = self.send_sticker(self.other_contact.pk, sticker.pk)
        self.assertEqual(response.status_code, 404)
        mock_upload.assert_not_called()


class WebhookTests(BaseChatsTestCase):
    URL = '/api/whatsapp/webhook'

    def setUp(self):
        super().setUp()
        # apply_async publicaria no broker real - mock cobre todos os POSTs da classe
        patcher = patch('api.endpoints.webhook.process_incoming_message.apply_async')
        self.mock_enqueue = patcher.start()
        self.addCleanup(patcher.stop)

    # helpers

    def sign(self, body: bytes, secret: str = 'meu-app-secret') -> str:
        return 'sha256=' + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    def message_payload(self, phone_number_id='111111', wa_id='5511777777777',
                        name='Novo Cliente', text='Quero comprar', wamid='wamid.novo'):
        return {
            'object': 'whatsapp_business_account',
            'entry': [{
                'id': 'waba-1',
                'changes': [{
                    'field': 'messages',
                    'value': {
                        'messaging_product': 'whatsapp',
                        'metadata': {'display_phone_number': '5511000000000', 'phone_number_id': phone_number_id},
                        'contacts': [{'profile': {'name': name}, 'wa_id': wa_id}],
                        'messages': [{
                            'from': wa_id, 'id': wamid, 'timestamp': '1720000000',
                            'type': 'text', 'text': {'body': text},
                        }],
                    },
                }],
            }],
        }

    def status_payload(self, wamid='wamid.1', status='delivered', phone_number_id='111111'):
        return {
            'object': 'whatsapp_business_account',
            'entry': [{
                'id': 'waba-1',
                'changes': [{
                    'field': 'messages',
                    'value': {
                        'messaging_product': 'whatsapp',
                        'metadata': {'display_phone_number': '5511000000000', 'phone_number_id': phone_number_id},
                        'statuses': [{'id': wamid, 'status': status, 'timestamp': '1720000000'}],
                    },
                }],
            }],
        }

    def interactive_payload(self, wa_id='5511777777777', wamid='wamid.btn', button_id='b1', title='Sim'):
        payload = self.message_payload(wa_id=wa_id, wamid=wamid)
        payload['entry'][0]['changes'][0]['value']['messages'][0] = {
            'from': wa_id, 'id': wamid, 'timestamp': '1720000000',
            'type': 'interactive',
            'interactive': {'type': 'button_reply', 'button_reply': {'id': button_id, 'title': title}},
        }
        return payload

    def post_webhook(self, payload: dict, signature: str | None = None):
        # assina os MESMOS bytes enviados - HMAC é sobre o body cru
        body = json.dumps(payload).encode()
        headers = {'X-Hub-Signature-256': signature if signature is not None else self.sign(body)}
        return self.client.post(self.URL, data=body, content_type='application/json', headers=headers)

    def test_interactive_button_reply_stores_button_id(self):
        response = self.post_webhook(self.interactive_payload())
        self.assertEqual(response.status_code, 200)
        msg = Message.objects.get(wa_message_id='wamid.btn')
        self.assertEqual(msg.button_id, 'b1')      # id do botão sobrevive pro dispatch_incoming
        self.assertEqual(msg.content, 'Sim')       # título vira o content
        self.assertEqual(msg.message_type, 'text')  # guardado como text (cabe no max_length)
        self.mock_enqueue.assert_called_once()

    def test_message_from_number_without_ninth_digit_uses_existing_contact(self):
        # cadastro gerou 13 dígitos, a Meta responde com 12: mesma conversa, sem duplicata
        before = Contact.objects.count()
        response = self.post_webhook(self.message_payload(wa_id='551199999999', wamid='wamid.sem9'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Contact.objects.count(), before)
        self.assertEqual(Message.objects.get(wa_message_id='wamid.sem9').contact_id, self.contact.pk)

    # GET verificação

    def test_verify_webhook_echoes_challenge_as_plain_text(self):
        response = self.client.get(self.URL, {
            'hub.mode': 'subscribe', 'hub.verify_token': 'verify-1', 'hub.challenge': '12345',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'12345')  # echo exato, sem aspas de JSON

    def test_verify_webhook_wrong_token_returns_403(self):
        response = self.client.get(self.URL, {
            'hub.mode': 'subscribe', 'hub.verify_token': 'token-errado', 'hub.challenge': '12345',
        })
        self.assertEqual(response.status_code, 403)

    def test_verify_webhook_wrong_mode_returns_403(self):
        response = self.client.get(self.URL, {
            'hub.mode': 'unsubscribe', 'hub.verify_token': 'verify-1', 'hub.challenge': '12345',
        })
        self.assertEqual(response.status_code, 403)

    # POST mensagens

    def test_receive_message_creates_contact_and_message(self):
        response = self.post_webhook(self.message_payload())
        self.assertEqual(response.status_code, 200)
        contact = Contact.objects.get(account=self.account, wa_id='5511777777777')
        self.assertEqual(contact.name, 'Novo Cliente')
        message = Message.objects.get(wa_message_id='wamid.novo')
        self.assertEqual(message.contact, contact)
        self.assertEqual(message.direction, 'in')
        self.assertEqual(message.content, 'Quero comprar')

    def test_receive_message_twice_is_idempotent(self):
        # Meta reenvia webhooks - wa_message_id unique segura duplicata
        self.post_webhook(self.message_payload())
        response = self.post_webhook(self.message_payload())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Message.objects.filter(wa_message_id='wamid.novo').count(), 1)

    def test_receive_message_reuses_existing_contact(self):
        payload = self.message_payload(wa_id='5511999999999', wamid='wamid.novo2')
        response = self.post_webhook(payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Contact.objects.filter(account=self.account, wa_id='5511999999999').count(), 1)
        message = Message.objects.get(wa_message_id='wamid.novo2')
        self.assertEqual(message.contact, self.contact)

    def test_invalid_signature_returns_403_and_writes_nothing(self):
        body = json.dumps(self.message_payload()).encode()
        response = self.post_webhook(self.message_payload(), signature=self.sign(body, secret='secret-errado'))
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Message.objects.filter(wa_message_id='wamid.novo').exists())
        self.assertFalse(Contact.objects.filter(wa_id='5511777777777').exists())

    def test_missing_signature_returns_403(self):
        response = self.post_webhook(self.message_payload(), signature='')
        self.assertEqual(response.status_code, 403)

    def test_unknown_phone_number_id_returns_200_silently(self):
        response = self.post_webhook(self.message_payload(phone_number_id='999999'))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Message.objects.filter(wa_message_id='wamid.novo').exists())

    def test_malformed_payload_returns_200(self):
        response = self.post_webhook({})
        self.assertEqual(response.status_code, 200)

    def media_message_payload(self, msg_type: str, media: dict, wamid: str, wa_id='5511777777777'):
        payload = self.message_payload(wa_id=wa_id, wamid=wamid)
        payload['entry'][0]['changes'][0]['value']['messages'][0] = {
            'from': wa_id, 'id': wamid, 'timestamp': '1720000000',
            'type': msg_type, msg_type: media,
        }
        return payload

    def test_unsupported_message_type_is_ignored(self):
        payload = self.media_message_payload('reaction', {'message_id': 'wamid.1', 'emoji': '👍'}, wamid='wamid.react')
        response = self.post_webhook(payload)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Message.objects.filter(wa_message_id='wamid.react').exists())
        self.mock_enqueue.assert_not_called()

    # mídia (imagem/áudio)

    def test_image_message_with_caption_creates_media_message(self):
        payload = self.media_message_payload(
            'image', {'id': 'media-img', 'mime_type': 'image/jpeg', 'caption': 'O que é isso?'}, wamid='wamid.img'
        )
        response = self.post_webhook(payload)
        self.assertEqual(response.status_code, 200)
        message = Message.objects.get(wa_message_id='wamid.img')
        self.assertEqual(message.message_type, 'image')
        self.assertEqual(message.media_id, 'media-img')
        self.assertEqual(message.content, 'O que é isso?')
        self.mock_enqueue.assert_called_once_with(args=(message.pk,), countdown=0)

    def test_image_without_caption_gets_placeholder_content(self):
        # content vazio viraria text block vazio no histórico - Anthropic rejeita
        payload = self.media_message_payload('image', {'id': 'media-img2'}, wamid='wamid.img2')
        self.post_webhook(payload)
        self.assertEqual(Message.objects.get(wa_message_id='wamid.img2').content, '[imagem]')

    def test_audio_message_creates_empty_content_for_transcription(self):
        payload = self.media_message_payload('audio', {'id': 'media-aud', 'mime_type': 'audio/ogg'}, wamid='wamid.aud')
        response = self.post_webhook(payload)
        self.assertEqual(response.status_code, 200)
        message = Message.objects.get(wa_message_id='wamid.aud')
        self.assertEqual(message.message_type, 'audio')
        self.assertEqual(message.media_id, 'media-aud')
        self.assertEqual(message.content, '')  # task preenche com o transcript
        self.mock_enqueue.assert_called_once_with(args=(message.pk,), countdown=0)

    def test_media_without_id_is_ignored(self):
        # payload defensivo: mídia sem id não tem o que baixar - 200 sem crash
        payload = self.media_message_payload('image', {}, wamid='wamid.noid')
        response = self.post_webhook(payload)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Message.objects.filter(wa_message_id='wamid.noid').exists())
        self.mock_enqueue.assert_not_called()

    def test_video_message_creates_media_message_with_placeholder(self):
        payload = self.media_message_payload('video', {'id': 'media-vid', 'mime_type': 'video/mp4'}, wamid='wamid.vid')
        response = self.post_webhook(payload)
        self.assertEqual(response.status_code, 200)
        message = Message.objects.get(wa_message_id='wamid.vid')
        self.assertEqual(message.message_type, 'video')
        self.assertEqual(message.media_id, 'media-vid')
        self.assertEqual(message.content, '[vídeo]')

    def test_document_message_uses_filename_placeholder(self):
        payload = self.media_message_payload(
            'document', {'id': 'media-doc', 'filename': 'orcamento.pdf'}, wamid='wamid.doc'
        )
        self.post_webhook(payload)
        message = Message.objects.get(wa_message_id='wamid.doc')
        self.assertEqual(message.message_type, 'document')
        self.assertEqual(message.content, '[documento: orcamento.pdf]')

    def test_sticker_message_creates_media_message_with_placeholder(self):
        payload = self.media_message_payload(
            'sticker', {'id': 'media-stk', 'mime_type': 'image/webp', 'animated': False}, wamid='wamid.stk-in'
        )
        response = self.post_webhook(payload)
        self.assertEqual(response.status_code, 200)
        message = Message.objects.get(wa_message_id='wamid.stk-in')
        self.assertEqual(message.message_type, 'sticker')
        self.assertEqual(message.media_id, 'media-stk')
        self.assertEqual(message.content, '[figurinha]')

    def outgoing(self, wamid='wamid.out', status='sent', error_detail=''):
        return Message.objects.create(
            contact=self.contact, direction='out', content='oi', wa_message_id=wamid, status=status, error_detail=error_detail
        )

    def test_status_update_changes_message_status(self):
        self.outgoing()
        response = self.post_webhook(self.status_payload(wamid='wamid.out', status='delivered'))
        self.assertEqual(response.status_code, 200)
        message = Message.objects.get(wa_message_id='wamid.out')
        self.assertEqual(message.status, 'delivered')

    def test_late_delivered_does_not_downgrade_read(self):
        message = self.outgoing(status='read')
        self.post_webhook(self.status_payload(wamid='wamid.out', status='delivered'))
        message.refresh_from_db()
        self.assertEqual(message.status, 'read')

    def test_status_advances_in_order(self):
        message = self.outgoing(status='sent')
        self.post_webhook(self.status_payload(wamid='wamid.out', status='read'))
        message.refresh_from_db()
        self.assertEqual(message.status, 'read')

    def test_failed_always_overrides_with_error(self):
        message = self.outgoing(status='delivered')
        payload = self.status_payload(wamid='wamid.out', status='failed')
        payload['entry'][0]['changes'][0]['value']['statuses'][0]['errors'] = [{'code': 131049, 'title': 'Não entregue'}]
        self.post_webhook(payload)
        message.refresh_from_db()
        self.assertEqual(message.status, 'failed')
        self.assertEqual(message.error_detail, 'Não entregue')

    def test_failed_without_errors_keeps_previous_detail(self):
        message = self.outgoing(status='sent', error_detail='erro anterior')
        self.post_webhook(self.status_payload(wamid='wamid.out', status='failed'))
        message.refresh_from_db()
        self.assertEqual(message.status, 'failed')
        self.assertEqual(message.error_detail, 'erro anterior')

    def test_status_after_failed_does_not_hide_failure(self):
        message = self.outgoing(status='failed', error_detail='x')
        self.post_webhook(self.status_payload(wamid='wamid.out', status='delivered'))
        message.refresh_from_db()
        self.assertEqual(message.status, 'failed')

    @patch('apps.chats.events.publish')
    def test_delivery_failure_moves_campaign_count_and_notifies(self, mock_pub):
        campaign = Campaign.objects.create(account=self.account, name='c', template_name='t', template_language='pt_BR', total=1, sent=1)
        message = self.outgoing()
        message.campaign = campaign
        message.save()
        for _ in range(2):  # Meta reenvia o mesmo status: conta uma vez só
            self.post_webhook(self.status_payload(wamid='wamid.out', status='failed'))
        campaign.refresh_from_db()
        self.assertEqual((campaign.sent, campaign.failed), (0, 1))
        mock_pub.assert_called_once_with(self.company.pk, {'type': 'message_status', 'contact_id': self.contact.pk})

    def test_unknown_status_is_ignored(self):
        message = self.outgoing(status='sent')
        self.post_webhook(self.status_payload(wamid='wamid.out', status='deleted'))
        message.refresh_from_db()
        self.assertEqual(message.status, 'sent')

    # vários entry/changes numa entrega só

    def test_processes_every_entry(self):
        payload = self.message_payload(wa_id='5511777777777', wamid='wamid.e1')
        second = self.message_payload(wa_id='5511666666666', wamid='wamid.e2')
        payload['entry'].append(second['entry'][0])
        response = self.post_webhook(payload)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Message.objects.filter(wa_message_id='wamid.e1').exists())
        self.assertTrue(Message.objects.filter(wa_message_id='wamid.e2').exists())
        self.assertEqual(self.mock_enqueue.call_count, 2)

    def test_processes_every_change(self):
        self.outgoing()
        payload = self.message_payload(wamid='wamid.c1')
        payload['entry'][0]['changes'].append(self.status_payload(wamid='wamid.out', status='read')['entry'][0]['changes'][0])
        self.post_webhook(payload)
        self.assertTrue(Message.objects.filter(wa_message_id='wamid.c1').exists())
        self.assertEqual(Message.objects.get(wa_message_id='wamid.out').status, 'read')

    def test_signature_checked_against_every_account_in_payload(self):
        # entry de outra account assinado com o secret da nossa: nada é processado
        payload = self.message_payload(wamid='wamid.nosso')
        payload['entry'].append(self.message_payload(phone_number_id='222222', wamid='wamid.alheio')['entry'][0])
        response = self.post_webhook(payload)
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Message.objects.filter(wa_message_id__in=['wamid.nosso', 'wamid.alheio']).exists())

    def test_invalid_json_returns_400(self):
        response = self.client.post(self.URL, data=b'{nao e json', content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_non_object_json_returns_200(self):
        response = self.client.post(self.URL, data=b'[1, 2]', content_type='application/json')
        self.assertEqual(response.status_code, 200)

    # tipos de mensagem

    def with_message(self, message: dict, wamid='wamid.tipo'):
        payload = self.message_payload(wamid=wamid)
        payload['entry'][0]['changes'][0]['value']['messages'][0] = {
            'from': '5511777777777', 'id': wamid, 'timestamp': '1720000000', **message,
        }
        return payload

    def test_template_button_reply_reaches_ai(self):
        payload = self.with_message({'type': 'button', 'button': {'text': 'Quero saber mais', 'payload': 'MAIS_INFO'}})
        self.post_webhook(payload)
        msg = Message.objects.get(wa_message_id='wamid.tipo')
        self.assertEqual(msg.content, 'Quero saber mais')
        self.assertEqual(msg.button_id, 'MAIS_INFO')
        self.assertIsNone(msg.campaign_id)  # sem context.id de campanha: segue pra IA normal
        self.mock_enqueue.assert_called_once()

    def test_template_button_without_payload_uses_text(self):
        self.post_webhook(self.with_message({'type': 'button', 'button': {'text': 'Confirmar'}}))
        self.assertEqual(Message.objects.get(wa_message_id='wamid.tipo').button_id, 'Confirmar')

    def test_template_button_reply_inherits_campaign(self):
        contact = Contact.objects.create(account=self.account, wa_id='5511777777777')
        campaign = Campaign.objects.create(account=self.account, name='Consultas', template_name='t', template_language='pt_BR')
        Message.objects.create(contact=contact, direction='out', content='Confirma?', wa_message_id='wamid.template', campaign=campaign)
        self.post_webhook(self.with_message({
            'type': 'button', 'button': {'text': 'Confirmar', 'payload': 'Confirmar'}, 'context': {'from': '5511000000000', 'id': 'wamid.template'},
        }))
        msg = Message.objects.get(wa_message_id='wamid.tipo')
        self.assertEqual(msg.campaign_id, campaign.pk)
        self.assertEqual(msg.button_id, 'Confirmar')

    def test_reply_to_other_contacts_message_does_not_inherit_campaign(self):
        stranger = Contact.objects.create(account=self.account, wa_id='5511666666666')
        campaign = Campaign.objects.create(account=self.account, name='Consultas', template_name='t', template_language='pt_BR')
        Message.objects.create(contact=stranger, direction='out', content='x', wa_message_id='wamid.template', campaign=campaign)
        self.post_webhook(self.with_message({
            'type': 'button', 'button': {'text': 'Confirmar'}, 'context': {'id': 'wamid.template'},
        }))
        self.assertIsNone(Message.objects.get(wa_message_id='wamid.tipo').campaign_id)

    def test_list_reply_stores_title_and_id(self):
        payload = self.with_message({
            'type': 'interactive',
            'interactive': {'type': 'list_reply', 'list_reply': {'id': 'opt-2', 'title': 'Plano anual'}},
        })
        self.post_webhook(payload)
        msg = Message.objects.get(wa_message_id='wamid.tipo')
        self.assertEqual(msg.content, 'Plano anual')
        self.assertEqual(msg.button_id, 'opt-2')

    def test_location_is_stored_as_placeholder(self):
        payload = self.with_message({'type': 'location', 'location': {'latitude': -23.5, 'longitude': -46.6, 'name': 'Escritório'}})
        self.post_webhook(payload)
        self.assertEqual(Message.objects.get(wa_message_id='wamid.tipo').content, '[localização: Escritório]')

    def test_location_without_name_uses_coordinates(self):
        payload = self.with_message({'type': 'location', 'location': {'latitude': -23.5, 'longitude': -46.6}})
        self.post_webhook(payload)
        self.assertEqual(Message.objects.get(wa_message_id='wamid.tipo').content, '[localização: -23.5, -46.6]')

    def test_shared_contact_is_stored_as_placeholder(self):
        payload = self.with_message({'type': 'contacts', 'contacts': [{'name': {'formatted_name': 'Maria Souza'}}]})
        self.post_webhook(payload)
        self.assertEqual(Message.objects.get(wa_message_id='wamid.tipo').content, '[contato: Maria Souza]')

    def test_unsupported_type_is_ignored(self):
        payload = self.with_message({'type': 'reaction', 'reaction': {'emoji': '👍', 'message_id': 'wamid.1'}})
        response = self.post_webhook(payload)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Message.objects.filter(wa_message_id='wamid.tipo').exists())
        self.mock_enqueue.assert_not_called()

    # disparo da task de resposta

    def test_new_message_enqueues_processing_task(self):
        response = self.post_webhook(self.message_payload())
        self.assertEqual(response.status_code, 200)
        record = Message.objects.get(wa_message_id='wamid.novo')
        self.mock_enqueue.assert_called_once_with(args=(record.pk,), countdown=0)

    def test_enqueue_countdown_uses_agent_response_delay(self):
        # debounce: countdown vem da config do agente
        Agent.objects.filter(pk=self.agent.pk).update(response_delay=8)
        self.post_webhook(self.message_payload())
        record = Message.objects.get(wa_message_id='wamid.novo')
        self.mock_enqueue.assert_called_once_with(args=(record.pk,), countdown=8)

    def test_resent_webhook_does_not_enqueue_twice(self):
        # Meta reenvia webhooks: gravação já era idempotente, resposta também precisa ser
        self.post_webhook(self.message_payload())
        self.post_webhook(self.message_payload())
        self.assertEqual(self.mock_enqueue.call_count, 1)

    def test_status_webhook_does_not_enqueue(self):
        self.post_webhook(self.status_payload())
        self.mock_enqueue.assert_not_called()

    # regressão: visto em produção - status da Meta com contacts sem 'profile';
    # qualquer não-200 vira retry storm e pode desativar o webhook

    def test_contacts_without_profile_returns_200(self):
        payload = self.status_payload()
        payload['entry'][0]['changes'][0]['value']['contacts'] = [{'wa_id': '5511999999999'}]
        response = self.post_webhook(payload)
        self.assertEqual(response.status_code, 200)

    def test_message_with_contact_without_profile_creates_contact_with_empty_name(self):
        payload = self.message_payload(wa_id='5511666666666', wamid='wamid.semprofile')
        payload['entry'][0]['changes'][0]['value']['contacts'] = [{'wa_id': '5511666666666'}]
        response = self.post_webhook(payload)
        self.assertEqual(response.status_code, 200)
        contact = Contact.objects.get(wa_id='5511666666666')
        self.assertEqual(contact.name, '')


class SendTextTests(BaseChatsTestCase):
    @patch('apps.chats.whatsapp.httpx.post')
    def test_send_text_calls_graph_api_and_returns_wamid(self, mock_post):
        mock_post.return_value.json.return_value = {'messages': [{'id': 'wamid.enviado'}]}

        wamid = whatsapp.send_text(self.account, '5511999999999', 'Olá!')

        self.assertEqual(wamid, 'wamid.enviado')
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], f'{whatsapp.GRAPH_URL}/111111/messages')
        # access_token decifrado do EncryptedTextField vai no header
        self.assertEqual(kwargs['headers']['Authorization'], 'Bearer EAAG-meu-token-secreto')
        self.assertEqual(kwargs['json'], {
            'messaging_product': 'whatsapp',
            'to': '5511999999999',
            'type': 'text',
            'text': {'body': 'Olá!'},
        })
        self.assertEqual(kwargs['timeout'], 10)

    @patch('apps.chats.whatsapp.httpx.post')
    def test_send_text_raises_on_api_error(self, mock_post):
        # erro da Meta (token expirado, janela 24h) sobe cru - caller decide retry/failed
        mock_post.return_value.raise_for_status.side_effect = httpx.HTTPStatusError(
            'Bad Request', request=MagicMock(), response=MagicMock()
        )
        with self.assertRaises(httpx.HTTPStatusError):
            whatsapp.send_text(self.account, '5511999999999', 'Olá!')

    def test_record_outgoing_message(self):
        message = services.record_outgoing_message(self.contact, 'wamid.out1', 'Resposta da IA')
        self.assertEqual(message.contact, self.contact)
        self.assertEqual(message.direction, 'out')
        self.assertEqual(message.status, 'sent')
        self.assertEqual(message.wa_message_id, 'wamid.out1')


class DownloadMediaTests(BaseChatsTestCase):
    @patch('apps.chats.whatsapp.httpx.get')
    def test_download_media_two_authenticated_gets(self, mock_get):
        meta_response = MagicMock()
        meta_response.json.return_value = {'url': 'https://lookaside.fbsbx.com/whatsapp/media-x', 'mime_type': 'image/jpeg'}
        file_response = MagicMock(content=b'bytes-da-imagem')
        mock_get.side_effect = [meta_response, file_response]

        content, mime_type = whatsapp.download_media(self.account, 'media-1')

        self.assertEqual(content, b'bytes-da-imagem')
        self.assertEqual(mime_type, 'image/jpeg')
        first, second = mock_get.call_args_list
        self.assertEqual(first.args[0], f'{whatsapp.GRAPH_URL}/media-1')
        self.assertEqual(second.args[0], 'https://lookaside.fbsbx.com/whatsapp/media-x')
        # a URL temporária também exige o Bearer - sem ele a Meta devolve erro
        for call in (first, second):
            self.assertEqual(call.kwargs['headers']['Authorization'], 'Bearer EAAG-meu-token-secreto')

    @patch('apps.chats.whatsapp.httpx.get')
    def test_download_media_raises_on_api_error(self, mock_get):
        # media_id expirado (30 dias) ou token inválido - sobe cru, task faz retry
        mock_get.return_value.raise_for_status.side_effect = httpx.HTTPStatusError(
            'Bad Request', request=MagicMock(), response=MagicMock()
        )
        with self.assertRaises(httpx.HTTPStatusError):
            whatsapp.download_media(self.account, 'media-1')


class SendMediaTests(BaseChatsTestCase):
    @patch('apps.chats.whatsapp.httpx.post')
    def test_upload_media_posts_multipart_and_returns_media_id(self, mock_post):
        mock_post.return_value.json.return_value = {'id': 'media-novo'}

        media_id = whatsapp.upload_media(self.account, b'jpeg-bytes', 'image/jpeg', 'foto.jpg')

        self.assertEqual(media_id, 'media-novo')
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], f'{whatsapp.GRAPH_URL}/111111/media')
        self.assertEqual(kwargs['headers']['Authorization'], 'Bearer EAAG-meu-token-secreto')
        self.assertEqual(kwargs['data'], {'messaging_product': 'whatsapp'})
        self.assertEqual(kwargs['files']['file'], ('foto.jpg', b'jpeg-bytes', 'image/jpeg'))
        self.assertEqual(kwargs['timeout'], 30)  # upload de arquivo demora mais que JSON

    @patch('apps.chats.whatsapp.httpx.post')
    def test_send_media_image_with_caption(self, mock_post):
        # chave do objeto de conteúdo é dinâmica: type='image' → payload['image']
        mock_post.return_value.json.return_value = {'messages': [{'id': 'wamid.media'}]}

        wamid = whatsapp.send_media(self.account, '5511999999999', 'image', 'media-1', caption='Olha isso')

        self.assertEqual(wamid, 'wamid.media')
        self.assertEqual(mock_post.call_args.kwargs['json'], {
            'messaging_product': 'whatsapp',
            'to': '5511999999999',
            'type': 'image',
            'image': {'id': 'media-1', 'caption': 'Olha isso'},
        })

    @patch('apps.chats.whatsapp.httpx.post')
    def test_send_media_audio_ignores_caption(self, mock_post):
        # Cloud API rejeita caption em áudio - função nunca pode mandar
        mock_post.return_value.json.return_value = {'messages': [{'id': 'wamid.aud'}]}
        whatsapp.send_media(self.account, '5511999999999', 'audio', 'media-2', caption='legenda')
        self.assertEqual(mock_post.call_args.kwargs['json']['audio'], {'id': 'media-2'})

    @patch('apps.chats.whatsapp.httpx.post')
    def test_send_media_sticker_ignores_caption(self, mock_post):
        mock_post.return_value.json.return_value = {'messages': [{'id': 'wamid.stk'}]}
        whatsapp.send_media(self.account, '5511999999999', 'sticker', 'media-4', caption='legenda')
        self.assertEqual(mock_post.call_args.kwargs['json']['sticker'], {'id': 'media-4'})

    @patch('apps.chats.whatsapp.httpx.post')
    def test_send_media_document_includes_filename(self, mock_post):
        mock_post.return_value.json.return_value = {'messages': [{'id': 'wamid.doc'}]}
        whatsapp.send_media(
            self.account, '5511999999999', 'document', 'media-3', caption='segue', filename='contrato.pdf'
        )
        self.assertEqual(
            mock_post.call_args.kwargs['json']['document'],
            {'id': 'media-3', 'caption': 'segue', 'filename': 'contrato.pdf'},
        )

    @patch('apps.chats.whatsapp.httpx.post')
    def test_send_media_raises_on_api_error(self, mock_post):
        mock_post.return_value.raise_for_status.side_effect = httpx.HTTPStatusError(
            'Bad Request', request=MagicMock(), response=MagicMock()
        )
        with self.assertRaises(httpx.HTTPStatusError):
            whatsapp.send_media(self.account, '5511999999999', 'image', 'media-1')


class TranscribeAudioTests(BaseChatsTestCase):
    @patch('apps.ai.httpx.post')
    def test_transcribe_audio_calls_groq_and_returns_text(self, mock_post):
        mock_post.return_value.json.return_value = {'text': 'Quero comprar'}

        text = ai.transcribe_audio(b'ogg-bytes', 'audio/ogg; codecs=opus')

        self.assertEqual(text, 'Quero comprar')
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], ai.GROQ_STT_URL)
        # key global da plataforma - STT não é BYOK
        self.assertEqual(kwargs['headers']['Authorization'], f'Bearer {settings.GROQ_API_KEY}')
        self.assertEqual(kwargs['data'], {'model': 'whisper-large-v3-turbo'})
        self.assertEqual(kwargs['files']['file'], ('audio.ogg', b'ogg-bytes', 'audio/ogg; codecs=opus'))

    @patch('apps.ai.httpx.post')
    def test_transcribe_audio_raises_on_api_error(self, mock_post):
        mock_post.return_value.raise_for_status.side_effect = httpx.HTTPStatusError(
            'Rate limited', request=MagicMock(), response=MagicMock()
        )
        with self.assertRaises(httpx.HTTPStatusError):
            ai.transcribe_audio(b'ogg-bytes', 'audio/ogg')


class BuildHistoryTests(BaseChatsTestCase):
    def test_build_history_returns_chronological_role_content(self):
        history = services.build_history(self.contact)
        self.assertEqual(history, [
            {'role': 'user', 'content': 'Oi'},
            {'role': 'assistant', 'content': 'Olá! Como posso ajudar?'},
        ])

    def test_build_history_does_not_leak_other_contacts(self):
        # cross-tenant: histórico é só do contact pedido
        history = services.build_history(self.other_contact)
        self.assertEqual(history, [{'role': 'user', 'content': 'Mensagem alheia'}])

    def test_build_history_keeps_only_latest_messages_in_order(self):
        for i in range(25):
            Message.objects.create(contact=self.contact, direction='in', content=f'msg {i}')
        history = services.build_history(self.contact)
        self.assertEqual(len(history), 20)
        # janela desliza: mais antigas caem, ordem cronológica preservada
        self.assertEqual(history[0]['content'], 'msg 5')
        self.assertEqual(history[-1]['content'], 'msg 24')

    def test_build_history_skips_empty_content(self):
        # áudio sem transcript (STT ainda não rodou ou falhou de vez) não pode
        # virar text block vazio - Anthropic rejeita; vale com e sem exclude_pk
        Message.objects.create(contact=self.contact, direction='in', content='', message_type='audio', media_id='m1')
        for history in (services.build_history(self.contact), services.build_history(self.contact, exclude_pk=99999)):
            self.assertEqual(len(history), 2)
            self.assertNotIn('', [m['content'] for m in history])


class GenerateReplyTests(BaseChatsTestCase):
    @patch('apps.ai.Claude')
    @patch('apps.ai.AgnoAgent')
    def test_generate_reply_builds_agent_and_returns_content(self, mock_agent_cls, mock_claude):
        mock_agent_cls.return_value.run.return_value.content = 'Resposta da IA'

        reply = ai.generate_reply(self.agent, [{'role': 'user', 'content': 'Oi'}], 'Quanto custa?')

        self.assertEqual(reply, 'Resposta da IA')
        kwargs = mock_agent_cls.call_args.kwargs
        self.assertEqual(kwargs['model'], mock_claude.return_value)
        self.assertTrue(kwargs['system_message'].startswith('Você vende.\n\nAgora: '))
        self.assertEqual(len(kwargs['additional_input']), 1)
        self.assertEqual(kwargs['additional_input'][0].role, 'user')
        self.assertEqual(kwargs['additional_input'][0].content, 'Oi')
        mock_agent_cls.return_value.run.assert_called_once_with('Quanto custa?', images=None)

    @patch('apps.ai.Claude')
    @patch('apps.ai.AgnoAgent')
    def test_generate_reply_uses_tenant_key_and_model(self, mock_agent_cls, mock_claude):
        # BYOK: key e modelo vêm do cadastro do tenant, sem default nosso
        mock_agent_cls.return_value.run.return_value.content = 'ok'
        ai.generate_reply(self.agent, [], 'Oi')
        mock_claude.assert_called_once_with(id='claude-opus-4-8', api_key='sk-minha-chave')

    @patch('apps.ai._Responses')
    @patch('apps.ai.AgnoAgent')
    def test_generate_reply_routes_openai_provider(self, mock_agent_cls, mock_openai):
        mock_agent_cls.return_value.run.return_value.content = 'ok'
        credential = Credential.objects.create(company=self.company, provider='openai', api_key='sk-openai')
        agent = Agent.objects.create(
            company=self.company, name='GPT', prompt='...', credential=credential, model='gpt-5.2'
        )
        ai.generate_reply(agent, [], 'Oi')
        mock_openai.assert_called_once_with(id='gpt-5.2', api_key='sk-openai')

    @patch('apps.ai.Claude')
    @patch('apps.ai.AgnoAgent')
    def test_generate_reply_returns_empty_string_when_content_none(self, mock_agent_cls, mock_claude):
        mock_agent_cls.return_value.run.return_value.content = None
        self.assertEqual(ai.generate_reply(self.agent, [], 'Oi'), '')

    @patch('apps.ai.Claude')
    @patch('apps.ai.AgnoAgent')
    def test_generate_reply_passes_image_to_model(self, mock_agent_cls, mock_claude):
        mock_agent_cls.return_value.run.return_value.content = 'É um tênis azul.'

        reply = ai.generate_reply(self.agent, [], '[imagem]', image=b'jpeg-bytes', mime_type='image/jpeg')

        self.assertEqual(reply, 'É um tênis azul.')
        run_kwargs = mock_agent_cls.return_value.run.call_args.kwargs
        self.assertEqual(len(run_kwargs['images']), 1)
        self.assertEqual(run_kwargs['images'][0].content, b'jpeg-bytes')
        self.assertEqual(run_kwargs['images'][0].mime_type, 'image/jpeg')

    # memória de longo prazo

    @patch('apps.ai.Claude')
    @patch('apps.ai.AgnoAgent')
    def test_generate_reply_injects_memory_into_system_message(self, mock_agent_cls, mock_claude):
        mock_agent_cls.return_value.run.return_value.content = 'Oi Matheus!'

        ai.generate_reply(self.agent, [], 'qual meu nome?', memory='Nome: Matheus')

        system = mock_agent_cls.call_args.kwargs['system_message']
        self.assertIn('Você vende.', system)
        self.assertIn('<memory>\nNome: Matheus\n</memory>', system)

    @patch('apps.ai.Claude')
    @patch('apps.ai.AgnoAgent')
    def test_generate_reply_passes_tools_to_agent(self, mock_agent_cls, mock_claude):
        mock_agent_cls.return_value.run.return_value.content = 'ok'

        ai.generate_reply(self.agent, [], 'Oi')
        self.assertIsNone(mock_agent_cls.call_args.kwargs['tools'])

        def fake_tool():
            return 'x'

        ai.generate_reply(self.agent, [], 'Oi', tools=[fake_tool])
        self.assertEqual(mock_agent_cls.call_args.kwargs['tools'], [fake_tool])

    @patch('apps.ai.Claude')
    @patch('apps.ai.AgnoAgent')
    def test_generate_reply_adds_handoff_policy_only_with_handoff_tool(self, mock_agent_cls, mock_claude):
        mock_agent_cls.return_value.run.return_value.content = 'ok'

        def transferir_para_atendente():
            return 'Transferido'

        ai.generate_reply(self.agent, [], 'Oi', tools=[transferir_para_atendente])
        self.assertIn(ai.HANDOFF_PROMPT, mock_agent_cls.call_args.kwargs['system_message'])

        ai.generate_reply(self.agent, [], 'Oi')
        self.assertNotIn(ai.HANDOFF_PROMPT, mock_agent_cls.call_args.kwargs['system_message'])

    @patch('apps.ai.embed_text')
    @patch('apps.ai.Claude')
    @patch('apps.ai.AgnoAgent')
    def test_generate_reply_combines_memory_and_knowledge(self, mock_agent_cls, mock_claude, mock_embed):
        mock_agent_cls.return_value.run.return_value.content = 'ok'
        mock_embed.return_value = [[1.0] + [0.0] * 1535]
        document = Document.objects.create(agent=self.agent, name='faq.txt', content='...', status='ready')
        DocumentChunk.objects.create(document=document, content='Entrega em 3 dias', embedding=[1.0] + [0.0] * 1535)

        ai.generate_reply(self.agent, [], 'qual o prazo?', memory='Nome: Matheus')

        system = mock_agent_cls.call_args.kwargs['system_message']
        self.assertEqual(system.count('Você vende.'), 1)  # prompt do agent não pode duplicar
        self.assertLess(system.index('<memory>'), system.index('<knowledge>'))
        # blocos separados por linha em branco, não colados
        self.assertIn('</memory>\n\nUse as informações', system)

    # split de mensagens

    @patch('apps.ai.Claude')
    @patch('apps.ai.AgnoAgent')
    def test_generate_reply_adds_split_instruction_only_when_enabled(self, mock_agent_cls, mock_claude):
        mock_agent_cls.return_value.run.return_value.content = 'ok'

        ai.generate_reply(self.agent, [], 'Oi')
        self.assertNotIn(ai.SPLIT_PROMPT, mock_agent_cls.call_args.kwargs['system_message'])

        self.agent.split_messages = True
        ai.generate_reply(self.agent, [], 'Oi')
        system = mock_agent_cls.call_args.kwargs['system_message']
        self.assertIn(ai.SPLIT_PROMPT, system)
        self.assertTrue(system.startswith('Você vende.'))  # instrução acrescenta, não substitui

    # erro do provider (ex.: 529 overloaded) - Agno devolve o erro como content em vez de levantar

    @patch('apps.ai.Claude')
    @patch('apps.ai.AgnoAgent')
    def test_generate_reply_raises_when_run_status_error(self, mock_agent_cls, mock_claude):
        run_result = mock_agent_cls.return_value.run.return_value
        run_result.status = RunStatus.error
        run_result.content = "Error code: 529 - {'type': 'overloaded_error'}"

        with self.assertRaises(RuntimeError) as ctx:
            ai.generate_reply(self.agent, [], 'Oi')
        # exceção carrega o erro pro log; task faz retry e o cliente não recebe o texto
        self.assertIn('529', str(ctx.exception))


class UpdateMemoryTests(BaseChatsTestCase):
    @patch('apps.ai.Claude')
    @patch('apps.ai.AgnoAgent')
    def test_update_memory_builds_prompt_and_returns_notes(self, mock_agent_cls, mock_claude):
        mock_agent_cls.return_value.run.return_value.content = 'Nome: Matheus'
        history = [{'role': 'user', 'content': 'meu nome é Matheus'}, {'role': 'assistant', 'content': 'Prazer!'}]

        result = ai.update_memory(self.agent, 'anotações antigas', history)

        self.assertEqual(result, 'Nome: Matheus')
        self.assertEqual(mock_agent_cls.call_args.kwargs['system_message'], ai.MEMORY_PROMPT)
        prompt = mock_agent_cls.return_value.run.call_args.args[0]
        self.assertIn('<anotacoes_atuais>\nanotações antigas\n</anotacoes_atuais>', prompt)
        self.assertIn('user: meu nome é Matheus\nassistant: Prazer!', prompt)

    @patch('apps.ai.Claude')
    @patch('apps.ai.AgnoAgent')
    def test_update_memory_truncates_at_max_chars(self, mock_agent_cls, mock_claude):
        # cap hard contra blob crescendo sem limite
        mock_agent_cls.return_value.run.return_value.content = 'x' * (ai.MEMORY_MAX_CHARS + 500)
        self.assertEqual(len(ai.update_memory(self.agent, '', [])), ai.MEMORY_MAX_CHARS)

    @patch('apps.ai.Claude')
    @patch('apps.ai.AgnoAgent')
    def test_update_memory_none_content_returns_empty(self, mock_agent_cls, mock_claude):
        mock_agent_cls.return_value.run.return_value.content = None
        self.assertEqual(ai.update_memory(self.agent, '', []), '')

    @patch('apps.ai.Claude')
    @patch('apps.ai.AgnoAgent')
    def test_update_memory_raises_when_run_status_error(self, mock_agent_cls, mock_claude):
        # sem o raise, o texto do erro viraria a memória do contato
        run_result = mock_agent_cls.return_value.run.return_value
        run_result.status = RunStatus.error
        run_result.content = 'Error code: 529'

        with self.assertRaises(RuntimeError):
            ai.update_memory(self.agent, 'Nome: Matheus', [])


class ProcessIncomingMessageTests(BaseChatsTestCase):
    def setUp(self):
        super().setUp()
        # sem o mock, o fim feliz da task publica update_contact_memory no broker real
        patcher = patch('apps.chats.tasks.update_contact_memory.delay')
        self.mock_memory_enqueue = patcher.start()
        self.addCleanup(patcher.stop)
    @patch('apps.chats.whatsapp.send_text')
    @patch('apps.ai.generate_reply')
    def test_task_generates_sends_and_records_reply(self, mock_generate, mock_send):
        mock_generate.return_value = 'Resposta gerada'
        mock_send.return_value = 'wamid.resposta'
        incoming = Message.objects.get(wa_message_id='wamid.1')

        tasks.process_incoming_message(incoming.pk)

        # histórico exclui a mensagem sendo respondida - sobra só a out antiga
        mock_generate.assert_called_once_with(
            self.agent, [{'role': 'assistant', 'content': 'Olá! Como posso ajudar?'}], 'Oi',
            image=None, mime_type='', memory='', tools=ANY, variables=ANY,
        )
        mock_send.assert_called_once_with(self.account, '5511999999999', 'Resposta gerada')
        reply = Message.objects.get(wa_message_id='wamid.resposta')
        self.assertEqual(reply.direction, 'out')
        self.assertEqual(reply.content, 'Resposta gerada')
        self.assertEqual(reply.contact, self.contact)
        self.assertFalse(reply.from_human)  # resposta da IA, não do atendente

    @patch('apps.chats.tasks.process_incoming_message.apply_async')
    @patch('apps.chats.whatsapp.send_text')
    @patch('apps.ai.generate_reply')
    def test_outside_ai_hours_defers_to_opening(self, mock_generate, mock_send, mock_enqueue):
        Agent.objects.filter(pk=self.agent.pk).update(ai_schedule=[[['09:00', '18:00']]] * 7)
        incoming = Message.objects.create(contact=self.contact, direction='in', content='Oi?', wa_message_id='wamid.noite')

        with patch('apps.chats.tasks.timezone.now', return_value=datetime(2026, 10, 5, 23, tzinfo=SP)):
            tasks.process_incoming_message(incoming.pk)

        mock_generate.assert_not_called()
        mock_enqueue.assert_called_once_with(args=(incoming.pk,), kwargs={'deferred': True}, eta=datetime(2026, 10, 6, 9, tzinfo=SP))

    @patch('apps.chats.tasks.process_incoming_message.apply_async')
    @patch('apps.chats.whatsapp.send_text', return_value='wamid.ia')
    @patch('apps.ai.generate_reply', return_value='Oi, aqui é a IA')
    def test_campaign_lead_has_no_catchup_reply(self, mock_generate, mock_send, mock_enqueue):
        Agent.objects.filter(pk=self.agent.pk).update(ai_schedule=[[['18:00', '00:00']]] * 7)
        Contact.objects.filter(pk=self.contact.pk).update(ai_no_catchup=True)
        afternoon = Message.objects.create(contact=self.contact, direction='in', content='Tenho interesse', wa_message_id='wamid.tarde')
        with patch('apps.chats.tasks.timezone.now', return_value=datetime(2026, 10, 5, 15, tzinfo=SP)):
            tasks.process_incoming_message(afternoon.pk)
        # 15h, fora do horário da IA: fica pra equipe, nada agendado pras 18h
        mock_enqueue.assert_not_called()
        mock_generate.assert_not_called()

        night = Message.objects.create(contact=self.contact, direction='in', content='Ainda tá aí?', wa_message_id='wamid.noite2')
        with patch('apps.chats.tasks.timezone.now', return_value=datetime(2026, 10, 5, 20, tzinfo=SP)):
            tasks.process_incoming_message(night.pk)
        # 20h, dentro do horário: responde, e o contato volta ao normal
        mock_send.assert_called_once()
        self.contact.refresh_from_db()
        self.assertFalse(self.contact.ai_no_catchup)

    @patch('apps.chats.whatsapp.send_text')
    @patch('apps.ai.generate_reply')
    def test_deferred_run_skips_campaign_lead(self, mock_generate, mock_send):
        Contact.objects.filter(pk=self.contact.pk).update(ai_no_catchup=True)
        incoming = Message.objects.create(contact=self.contact, direction='in', content='Oi?', wa_message_id='wamid.agendada')
        tasks.process_incoming_message(incoming.pk, deferred=True)
        mock_generate.assert_not_called()
        mock_send.assert_not_called()

    @patch('apps.automations.services.dispatch_incoming')
    @patch('apps.chats.whatsapp.send_text')
    @patch('apps.ai.generate_reply')
    def test_deferred_run_replies_without_running_flows_again(self, mock_generate, mock_send, mock_dispatch):
        mock_generate.return_value = 'Bom dia'
        mock_send.return_value = 'wamid.manha'
        incoming = Message.objects.create(contact=self.contact, direction='in', content='Oi?', wa_message_id='wamid.noite')

        tasks.process_incoming_message(incoming.pk, deferred=True)

        mock_dispatch.assert_not_called()
        mock_send.assert_called_once()

    @patch('apps.chats.whatsapp.send_text')
    @patch('apps.ai.generate_reply')
    def test_deferred_run_skips_when_someone_already_replied(self, mock_generate, mock_send):
        incoming = Message.objects.create(contact=self.contact, direction='in', content='Oi?', wa_message_id='wamid.noite')
        # atendente respondeu antes de a IA abrir (e a pausa dele já expirou)
        Message.objects.create(contact=self.contact, direction='out', content='Já te ajudo', from_human=True, wa_message_id='wamid.humano')

        tasks.process_incoming_message(incoming.pk, deferred=True)

        mock_generate.assert_not_called()
        mock_send.assert_not_called()

    @patch('apps.chats.whatsapp.send_text')
    @patch('apps.ai.generate_reply')
    def test_task_with_unknown_message_id_is_silent(self, mock_generate, mock_send):
        tasks.process_incoming_message(99999)
        mock_generate.assert_not_called()
        mock_send.assert_not_called()

    # nota: em chamada direta (fora de worker) self.retry re-levanta a exceção
    # original - o reagendamento em si é plumbing do Celery, não se testa aqui

    @patch('apps.chats.whatsapp.send_text')
    @patch('apps.ai.generate_reply')
    def test_task_ai_failure_raises_and_sends_nothing(self, mock_generate, mock_send):
        mock_generate.side_effect = RuntimeError('provider down')
        incoming = Message.objects.get(wa_message_id='wamid.1')
        out_count = Message.objects.filter(direction='out').count()

        with self.assertRaises(RuntimeError):
            tasks.process_incoming_message(incoming.pk)

        mock_send.assert_not_called()
        self.assertEqual(Message.objects.filter(direction='out').count(), out_count)

    @patch('apps.chats.whatsapp.send_text')
    @patch('apps.ai.generate_reply')
    def test_task_send_failure_raises_without_recording(self, mock_generate, mock_send):
        # falha no envio: exceção sobe sem gravar out fantasma no banco
        mock_generate.return_value = 'Resposta'
        mock_send.side_effect = httpx.HTTPStatusError('Bad Request', request=MagicMock(), response=MagicMock())
        incoming = Message.objects.get(wa_message_id='wamid.1')
        out_count = Message.objects.filter(direction='out').count()

        with self.assertRaises(httpx.HTTPStatusError):
            tasks.process_incoming_message(incoming.pk)

        self.assertEqual(Message.objects.filter(direction='out').count(), out_count)

    # envio parcial e pausa durante a geração

    @patch('apps.chats.tasks.time.sleep')
    @patch('apps.chats.whatsapp.send_text')
    @patch('apps.ai.generate_reply')
    def test_second_part_failure_keeps_first_recorded(self, mock_generate, mock_send, mock_sleep):
        self.agent.split_messages = True
        self.agent.save(update_fields=['split_messages'])
        mock_generate.return_value = 'Parte um\n\nParte dois'
        mock_send.side_effect = ['wamid.p1', httpx.ConnectError('caiu')]
        incoming = Message.objects.get(wa_message_id='wamid.1')

        with self.assertRaises(httpx.ConnectError):
            tasks.process_incoming_message(incoming.pk)

        self.assertEqual(Message.objects.get(wa_message_id='wamid.p1').content, 'Parte um')

    @patch('apps.chats.tasks.time.sleep')
    @patch('apps.chats.whatsapp.send_text')
    @patch('apps.ai.generate_reply')
    def test_second_part_failure_retries_with_only_the_rest(self, mock_generate, mock_send, mock_sleep):
        self.agent.split_messages = True
        self.agent.save(update_fields=['split_messages'])
        mock_generate.return_value = 'Parte um\n\nParte dois\n\nParte três'
        mock_send.side_effect = ['wamid.p1', httpx.ConnectError('caiu')]
        incoming = Message.objects.get(wa_message_id='wamid.1')

        with patch.object(tasks.process_incoming_message, 'retry', side_effect=RuntimeError('retry')) as mock_retry:
            with self.assertRaises(RuntimeError):
                tasks.process_incoming_message(incoming.pk)

        self.assertEqual(mock_retry.call_args.kwargs['args'], (incoming.pk,))
        self.assertEqual(
            mock_retry.call_args.kwargs['kwargs'], {'deferred': False, 'pending': ['Parte dois', 'Parte três'], 'handed_off': False},
        )

    @patch('apps.chats.tasks.time.sleep')
    @patch('apps.chats.whatsapp.send_text')
    @patch('apps.ai.generate_reply')
    def test_retry_with_pending_sends_the_rest_without_generating(self, mock_generate, mock_send, mock_sleep):
        mock_send.side_effect = ['wamid.p2', 'wamid.p3']
        incoming = Message.objects.get(wa_message_id='wamid.1')

        tasks.process_incoming_message(incoming.pk, pending=['Parte dois', 'Parte três'])

        mock_generate.assert_not_called()
        self.assertEqual([c.args[2] for c in mock_send.call_args_list], ['Parte dois', 'Parte três'])
        self.assertTrue(Message.objects.filter(wa_message_id='wamid.p3', content='Parte três').exists())
        self.mock_memory_enqueue.assert_called_once_with(self.contact.pk)

    @patch('apps.chats.whatsapp.send_text')
    @patch('apps.ai.generate_reply')
    def test_retry_after_partial_send_does_not_resend(self, mock_generate, mock_send):
        incoming = Message.objects.create(contact=self.contact, direction='in', content='Oi de novo', wa_message_id='wamid.novo')
        services.record_outgoing_message(self.contact, 'wamid.p1', 'Parte um')  # saiu antes da falha

        tasks.process_incoming_message.apply(args=(incoming.pk,), retries=1)

        mock_generate.assert_not_called()
        mock_send.assert_not_called()

    @patch('apps.chats.whatsapp.send_text')
    @patch('apps.ai.generate_reply')
    def test_retry_without_previous_send_still_replies(self, mock_generate, mock_send):
        # falhou antes de enviar qualquer coisa (ex.: provider fora): o retry responde normalmente
        mock_generate.return_value = 'Resposta'
        mock_send.return_value = 'wamid.r'
        incoming = Message.objects.create(contact=self.contact, direction='in', content='Oi de novo', wa_message_id='wamid.novo')

        tasks.process_incoming_message.apply(args=(incoming.pk,), retries=1)

        mock_send.assert_called_once()

    @patch('apps.chats.whatsapp.send_text')
    @patch('apps.ai.generate_reply')
    def test_pause_during_generation_blocks_send(self, mock_generate, mock_send):
        def pause_meanwhile(*args, **kwargs):
            Contact.objects.filter(pk=self.contact.pk).update(ai_paused=True)  # atendente assumiu
            return 'Resposta atrasada'

        mock_generate.side_effect = pause_meanwhile
        incoming = Message.objects.get(wa_message_id='wamid.1')
        out_count = Message.objects.filter(direction='out').count()

        tasks.process_incoming_message(incoming.pk)

        mock_send.assert_not_called()
        self.assertEqual(Message.objects.filter(direction='out').count(), out_count)

    @patch('apps.chats.whatsapp.send_text')
    @patch('apps.ai.generate_reply')
    def test_handoff_tool_still_sends_farewell(self, mock_generate, mock_send):
        # a pausa da própria ferramenta não bloqueia a mensagem que avisa da transferência
        def call_handoff(*args, tools, **kwargs):
            next(t for t in tools if t.__name__ == 'transferir_para_atendente')(motivo='cliente pediu humano')
            return 'Vou te passar para um atendente.'

        mock_generate.side_effect = call_handoff
        mock_send.return_value = 'wamid.tchau'
        incoming = Message.objects.get(wa_message_id='wamid.1')

        tasks.process_incoming_message(incoming.pk)

        mock_send.assert_called_once_with(self.account, '5511999999999', 'Vou te passar para um atendente.')
        self.contact.refresh_from_db()
        self.assertTrue(self.contact.ai_paused)

    @patch('apps.chats.tasks.time.sleep')
    @patch('apps.chats.whatsapp.send_text')
    @patch('apps.ai.generate_reply')
    def test_pause_between_parts_stops_remaining(self, mock_generate, mock_send, mock_sleep):
        self.agent.split_messages = True
        self.agent.save(update_fields=['split_messages'])
        mock_generate.return_value = 'Parte um\n\nParte dois'
        mock_send.return_value = 'wamid.p1'
        mock_sleep.side_effect = lambda _: Contact.objects.filter(pk=self.contact.pk).update(ai_paused=True)
        incoming = Message.objects.get(wa_message_id='wamid.1')

        tasks.process_incoming_message(incoming.pk)

        mock_send.assert_called_once_with(self.account, '5511999999999', 'Parte um')

    # split de mensagens

    @patch('apps.chats.tasks.time.sleep')
    @patch('apps.chats.whatsapp.send_text')
    @patch('apps.ai.generate_reply')
    def test_task_splits_reply_into_multiple_messages(self, mock_generate, mock_send, mock_sleep):
        self.agent.split_messages = True
        self.agent.save(update_fields=['split_messages'])
        mock_generate.return_value = 'Oi!\n\nTudo bem com você?'
        mock_send.side_effect = ['wamid.p1', 'wamid.p2']
        incoming = Message.objects.get(wa_message_id='wamid.1')

        tasks.process_incoming_message(incoming.pk)

        self.assertEqual(mock_send.call_args_list, [
            call(self.account, '5511999999999', 'Oi!'),
            call(self.account, '5511999999999', 'Tudo bem com você?'),
        ])
        parts = Message.objects.filter(direction='out', wa_message_id__in=['wamid.p1', 'wamid.p2']).order_by('pk')
        self.assertEqual([m.content for m in parts], ['Oi!', 'Tudo bem com você?'])
        # pausa entre blocos, nunca antes do primeiro
        self.assertEqual(mock_sleep.call_count, 1)

    @patch('apps.chats.whatsapp.send_text')
    @patch('apps.ai.generate_reply')
    def test_task_split_disabled_sends_single_message(self, mock_generate, mock_send):
        # default off: resposta com parágrafos continua indo em bloco único
        mock_generate.return_value = 'Oi!\n\nTudo bem com você?'
        mock_send.return_value = 'wamid.unico'
        incoming = Message.objects.get(wa_message_id='wamid.1')

        tasks.process_incoming_message(incoming.pk)

        mock_send.assert_called_once_with(self.account, '5511999999999', 'Oi!\n\nTudo bem com você?')
        self.assertEqual(Message.objects.get(wa_message_id='wamid.unico').content, 'Oi!\n\nTudo bem com você?')

    @patch('apps.chats.tasks.time.sleep')
    @patch('apps.chats.whatsapp.send_text')
    @patch('apps.ai.generate_reply')
    def test_task_split_skips_empty_blocks(self, mock_generate, mock_send, mock_sleep):
        # linhas em branco extras da IA não podem virar mensagem vazia (Meta rejeita body vazio)
        self.agent.split_messages = True
        self.agent.save(update_fields=['split_messages'])
        mock_generate.return_value = 'Oi!\n\n\n\n   \n\nTudo bem?'
        mock_send.side_effect = ['wamid.s1', 'wamid.s2']
        incoming = Message.objects.get(wa_message_id='wamid.1')

        tasks.process_incoming_message(incoming.pk)

        self.assertEqual(mock_send.call_count, 2)
        self.assertEqual(
            [args.args[2] for args in mock_send.call_args_list],
            ['Oi!', 'Tudo bem?'],
        )

    # mídia

    @patch('apps.chats.whatsapp.send_text')
    @patch('apps.chats.whatsapp.download_media')
    @patch('apps.ai.generate_reply')
    def test_task_downloads_image_and_passes_to_ai(self, mock_generate, mock_download, mock_send):
        mock_download.return_value = (b'jpeg-bytes', 'image/jpeg')
        mock_generate.return_value = 'É um tênis.'
        mock_send.return_value = 'wamid.resp-img'
        incoming = Message.objects.create(
            contact=self.contact, direction='in', content='[imagem]',
            message_type='image', media_id='media-img', wa_message_id='wamid.img-task',
        )

        tasks.process_incoming_message(incoming.pk)

        mock_download.assert_called_once_with(self.account, 'media-img')
        mock_generate.assert_called_once_with(
            self.agent,
            [{'role': 'user', 'content': 'Oi'}, {'role': 'assistant', 'content': 'Olá! Como posso ajudar?'}],
            '[imagem]', image=b'jpeg-bytes', mime_type='image/jpeg', memory='', tools=ANY, variables=ANY,
        )
        self.assertTrue(Message.objects.filter(wa_message_id='wamid.resp-img', direction='out').exists())

    @patch('apps.chats.whatsapp.send_text')
    @patch('apps.ai.transcribe_audio')
    @patch('apps.chats.whatsapp.download_media')
    @patch('apps.ai.generate_reply')
    def test_task_transcribes_audio_and_saves_transcript(self, mock_generate, mock_download, mock_transcribe, mock_send):
        mock_download.return_value = (b'ogg-bytes', 'audio/ogg')
        mock_transcribe.return_value = 'Quero comprar por áudio'
        mock_generate.return_value = 'Claro!'
        mock_send.return_value = 'wamid.resp-aud'
        incoming = Message.objects.create(
            contact=self.contact, direction='in', content='',
            message_type='audio', media_id='media-aud', wa_message_id='wamid.aud-task',
        )

        tasks.process_incoming_message(incoming.pk)

        mock_download.assert_called_once_with(self.account, 'media-aud')
        mock_transcribe.assert_called_once_with(b'ogg-bytes', 'audio/ogg')
        # transcript persiste no content - histórico das próximas mensagens fica coerente
        incoming.refresh_from_db()
        self.assertEqual(incoming.content, 'Quero comprar por áudio')
        # IA recebe transcript como texto puro, sem imagem
        self.assertEqual(mock_generate.call_args.args[2], 'Quero comprar por áudio')
        self.assertEqual(mock_generate.call_args.kwargs, {'image': None, 'mime_type': '', 'memory': '', 'tools': ANY, 'variables': ANY})

    @patch('apps.chats.whatsapp.send_text')
    @patch('apps.ai.transcribe_audio')
    @patch('apps.chats.whatsapp.download_media')
    @patch('apps.ai.generate_reply')
    def test_task_retry_does_not_retranscribe_audio(self, mock_generate, mock_download, mock_transcribe, mock_send):
        # retry após falha pós-STT: content já preenchido pula download + Groq
        mock_generate.return_value = 'ok'
        mock_send.return_value = 'wamid.resp-retry'
        incoming = Message.objects.create(
            contact=self.contact, direction='in', content='já transcrito',
            message_type='audio', media_id='media-aud2', wa_message_id='wamid.aud-retry',
        )

        tasks.process_incoming_message(incoming.pk)

        mock_download.assert_not_called()
        mock_transcribe.assert_not_called()
        self.assertEqual(mock_generate.call_args.args[2], 'já transcrito')

    # debounce: só a task da última mensagem recebida responde

    @patch('apps.chats.whatsapp.send_text')
    @patch('apps.ai.generate_reply')
    def test_task_aborts_when_newer_incoming_message_exists(self, mock_generate, mock_send):
        # a task da mensagem mais nova responde por todas; guard olha só direction='in'
        # (out mais nova não abortar é coberto por test_task_generates_sends_and_records_reply,
        # onde wamid.1 tem uma out posterior)
        incoming = Message.objects.get(wa_message_id='wamid.1')
        Message.objects.create(contact=self.contact, direction='in', content='tudo bem?', wa_message_id='wamid.newer')
        out_count = Message.objects.filter(direction='out').count()

        tasks.process_incoming_message(incoming.pk)

        mock_generate.assert_not_called()
        mock_send.assert_not_called()
        self.assertEqual(Message.objects.filter(direction='out').count(), out_count)

    @patch('apps.chats.whatsapp.send_text')
    @patch('apps.ai.transcribe_audio')
    @patch('apps.chats.whatsapp.download_media')
    @patch('apps.ai.generate_reply')
    def test_task_aborted_audio_still_persists_transcript(self, mock_generate, mock_download, mock_transcribe, mock_send):
        # transcrição roda antes do guard: transcript entra no histórico da task final
        mock_download.return_value = (b'ogg-bytes', 'audio/ogg')
        mock_transcribe.return_value = 'primeira parte por áudio'
        audio = Message.objects.create(
            contact=self.contact, direction='in', content='',
            message_type='audio', media_id='media-aud3', wa_message_id='wamid.aud-abort',
        )
        Message.objects.create(contact=self.contact, direction='in', content='segunda parte', wa_message_id='wamid.after-audio')

        tasks.process_incoming_message(audio.pk)

        audio.refresh_from_db()
        self.assertEqual(audio.content, 'primeira parte por áudio')
        mock_generate.assert_not_called()
        mock_send.assert_not_called()

    @patch('apps.chats.whatsapp.send_text')
    @patch('apps.ai.generate_reply')
    def test_task_of_last_message_replies_with_accumulated_history(self, mock_generate, mock_send):
        # mensagens acumuladas viram turnos 'user' consecutivos no histórico
        mock_generate.return_value = 'Oi! Tudo ótimo.'
        mock_send.return_value = 'wamid.resp-debounce'
        Message.objects.create(contact=self.contact, direction='in', content='oi', wa_message_id='wamid.parte1')
        last = Message.objects.create(contact=self.contact, direction='in', content='tudo bem?', wa_message_id='wamid.parte2')

        tasks.process_incoming_message(last.pk)

        mock_generate.assert_called_once_with(
            self.agent,
            [
                {'role': 'user', 'content': 'Oi'},
                {'role': 'assistant', 'content': 'Olá! Como posso ajudar?'},
                {'role': 'user', 'content': 'oi'},
            ],
            'tudo bem?', image=None, mime_type='', memory='', tools=ANY, variables=ANY,
        )
        self.assertTrue(Message.objects.filter(wa_message_id='wamid.resp-debounce', direction='out').exists())

    # memória de longo prazo

    @patch('apps.chats.whatsapp.send_text')
    @patch('apps.ai.generate_reply')
    def test_task_passes_contact_memory_to_ai(self, mock_generate, mock_send):
        mock_generate.return_value = 'Oi Matheus!'
        mock_send.return_value = 'wamid.resp-mem'
        Contact.objects.filter(pk=self.contact.pk).update(memory='Nome: Matheus')
        incoming = Message.objects.get(wa_message_id='wamid.1')

        tasks.process_incoming_message(incoming.pk)

        self.assertEqual(mock_generate.call_args.kwargs['memory'], 'Nome: Matheus')

    @patch('apps.chats.whatsapp.send_text')
    @patch('apps.ai.generate_reply')
    def test_task_enqueues_memory_update_after_recording_reply(self, mock_generate, mock_send):
        mock_generate.return_value = 'Resposta gerada'
        mock_send.return_value = 'wamid.resposta'
        incoming = Message.objects.get(wa_message_id='wamid.1')
        # update reescreve a memória a partir do histórico - a resposta precisa já estar gravada
        self.mock_memory_enqueue.side_effect = lambda contact_pk: self.assertTrue(
            Message.objects.filter(wa_message_id='wamid.resposta', direction='out').exists()
        )

        tasks.process_incoming_message(incoming.pk)

        self.mock_memory_enqueue.assert_called_once_with(self.contact.pk)

    @patch('apps.chats.whatsapp.send_text')
    @patch('apps.ai.generate_reply')
    def test_task_aborted_by_debounce_does_not_update_memory(self, mock_generate, mock_send):
        incoming = Message.objects.get(wa_message_id='wamid.1')
        Message.objects.create(contact=self.contact, direction='in', content='mais nova', wa_message_id='wamid.newest')

        tasks.process_incoming_message(incoming.pk)

        self.mock_memory_enqueue.assert_not_called()

    @patch('apps.chats.whatsapp.send_text')
    @patch('apps.ai.generate_reply')
    def test_task_send_failure_does_not_update_memory(self, mock_generate, mock_send):
        mock_generate.return_value = 'Resposta'
        mock_send.side_effect = httpx.HTTPStatusError('Bad Request', request=MagicMock(), response=MagicMock())
        incoming = Message.objects.get(wa_message_id='wamid.1')

        with self.assertRaises(httpx.HTTPStatusError):
            tasks.process_incoming_message(incoming.pk)

        self.mock_memory_enqueue.assert_not_called()

    # handoff

    @patch('apps.chats.whatsapp.send_text')
    @patch('apps.ai.generate_reply')
    def test_task_paused_contact_does_not_reply(self, mock_generate, mock_send):
        Contact.objects.filter(pk=self.contact.pk).update(ai_paused=True)
        incoming = Message.objects.get(wa_message_id='wamid.1')
        out_count = Message.objects.filter(direction='out').count()

        tasks.process_incoming_message(incoming.pk)

        mock_generate.assert_not_called()
        mock_send.assert_not_called()
        self.mock_memory_enqueue.assert_not_called()
        self.assertEqual(Message.objects.filter(direction='out').count(), out_count)

    @patch('apps.chats.whatsapp.send_text')
    @patch('apps.ai.transcribe_audio')
    @patch('apps.chats.whatsapp.download_media')
    @patch('apps.ai.generate_reply')
    def test_task_paused_contact_still_transcribes_audio(self, mock_generate, mock_download, mock_transcribe, mock_send):
        # atendente precisa ler o áudio - transcrição roda antes do guard de pausa
        Contact.objects.filter(pk=self.contact.pk).update(ai_paused=True)
        mock_download.return_value = (b'ogg-bytes', 'audio/ogg')
        mock_transcribe.return_value = 'áudio pro atendente'
        incoming = Message.objects.create(
            contact=self.contact, direction='in', content='',
            message_type='audio', media_id='media-aud-paused', wa_message_id='wamid.aud-paused',
        )

        tasks.process_incoming_message(incoming.pk)

        incoming.refresh_from_db()
        self.assertEqual(incoming.content, 'áudio pro atendente')
        mock_generate.assert_not_called()
        mock_send.assert_not_called()

    @patch('apps.chats.whatsapp.send_text')
    @patch('apps.ai.generate_reply')
    def test_task_transfer_tool_pauses_contact(self, mock_generate, mock_send):
        # a closure passada como tool tem que pausar ESTE contact quando o modelo chamá-la
        mock_generate.return_value = 'Vou te transferir!'
        mock_send.return_value = 'wamid.resp-transfer'
        incoming = Message.objects.get(wa_message_id='wamid.1')

        tasks.process_incoming_message(incoming.pk)

        tools = mock_generate.call_args.kwargs['tools']
        self.assertEqual(len(tools), 1)
        tool = tools[0]
        self.assertEqual(tool.__name__, 'transferir_para_atendente')
        self.assertTrue(tool.__doc__)  # docstring é a descrição que o modelo lê
        tool(motivo='cliente pediu humano')  # simula o modelo chamando a tool
        self.contact.refresh_from_db()
        self.assertTrue(self.contact.ai_paused)
        # handoff automático tem tipo próprio: front notifica só quando a IA transfere,
        # não quando o próprio atendente pausa pela UI (esse é 'contact');
        # name vai no evento pro toast funcionar fora da página de conversas
        self.mock_events.assert_any_call(
            self.company.pk,
            {'type': 'handoff', 'contact_id': self.contact.pk, 'name': self.contact.name},
        )


class UpdateContactMemoryTests(BaseChatsTestCase):
    @patch('apps.ai.update_memory')
    def test_task_rewrites_contact_memory(self, mock_update):
        mock_update.return_value = 'Nome: Matheus. Prefere áudio.'
        Contact.objects.filter(pk=self.contact.pk).update(memory='Nome: Matheus')

        tasks.update_contact_memory(self.contact.pk)

        self.contact.refresh_from_db()
        self.assertEqual(self.contact.memory, 'Nome: Matheus. Prefere áudio.')
        mock_update.assert_called_once_with(
            self.agent, 'Nome: Matheus',
            [{'role': 'user', 'content': 'Oi'}, {'role': 'assistant', 'content': 'Olá! Como posso ajudar?'}],
        )

    @patch('apps.ai.update_memory')
    def test_task_unknown_contact_is_silent(self, mock_update):
        tasks.update_contact_memory(99999)
        mock_update.assert_not_called()

    @patch('apps.ai.update_memory')
    def test_task_failure_raises_without_saving(self, mock_update):
        # em chamada direta (fora de worker) self.retry re-levanta a exceção original
        mock_update.side_effect = RuntimeError('provider down')

        with self.assertRaises(RuntimeError):
            tasks.update_contact_memory(self.contact.pk)

        self.contact.refresh_from_db()
        self.assertEqual(self.contact.memory, '')


# payload no formato real do GET /{waba_id}/message_templates da Meta
TEMPLATES_FIXTURE = [
    {
        'id': 'tpl-oferta',
        'name': 'oferta',
        'status': 'APPROVED',
        'language': 'pt_BR',
        'category': 'MARKETING',
        'components': [
            {'type': 'HEADER', 'format': 'TEXT', 'text': 'Olá!'},
            {'type': 'BODY', 'text': 'Oi {{1}}, temos {{2}} pra você.'},
            {'type': 'FOOTER', 'text': 'Chatly'},
        ],
    },
    {
        'id': 'tpl-janela',
        'name': 'janela',
        'status': 'APPROVED',
        'language': 'pt_BR',
        'category': 'UTILITY',
        'components': [{'type': 'BODY', 'text': 'Podemos continuar seu atendimento?'}],
    },
    {
        'id': 'tpl-pendente',
        'name': 'pendente',
        'status': 'PENDING',
        'language': 'pt_BR',
        'category': 'MARKETING',
        'components': [{'type': 'BODY', 'text': 'Aguardando {{1}}.'}],
    },
    {
        'id': 'tpl-promo-imagem',
        'name': 'promo_imagem',
        'status': 'APPROVED',
        'language': 'pt_BR',
        'category': 'MARKETING',
        'components': [
            {'type': 'HEADER', 'format': 'IMAGE'},
            {'type': 'BODY', 'text': 'Confira nossa promoção!'},
        ],
    },
]


class TemplateTests(BaseChatsTestCase):
    """Listagem de templates da Meta + envio de template no chat 1-1 (janela de 24h)."""

    def list_url(self, company_id: int, account_id: int) -> str:
        return f'/api/companies/{company_id}/accounts/{account_id}/templates'

    def post_template(self, contact_id: int, template_name: str = 'oferta', params: list | None = None, header_media_id: str = ''):
        payload = {'template_name': template_name, 'language': 'pt_BR', 'params': params or [], 'header_media_id': header_media_id}
        return self.client.post(
            f'/api/companies/{self.company.pk}/contacts/{contact_id}/messages/template',
            data=json.dumps(payload), content_type='application/json', headers=self.auth,
        )

    @patch('apps.chats.whatsapp.list_templates')
    def test_list_templates_extracts_body_and_param_count(self, mock_list):
        mock_list.return_value = TEMPLATES_FIXTURE

        response = self.client.get(self.list_url(self.company.pk, self.account.pk), headers=self.auth)

        self.assertEqual(response.status_code, 200, response.json())
        mock_list.assert_called_once_with(self.account)
        body = response.json()
        self.assertEqual(len(body), 4)
        oferta = body[0]
        self.assertEqual(oferta['id'], 'tpl-oferta')
        self.assertEqual(oferta['name'], 'oferta')
        self.assertEqual(oferta['status'], 'APPROVED')
        # body_text vem só do component BODY (header/footer ficam de fora)
        self.assertEqual(oferta['body_text'], 'Oi {{1}}, temos {{2}} pra você.')
        self.assertEqual(oferta['param_count'], 2)
        self.assertEqual(oferta['header_format'], 'TEXT')
        self.assertEqual(body[1]['param_count'], 0)
        self.assertEqual(body[1]['header_format'], 'NONE')
        self.assertEqual(body[2]['status'], 'PENDING')
        self.assertEqual(body[3]['header_format'], 'IMAGE')
        self.assertFalse(body[3]['has_header_media'])
        self.assertEqual(oferta['quick_replies'], [])

    @patch('apps.chats.whatsapp.list_templates')
    def test_list_templates_exposes_quick_replies(self, mock_list):
        mock_list.return_value = [{
            'id': 't', 'name': 'consulta', 'status': 'APPROVED', 'language': 'pt_BR', 'category': 'UTILITY',
            'components': [
                {'type': 'BODY', 'text': 'Confirma?'},
                {'type': 'BUTTONS', 'buttons': [
                    {'type': 'QUICK_REPLY', 'text': 'Confirmar'}, {'type': 'URL', 'text': 'Site', 'url': 'https://x.com'},
                    {'type': 'QUICK_REPLY', 'text': 'Bloquear'},
                ]},
            ],
        }]
        body = self.client.get(self.list_url(self.company.pk, self.account.pk), headers=self.auth).json()
        self.assertEqual(body[0]['quick_replies'], ['Confirmar', 'Bloquear'])

    @patch('apps.chats.whatsapp.list_templates')
    def test_list_templates_reports_saved_media(self, mock_list):
        mock_list.return_value = TEMPLATES_FIXTURE
        TemplateHeaderMedia.objects.create(
            account=self.account, template_name='promo_imagem', template_language='pt_BR', media_id='media-abc', media_type='image'
        )

        response = self.client.get(self.list_url(self.company.pk, self.account.pk), headers=self.auth)

        body = response.json()
        promo = next(t for t in body if t['name'] == 'promo_imagem')
        self.assertTrue(promo['has_header_media'])

    @patch('apps.chats.whatsapp.list_templates')
    def test_list_templates_of_foreign_account_returns_404(self, mock_list):
        response = self.client.get(self.list_url(self.company.pk, self.other_account.pk), headers=self.auth)
        self.assertEqual(response.status_code, 404)
        mock_list.assert_not_called()

    @patch('apps.chats.whatsapp.send_template')
    @patch('apps.chats.whatsapp.list_templates')
    def test_send_template_message_resolves_nome_and_records_rendered_body(self, mock_list, mock_send):
        mock_list.return_value = TEMPLATES_FIXTURE
        mock_send.return_value = 'wamid.tpl'

        response = self.post_template(self.contact.pk, params=['{{nome}}', '10% off'])

        self.assertEqual(response.status_code, 201, response.json())
        mock_send.assert_called_once_with(self.account, '5511999999999', 'oferta', 'pt_BR', ['Cliente A', '10% off'], '', '')
        message = Message.objects.get(wa_message_id='wamid.tpl')
        # content persiste o body renderizado: history da IA e preview da lista ficam legíveis
        self.assertEqual(message.content, 'Oi Cliente A, temos 10% off pra você.')
        self.assertEqual(message.direction, 'out')
        self.assertTrue(message.from_human)

    @patch('apps.chats.whatsapp.send_template')
    @patch('apps.chats.whatsapp.list_templates')
    def test_send_template_works_with_closed_window(self, mock_list, mock_send):
        # template é justamente o canal de reabertura - não pode levar o check da janela
        close_service_window(self.contact)
        mock_list.return_value = TEMPLATES_FIXTURE
        mock_send.return_value = 'wamid.tpl-closed'

        response = self.post_template(self.contact.pk, params=['Cliente', '10% off'])

        self.assertEqual(response.status_code, 201, response.json())
        self.assertTrue(Message.objects.filter(wa_message_id='wamid.tpl-closed').exists())

    @patch('apps.chats.whatsapp.send_template')
    @patch('apps.chats.whatsapp.list_templates')
    def test_send_template_without_params(self, mock_list, mock_send):
        mock_list.return_value = TEMPLATES_FIXTURE
        mock_send.return_value = 'wamid.tpl2'

        response = self.post_template(self.contact.pk, template_name='janela')

        self.assertEqual(response.status_code, 201, response.json())
        mock_send.assert_called_once_with(self.account, '5511999999999', 'janela', 'pt_BR', [], '', '')
        self.assertEqual(Message.objects.get(wa_message_id='wamid.tpl2').content, 'Podemos continuar seu atendimento?')

    @patch('apps.chats.whatsapp.send_template')
    @patch('apps.chats.whatsapp.list_templates')
    def test_send_unapproved_template_returns_400(self, mock_list, mock_send):
        # PENDING/REJECTED não pode ser enviado - Meta recusaria
        mock_list.return_value = TEMPLATES_FIXTURE
        response = self.post_template(self.contact.pk, template_name='pendente', params=['x'])
        self.assertEqual(response.status_code, 400)
        mock_send.assert_not_called()

    @patch('apps.chats.whatsapp.send_template')
    @patch('apps.chats.whatsapp.list_templates')
    def test_send_unknown_template_returns_400(self, mock_list, mock_send):
        mock_list.return_value = TEMPLATES_FIXTURE
        response = self.post_template(self.contact.pk, template_name='nao_existe')
        self.assertEqual(response.status_code, 400)
        mock_send.assert_not_called()

    @patch('apps.chats.whatsapp.send_template')
    @patch('apps.chats.whatsapp.list_templates')
    def test_send_template_to_foreign_contact_returns_404(self, mock_list, mock_send):
        response = self.post_template(self.other_contact.pk)
        self.assertEqual(response.status_code, 404)
        mock_send.assert_not_called()

    @patch('apps.chats.whatsapp.send_template')
    @patch('apps.chats.whatsapp.list_templates')
    def test_send_template_meta_rejection_returns_400(self, mock_list, mock_send):
        mock_list.return_value = TEMPLATES_FIXTURE
        mock_send.side_effect = httpx.HTTPStatusError('Bad Request', request=MagicMock(), response=MagicMock())
        out_count = Message.objects.filter(direction='out').count()

        response = self.post_template(self.contact.pk, template_name='janela')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Message.objects.filter(direction='out').count(), out_count)

    @patch('apps.chats.whatsapp.send_template')
    @patch('apps.chats.whatsapp.list_templates')
    def test_send_template_with_image_header_requires_media(self, mock_list, mock_send):
        mock_list.return_value = TEMPLATES_FIXTURE

        response = self.post_template(self.contact.pk, template_name='promo_imagem')

        self.assertEqual(response.status_code, 400)
        self.assertIn('cabeçalho', response.json()['detail'])
        mock_send.assert_not_called()

    @patch('apps.chats.whatsapp.send_template')
    @patch('apps.chats.whatsapp.list_templates')
    def test_send_template_with_image_header_and_media_id_succeeds(self, mock_list, mock_send):
        mock_list.return_value = TEMPLATES_FIXTURE
        mock_send.return_value = 'wamid.img'

        response = self.post_template(self.contact.pk, template_name='promo_imagem', header_media_id='media-abc')

        self.assertEqual(response.status_code, 201, response.json())
        mock_send.assert_called_once_with(
            self.account, '5511999999999', 'promo_imagem', 'pt_BR', [], 'media-abc', 'image'
        )

    @patch('apps.chats.whatsapp.send_template')
    @patch('apps.chats.whatsapp.list_templates')
    def test_send_template_with_image_header_uses_saved_media_when_not_provided(self, mock_list, mock_send):
        mock_list.return_value = TEMPLATES_FIXTURE
        mock_send.return_value = 'wamid.img'
        TemplateHeaderMedia.objects.create(
            account=self.account, template_name='promo_imagem', template_language='pt_BR', media_id='media-salva', media_type='image'
        )

        response = self.post_template(self.contact.pk, template_name='promo_imagem')

        self.assertEqual(response.status_code, 201, response.json())
        mock_send.assert_called_once_with(
            self.account, '5511999999999', 'promo_imagem', 'pt_BR', [], 'media-salva', 'image'
        )

    def test_list_contacts_exposes_last_inbound_at(self):
        # front usa pra detectar janela de 24h fechada; só msgs direction='in' contam
        only_out = Contact.objects.create(account=self.account, wa_id='5511777777777', name='Sem inbound')
        Message.objects.create(contact=only_out, direction='out', content='disparo', status='sent')

        response = self.client.get(
            f'/api/companies/{self.company.pk}/whatsapp-accounts/{self.account.pk}/contacts', headers=self.auth
        )

        self.assertEqual(response.status_code, 200, response.json())
        by_wa_id = {c['wa_id']: c for c in response.json()['items']}
        self.assertIsNotNone(by_wa_id['5511999999999']['last_inbound_at'])
        self.assertIsNone(by_wa_id['5511777777777']['last_inbound_at'])


class CampaignTests(BaseChatsTestCase):
    """CRUD de campanhas: criação valida template/destinatários e enfileira a task."""

    def url(self, company_id: int, campaign_id: int | None = None) -> str:
        base = f'/api/companies/{company_id}/campaigns'
        return f'{base}/{campaign_id}' if campaign_id else base

    def campaign_payload(self, **overrides) -> dict:
        payload = {
            'account_id': self.account.pk,
            'name': 'Promo de julho',
            'template_name': 'oferta',
            'template_language': 'pt_BR',
            'body_params': ['{{nome}}', '10% off'],
        }
        payload.update(overrides)
        return payload

    def post_campaign(self, company_id: int, **overrides):
        return self.client.post(
            self.url(company_id), data=json.dumps(self.campaign_payload(**overrides)),
            content_type='application/json', headers=self.auth,
        )

    @patch('apps.chats.tasks.run_campaign.delay')
    @patch('apps.chats.whatsapp.list_templates')
    def test_create_campaign_defaults_to_all_contacts(self, mock_list, mock_delay):
        mock_list.return_value = TEMPLATES_FIXTURE

        response = self.post_campaign(self.company.pk)

        self.assertEqual(response.status_code, 201, response.json())
        campaign = Campaign.objects.get(name='Promo de julho')
        self.assertEqual(campaign.total, 1)
        self.assertEqual(campaign.status, 'sending')
        mock_delay.assert_called_once_with(campaign.pk, [self.contact.pk])

    def test_draft_saves_incomplete_form_and_updates(self):
        url = f'/api/companies/{self.company.pk}/campaigns/drafts'
        response = self.client.post(url, data={'account_id': self.account.pk, 'name': '', 'draft': {'mode': 'csv'}},
                                    content_type='application/json', headers=self.auth)
        self.assertEqual(response.status_code, 201, response.json())
        body = response.json()
        self.assertEqual((body['status'], body['name'], body['draft']), ('draft', 'Rascunho', {'mode': 'csv'}))

        response = self.client.put(f'{url}/{body["id"]}', data={'account_id': self.account.pk, 'name': 'Promo', 'draft': {'template_name': 'oferta'}},
                                   content_type='application/json', headers=self.auth)
        self.assertEqual(response.status_code, 200, response.json())
        draft = Campaign.objects.get(pk=body['id'])
        self.assertEqual((draft.name, draft.template_name, draft.status), ('Promo', 'oferta', 'draft'))

    def test_draft_update_refuses_sent_campaign(self):
        sent = Campaign.objects.create(account=self.account, name='c', template_name='t', template_language='pt_BR', status='done')
        response = self.client.put(f'/api/companies/{self.company.pk}/campaigns/drafts/{sent.pk}', data={'account_id': self.account.pk},
                                   content_type='application/json', headers=self.auth)
        self.assertEqual(response.status_code, 409)

    @patch('apps.chats.tasks.run_campaign.delay')
    @patch('apps.chats.whatsapp.list_templates')
    def test_create_from_draft_removes_draft(self, mock_list, mock_delay):
        mock_list.return_value = TEMPLATES_FIXTURE
        draft = Campaign.objects.create(account=self.account, name='r', template_name='', template_language='', status='draft')

        response = self.post_campaign(self.company.pk, draft_id=draft.pk)

        self.assertEqual(response.status_code, 201, response.json())
        self.assertFalse(Campaign.objects.filter(pk=draft.pk).exists())
        mock_delay.assert_called_once()

    @patch('apps.chats.tasks.run_campaign.delay')
    @patch('apps.chats.whatsapp.list_templates')
    def test_create_campaign_selected_ignores_foreign_contacts(self, mock_list, mock_delay):
        mock_list.return_value = TEMPLATES_FIXTURE

        response = self.post_campaign(self.company.pk, contact_ids=[self.contact.pk, self.other_contact.pk])

        self.assertEqual(response.status_code, 201, response.json())
        self.assertEqual(response.json()['total'], 1)
        mock_delay.assert_called_once_with(ANY, [self.contact.pk])

    @patch('apps.chats.tasks.run_campaign.delay')
    @patch('apps.chats.whatsapp.list_templates')
    def test_create_campaign_csv_normalizes_number_and_creates_contact(self, mock_list, mock_delay):
        mock_list.return_value = TEMPLATES_FIXTURE

        response = self.post_campaign(
            self.company.pk, contact_ids=[], numbers=[{'number': '+55 (11) 97777-7777', 'name': 'Lead Novo'}]
        )

        self.assertEqual(response.status_code, 201, response.json())
        contact = Contact.objects.get(wa_id='5511977777777')
        self.assertEqual(contact.name, 'Lead Novo')
        self.assertEqual(contact.account, self.account)
        self.assertEqual(response.json()['total'], 1)
        mock_delay.assert_called_once_with(ANY, [contact.pk])

    @patch('apps.chats.tasks.run_campaign.delay')
    @patch('apps.chats.whatsapp.list_templates')
    def test_create_campaign_invalid_number_returns_400(self, mock_list, mock_delay):
        mock_list.return_value = TEMPLATES_FIXTURE

        response = self.post_campaign(self.company.pk, contact_ids=[], numbers=[{'number': '123'}])

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Campaign.objects.count(), 0)
        mock_delay.assert_not_called()

    @patch('apps.chats.tasks.run_campaign.delay')
    @patch('apps.chats.whatsapp.list_templates')
    def test_create_campaign_param_count_mismatch_returns_400(self, mock_list, mock_delay):
        # oferta tem {{1}} e {{2}} - mandar 1 param só = erro antes de criar qualquer coisa
        mock_list.return_value = TEMPLATES_FIXTURE

        response = self.post_campaign(self.company.pk, body_params=['só um'])

        self.assertEqual(response.status_code, 400)
        self.assertIn('expects 2', response.json()['detail'])
        mock_delay.assert_not_called()

    @patch('apps.chats.tasks.run_campaign.delay')
    @patch('apps.chats.whatsapp.list_templates')
    def test_create_campaign_without_recipients_returns_400(self, mock_list, mock_delay):
        mock_list.return_value = TEMPLATES_FIXTURE
        response = self.post_campaign(self.company.pk, contact_ids=[])
        self.assertEqual(response.status_code, 400)
        mock_delay.assert_not_called()

    @patch('apps.chats.whatsapp.list_templates')
    def test_create_campaign_on_foreign_company_returns_404(self, mock_list):
        response = self.post_campaign(self.other_company.pk)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(Campaign.objects.count(), 0)

    def test_list_campaigns_only_mine(self):
        Campaign.objects.create(account=self.account, name='Minha', template_name='oferta', template_language='pt_BR')
        Campaign.objects.create(account=self.other_account, name='Alheia', template_name='x', template_language='pt_BR')

        response = self.client.get(self.url(self.company.pk), headers=self.auth)

        self.assertEqual(response.status_code, 200, response.json())
        self.assertEqual(response.json()['count'], 1)
        self.assertEqual(response.json()['items'][0]['name'], 'Minha')

    def test_get_campaign_aggregates_delivered_and_read(self):
        campaign = Campaign.objects.create(
            account=self.account, name='Promo', template_name='oferta', template_language='pt_BR', total=3, sent=3
        )
        for i, status in enumerate(('sent', 'delivered', 'read')):
            Message.objects.create(
                contact=self.contact, direction='out', content='oi', status=status,
                wa_message_id=f'wamid.camp{i}', campaign=campaign,
            )

        response = self.client.get(self.url(self.company.pk, campaign.pk), headers=self.auth)

        self.assertEqual(response.status_code, 200, response.json())
        body = response.json()
        self.assertEqual(body['delivered'], 1)
        self.assertEqual(body['read'], 1)

    def test_get_foreign_campaign_returns_404(self):
        campaign = Campaign.objects.create(account=self.other_account, name='Alheia', template_name='x', template_language='pt_BR')
        response = self.client.get(self.url(self.company.pk, campaign.pk), headers=self.auth)
        self.assertEqual(response.status_code, 404)

    @patch('apps.chats.tasks.run_campaign.delay')
    @patch('apps.chats.whatsapp.list_templates')
    def test_create_campaign_with_image_header_requires_media(self, mock_list, mock_delay):
        mock_list.return_value = TEMPLATES_FIXTURE

        response = self.post_campaign(self.company.pk, template_name='promo_imagem', body_params=[])

        self.assertEqual(response.status_code, 400)
        self.assertIn('cabeçalho', response.json()['detail'])
        mock_delay.assert_not_called()

    @patch('apps.chats.tasks.run_campaign.delay')
    @patch('apps.chats.whatsapp.list_templates')
    def test_create_campaign_with_image_header_and_media_id_saves_it(self, mock_list, mock_delay):
        mock_list.return_value = TEMPLATES_FIXTURE

        response = self.post_campaign(
            self.company.pk, template_name='promo_imagem', body_params=[], header_media_id='media-abc'
        )

        self.assertEqual(response.status_code, 201, response.json())
        campaign = Campaign.objects.get(name='Promo de julho')
        self.assertEqual(campaign.header_media_id, 'media-abc')
        self.assertEqual(campaign.header_media_type, 'image')

    @patch('apps.chats.tasks.run_campaign.delay')
    @patch('apps.chats.whatsapp.list_templates')
    def test_create_campaign_with_image_header_uses_saved_media_when_not_provided(self, mock_list, mock_delay):
        mock_list.return_value = TEMPLATES_FIXTURE
        TemplateHeaderMedia.objects.create(
            account=self.account, template_name='promo_imagem', template_language='pt_BR', media_id='media-salva', media_type='image'
        )

        response = self.post_campaign(self.company.pk, template_name='promo_imagem', body_params=[])

        self.assertEqual(response.status_code, 201, response.json())
        campaign = Campaign.objects.get(name='Promo de julho')
        self.assertEqual(campaign.header_media_id, 'media-salva')
        self.assertEqual(campaign.header_media_type, 'image')


class CampaignFirstNameEndToEndTests(BaseChatsTestCase):
    url = CampaignTests.url
    campaign_payload = CampaignTests.campaign_payload
    post_campaign = CampaignTests.post_campaign

    """Caminho real: CSV -> campanha com {{first_name}} (do sistema, sai do nome) -> envio."""

    @patch('apps.chats.tasks.time.sleep')
    @patch('apps.chats.whatsapp.send_template', side_effect=['wamid.e1', 'wamid.e2'])
    @patch('apps.chats.whatsapp.list_templates', return_value=TEMPLATES_FIXTURE)
    def test_csv_first_name_fills_template_variable(self, mock_list, mock_send, mock_sleep):
        r = self.client.post(f'/api/companies/{self.company.pk}/contact-fields', data={'label': 'first_name'}, content_type='application/json', headers=self.auth)
        self.assertEqual(r.status_code, 400)  # reservado ao sistema

        numbers = [
            {'number': '5511955550001', 'name': 'Hélcio Santos | Violinista', 'fields': {'first_name': 'Hélcio'}},
            {'number': self.contact.wa_id, 'name': 'Cliente A', 'fields': {'first_name': 'Ana'}},  # contato que já existia
        ]
        with patch('apps.chats.tasks.run_campaign.delay', side_effect=lambda *args: tasks.run_campaign(*args)):
            r = self.post_campaign(self.company.pk, contact_ids=[], numbers=numbers, body_params=['{{first_name}}', '10% off'])
        self.assertEqual(r.status_code, 201, r.json())

        sent = {c.args[1]: c.args[4] for c in mock_send.call_args_list}
        self.assertEqual(sent, {'5511955550001': ['Hélcio', '10% off'], self.contact.wa_id: ['Cliente', '10% off']})
        campaign = Campaign.objects.get(pk=r.json()['id'])
        self.assertEqual((campaign.sent, campaign.failed, campaign.status), (2, 0, 'done'))


class FirstNameVariableTests(BaseChatsTestCase):
    def test_first_name_always_filled(self):
        cases = [
            ('Henrique Teste', {}, 'Henrique'),
            ('Ana', {}, 'Ana'),
            ('🌸 Maria Clara', {}, 'Maria'),
            ('Hélcio Santos', {'first_name': 'Helcinho'}, 'Hélcio'),  # valor antigo no JSON não vale mais
            ('', {}, '5511900000000'),
        ]
        for name, fields, expected in cases:
            contact = Contact(account=self.account, wa_id='5511900000000', name=name, custom_fields=fields)
            self.assertEqual(services.contact_variables(contact)['first_name'], expected, name)


class DeleteCampaignTests(BaseChatsTestCase):
    url = CampaignTests.url
    campaign_payload = CampaignTests.campaign_payload
    post_campaign = CampaignTests.post_campaign

    def test_delete_keeps_messages_in_chat(self):
        campaign = Campaign.objects.create(account=self.account, name='teste', template_name='oferta', template_language='pt_BR', status='done')
        msg = Message.objects.create(contact=self.contact, direction='out', content='oi', campaign=campaign)
        r = self.client.delete(self.url(self.company.pk, campaign.pk), headers=self.auth)
        self.assertEqual(r.status_code, 204)
        self.assertFalse(Campaign.objects.filter(pk=campaign.pk).exists())
        msg.refresh_from_db()
        self.assertIsNone(msg.campaign_id)

    def test_delete_rules(self):
        sending = Campaign.objects.create(account=self.account, name='x', template_name='oferta', template_language='pt_BR', status='sending')
        r = self.client.delete(self.url(self.company.pk, sending.pk), headers=self.auth)
        self.assertEqual(r.status_code, 409)
        r = self.client.delete(self.url(self.other_company.pk, sending.pk), headers=self.auth)
        self.assertEqual(r.status_code, 404)
        self.assertTrue(Campaign.objects.filter(pk=sending.pk).exists())


class RunCampaignTests(BaseChatsTestCase):
    """Task de disparo: contadores, render por contato e término com status certo."""

    def setUp(self):
        super().setUp()
        # throttle de 0.1s por envio deixaria a suíte lenta
        patcher = patch('apps.chats.tasks.time.sleep')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.campaign = Campaign.objects.create(
            account=self.account, name='Promo', template_name='oferta',
            template_language='pt_BR', body_params=['{{nome}}', '10% off'], total=2,
        )
        self.contact_b = Contact.objects.create(account=self.account, wa_id='5511666666666', name='Cliente B')

    @patch('apps.chats.whatsapp.send_template')
    @patch('apps.chats.whatsapp.list_templates')
    def test_run_campaign_counts_sent_and_failed_and_finishes_done(self, mock_list, mock_send):
        mock_list.return_value = TEMPLATES_FIXTURE
        # 1º envio ok, 2º rejeitado pela Meta - task segue e contabiliza
        mock_send.side_effect = ['wamid.c1', httpx.HTTPStatusError('Bad Request', request=MagicMock(), response=MagicMock())]

        tasks.run_campaign(self.campaign.pk, [self.contact.pk, self.contact_b.pk])

        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.sent, 1)
        self.assertEqual(self.campaign.failed, 1)
        self.assertEqual(self.campaign.status, 'done')
        # {{nome}} resolvido por contato em cada chamada
        self.assertEqual(mock_send.call_args_list[0].args, (self.account, '5511999999999', 'oferta', 'pt_BR', ['Cliente A', '10% off'], '', ''))
        self.assertEqual(mock_send.call_args_list[1].args, (self.account, '5511666666666', 'oferta', 'pt_BR', ['Cliente B', '10% off'], '', ''))
        message = Message.objects.get(wa_message_id='wamid.c1')
        self.assertEqual(message.campaign, self.campaign)
        self.assertEqual(message.content, 'Oi Cliente A, temos 10% off pra você.')
        self.assertTrue(message.from_human)
        self.assertFalse(Message.objects.filter(contact=self.contact_b, direction='out').exists())
        # quem recebeu fica sem resposta atrasada da IA; quem falhou não
        self.assertEqual(set(Contact.objects.filter(ai_no_catchup=True)), {self.contact})

    @patch('apps.chats.whatsapp.send_template')
    @patch('apps.chats.whatsapp.list_templates')
    def test_run_campaign_passes_header_media_to_every_send(self, mock_list, mock_send):
        mock_list.return_value = TEMPLATES_FIXTURE
        self.campaign.header_media_id = 'media-abc'
        self.campaign.header_media_type = 'image'
        self.campaign.save()
        mock_send.side_effect = ['wamid.c1', 'wamid.c2']

        tasks.run_campaign(self.campaign.pk, [self.contact.pk, self.contact_b.pk])

        self.assertEqual(mock_send.call_args_list[0].args[-2:], ('media-abc', 'image'))
        self.assertEqual(mock_send.call_args_list[1].args[-2:], ('media-abc', 'image'))

    @patch('apps.chats.whatsapp.send_template')
    @patch('apps.chats.whatsapp.list_templates')
    def test_run_campaign_template_gone_marks_failed(self, mock_list, mock_send):
        # template apagado/despublicado na Meta entre criar e rodar
        mock_list.return_value = []

        tasks.run_campaign(self.campaign.pk, [self.contact.pk])

        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, 'failed')
        mock_send.assert_not_called()

    @patch('apps.chats.whatsapp.send_template')
    def test_run_campaign_unknown_campaign_is_silent(self, mock_send):
        tasks.run_campaign(99999, [])
        mock_send.assert_not_called()


class TemplateApiUnitTests(BaseChatsTestCase):
    """Camada whatsapp: formato exato dos payloads de template da Cloud API."""

    @patch('apps.chats.whatsapp.httpx.get')
    def test_list_templates_queries_waba(self, mock_get):
        mock_get.return_value.json.return_value = {'data': TEMPLATES_FIXTURE}

        result = whatsapp.list_templates(self.account)

        self.assertEqual(result, TEMPLATES_FIXTURE)
        args, kwargs = mock_get.call_args
        self.assertEqual(args[0], f'{whatsapp.GRAPH_URL}/waba-1/message_templates')
        self.assertEqual(kwargs['headers']['Authorization'], 'Bearer EAAG-meu-token-secreto')
        self.assertEqual(kwargs['params'], {'fields': 'id,name,status,language,category,components', 'limit': 100})

    @patch('apps.chats.whatsapp.httpx.post')
    def test_send_template_with_body_params(self, mock_post):
        mock_post.return_value.json.return_value = {'messages': [{'id': 'wamid.tpl'}]}

        wamid = whatsapp.send_template(self.account, '5511999999999', 'oferta', 'pt_BR', ['Cliente A', '10% off'])

        self.assertEqual(wamid, 'wamid.tpl')
        self.assertEqual(mock_post.call_args.kwargs['json'], {
            'messaging_product': 'whatsapp',
            'to': '5511999999999',
            'type': 'template',
            'template': {
                'name': 'oferta',
                'language': {'code': 'pt_BR'},
                'components': [{'type': 'body', 'parameters': [
                    {'type': 'text', 'text': 'Cliente A'},
                    {'type': 'text', 'text': '10% off'},
                ]}],
            },
        })

    @patch('apps.chats.whatsapp.httpx.post')
    def test_send_template_without_params_omits_components(self, mock_post):
        mock_post.return_value.json.return_value = {'messages': [{'id': 'wamid.tpl'}]}

        whatsapp.send_template(self.account, '5511999999999', 'janela', 'pt_BR', [])

        self.assertEqual(mock_post.call_args.kwargs['json']['template'], {'name': 'janela', 'language': {'code': 'pt_BR'}})

    @patch('apps.chats.whatsapp.httpx.post')
    def test_send_template_raises_on_api_error(self, mock_post):
        mock_post.return_value.raise_for_status.side_effect = httpx.HTTPStatusError(
            'Bad Request', request=MagicMock(), response=MagicMock()
        )
        with self.assertRaises(httpx.HTTPStatusError):
            whatsapp.send_template(self.account, '5511999999999', 'oferta', 'pt_BR', [])

    @patch('apps.chats.whatsapp.httpx.post')
    def test_create_template_posts_payload_to_waba(self, mock_post):
        mock_post.return_value.is_success = True
        mock_post.return_value.json.return_value = {'id': 'tpl-1', 'status': 'PENDING', 'category': 'MARKETING'}
        payload = {'name': 'boas_vindas', 'language': 'pt_BR', 'category': 'MARKETING', 'components': []}

        result = whatsapp.create_template(self.account, payload)

        self.assertEqual(result['id'], 'tpl-1')
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], f'{whatsapp.GRAPH_URL}/waba-1/message_templates')
        self.assertEqual(kwargs['headers']['Authorization'], 'Bearer EAAG-meu-token-secreto')
        self.assertEqual(kwargs['json'], payload)

    @patch('apps.chats.whatsapp.httpx.post')
    def test_create_template_raises_with_meta_message(self, mock_post):
        mock_post.return_value.is_success = False
        mock_post.return_value.json.return_value = {'error': {'error_user_msg': 'Nome de template duplicado'}}

        with self.assertRaises(ValidationError) as ctx:
            whatsapp.create_template(self.account, {'name': 'dup'})
        self.assertEqual(ctx.exception.message, 'Nome de template duplicado')

    @patch('apps.chats.whatsapp.httpx.post')
    def test_create_template_falls_back_to_message_field(self, mock_post):
        mock_post.return_value.is_success = False
        mock_post.return_value.json.return_value = {'error': {'message': 'Invalid parameter'}}

        with self.assertRaises(ValidationError) as ctx:
            whatsapp.create_template(self.account, {})
        self.assertEqual(ctx.exception.message, 'Invalid parameter')

    @patch('apps.chats.whatsapp.httpx.post')
    def test_create_template_error_without_json_body_uses_fallback_message(self, mock_post):
        mock_post.return_value.is_success = False
        mock_post.return_value.json.side_effect = ValueError('no json')

        with self.assertRaises(ValidationError) as ctx:
            whatsapp.create_template(self.account, {})
        self.assertEqual(ctx.exception.message, 'erro no WhatsApp')

    @patch('apps.chats.whatsapp.httpx.post')
    def test_edit_template_posts_components_to_template_id(self, mock_post):
        mock_post.return_value.is_success = True
        mock_post.return_value.json.return_value = {'success': True}
        components = [{'type': 'BODY', 'text': 'Olá {{1}}'}]

        result = whatsapp.edit_template(self.account, 'tpl-1', components)

        self.assertEqual(result, {'success': True})
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], f'{whatsapp.GRAPH_URL}/tpl-1')
        self.assertEqual(kwargs['json'], {'components': components})

    @patch('apps.chats.whatsapp.httpx.post')
    def test_edit_template_includes_category_when_given(self, mock_post):
        mock_post.return_value.is_success = True
        mock_post.return_value.json.return_value = {'success': True}

        whatsapp.edit_template(self.account, 'tpl-1', [], category='UTILITY')

        self.assertEqual(mock_post.call_args.kwargs['json'], {'components': [], 'category': 'UTILITY'})

    @patch('apps.chats.whatsapp.httpx.post')
    def test_edit_template_raises_with_meta_message(self, mock_post):
        mock_post.return_value.is_success = False
        mock_post.return_value.json.return_value = {'error': {'error_user_msg': 'Template em análise, não pode editar'}}

        with self.assertRaises(ValidationError) as ctx:
            whatsapp.edit_template(self.account, 'tpl-1', [])
        self.assertEqual(ctx.exception.message, 'Template em análise, não pode editar')

    @patch('apps.chats.whatsapp.httpx.delete')
    def test_delete_template_by_name_only_omits_hsm_id(self, mock_delete):
        mock_delete.return_value.is_success = True
        mock_delete.return_value.json.return_value = {'success': True}

        whatsapp.delete_template(self.account, 'boas_vindas')

        args, kwargs = mock_delete.call_args
        self.assertEqual(args[0], f'{whatsapp.GRAPH_URL}/waba-1/message_templates')
        self.assertEqual(kwargs['params'], {'name': 'boas_vindas'})

    @patch('apps.chats.whatsapp.httpx.delete')
    def test_delete_template_with_id_includes_hsm_id(self, mock_delete):
        mock_delete.return_value.is_success = True
        mock_delete.return_value.json.return_value = {'success': True}

        whatsapp.delete_template(self.account, 'boas_vindas', template_id='tpl-1')

        self.assertEqual(mock_delete.call_args.kwargs['params'], {'name': 'boas_vindas', 'hsm_id': 'tpl-1'})

    @patch('apps.chats.whatsapp.httpx.delete')
    def test_delete_template_raises_with_meta_message(self, mock_delete):
        mock_delete.return_value.is_success = False
        mock_delete.return_value.json.return_value = {'error': {'error_user_msg': 'Template não encontrado'}}

        with self.assertRaises(ValidationError) as ctx:
            whatsapp.delete_template(self.account, 'inexistente')
        self.assertEqual(ctx.exception.message, 'Template não encontrado')


class TemplateMediaUploadTests(BaseChatsTestCase):
    """whatsapp.upload_sample_media: Resumable Upload API em 2 passos."""

    def test_upload_without_app_id_raises(self):
        with self.assertRaises(ValidationError) as ctx:
            whatsapp.upload_sample_media(self.account, b'bytes', 'image/png', 'foto.png')
        self.assertIn('app_id', ctx.exception.message)

    @patch('apps.chats.whatsapp.httpx.post')
    def test_upload_completes_two_step_flow(self, mock_post):
        self.account.app_id = 'app-123'
        self.account.save()
        session_resp = MagicMock(is_success=True)
        session_resp.json.return_value = {'id': 'upload:session-abc'}
        upload_resp = MagicMock(is_success=True)
        upload_resp.json.return_value = {'h': 'h:handle-xyz'}
        mock_post.side_effect = [session_resp, upload_resp]

        handle = whatsapp.upload_sample_media(self.account, b'conteudo', 'image/png', 'foto.png')

        self.assertEqual(handle, 'h:handle-xyz')
        first_call, second_call = mock_post.call_args_list
        self.assertEqual(first_call.args[0], f'{whatsapp.GRAPH_URL}/app-123/uploads')
        self.assertEqual(first_call.kwargs['headers']['Authorization'], 'Bearer EAAG-meu-token-secreto')
        self.assertEqual(first_call.kwargs['params'], {'file_length': 8, 'file_type': 'image/png', 'file_name': 'foto.png'})
        self.assertEqual(second_call.args[0], f'{whatsapp.GRAPH_URL}/upload:session-abc')
        self.assertEqual(second_call.kwargs['headers']['Authorization'], 'OAuth EAAG-meu-token-secreto')
        self.assertEqual(second_call.kwargs['content'], b'conteudo')

    @patch('apps.chats.whatsapp.httpx.post')
    def test_upload_session_error_raises_with_meta_message(self, mock_post):
        self.account.app_id = 'app-123'
        self.account.save()
        session_resp = MagicMock(is_success=False)
        session_resp.json.return_value = {'error': {'error_user_msg': 'App sem permissão de upload'}}
        mock_post.return_value = session_resp

        with self.assertRaises(ValidationError) as ctx:
            whatsapp.upload_sample_media(self.account, b'x', 'image/png', 'foto.png')
        self.assertEqual(ctx.exception.message, 'App sem permissão de upload')

    @patch('apps.chats.whatsapp.httpx.post')
    def test_upload_second_step_error_raises(self, mock_post):
        self.account.app_id = 'app-123'
        self.account.save()
        session_resp = MagicMock(is_success=True)
        session_resp.json.return_value = {'id': 'upload:session-abc'}
        upload_resp = MagicMock(is_success=False)
        upload_resp.json.return_value = {'error': {'error_user_msg': 'Upload expirado'}}
        mock_post.side_effect = [session_resp, upload_resp]

        with self.assertRaises(ValidationError) as ctx:
            whatsapp.upload_sample_media(self.account, b'x', 'image/png', 'foto.png')
        self.assertEqual(ctx.exception.message, 'Upload expirado')


class BuildTemplateComponentsTests(BaseChatsTestCase):
    """services.build_components: monta o array components no formato da Meta + validações."""

    def base_data(self, **overrides) -> dict:
        data = {'body_text': 'Oi {{1}}, tudo bem?', 'body_examples': ['Cliente A']}
        data.update(overrides)
        return data

    def test_body_only(self):
        components = services.build_components(self.base_data())
        self.assertEqual(components, [
            {'type': 'BODY', 'text': 'Oi {{1}}, tudo bem?', 'example': {'body_text': [['Cliente A']]}},
        ])

    def test_body_without_params_omits_example(self):
        components = services.build_components({'body_text': 'Mensagem fixa.', 'body_examples': []})
        self.assertEqual(components, [{'type': 'BODY', 'text': 'Mensagem fixa.'}])

    def test_header_text_with_example(self):
        data = self.base_data(header={'format': 'TEXT', 'text': 'Olá {{1}}', 'example': 'Cliente A'})
        components = services.build_components(data)
        self.assertEqual(components[0], {
            'type': 'HEADER', 'format': 'TEXT', 'text': 'Olá {{1}}', 'example': {'header_text': ['Cliente A']},
        })

    def test_header_none_format_is_omitted(self):
        data = self.base_data(header={'format': 'NONE'})
        components = services.build_components(data)
        self.assertEqual([c['type'] for c in components], ['BODY'])

    def test_footer(self):
        data = self.base_data(footer_text='Chatly')
        components = services.build_components(data)
        self.assertIn({'type': 'FOOTER', 'text': 'Chatly'}, components)

    def test_footer_too_long_raises(self):
        data = self.base_data(footer_text='x' * 61)
        with self.assertRaises(ValidationError):
            services.build_components(data)

    def test_body_examples_count_mismatch_raises(self):
        data = self.base_data(body_text='Oi {{1}} e {{2}}', body_examples=['só um'])
        with self.assertRaises(ValidationError):
            services.build_components(data)

    def test_quick_reply_button(self):
        data = self.base_data(buttons=[{'type': 'QUICK_REPLY', 'text': 'Confirmar'}])
        components = services.build_components(data)
        buttons = next(c for c in components if c['type'] == 'BUTTONS')
        self.assertEqual(buttons['buttons'], [{'type': 'QUICK_REPLY', 'text': 'Confirmar'}])

    def test_url_button(self):
        data = self.base_data(buttons=[{'type': 'URL', 'text': 'Acessar', 'url': 'https://chatly.ia.br'}])
        components = services.build_components(data)
        buttons = next(c for c in components if c['type'] == 'BUTTONS')
        self.assertEqual(buttons['buttons'], [{'type': 'URL', 'text': 'Acessar', 'url': 'https://chatly.ia.br'}])

    def test_url_button_without_url_raises(self):
        data = self.base_data(buttons=[{'type': 'URL', 'text': 'Acessar'}])
        with self.assertRaises(ValidationError):
            services.build_components(data)

    def test_phone_button(self):
        data = self.base_data(buttons=[{'type': 'PHONE_NUMBER', 'text': 'Ligar', 'phone_number': '+5511999999999'}])
        components = services.build_components(data)
        buttons = next(c for c in components if c['type'] == 'BUTTONS')
        self.assertEqual(buttons['buttons'], [{'type': 'PHONE_NUMBER', 'text': 'Ligar', 'phone_number': '+5511999999999'}])

    def test_phone_button_without_number_raises(self):
        data = self.base_data(buttons=[{'type': 'PHONE_NUMBER', 'text': 'Ligar'}])
        with self.assertRaises(ValidationError):
            services.build_components(data)

    def test_header_image_with_handle(self):
        data = self.base_data(header={'format': 'IMAGE', 'handle': 'h:handle-xyz'})
        components = services.build_components(data)
        self.assertEqual(components[0], {'type': 'HEADER', 'format': 'IMAGE', 'example': {'header_handle': ['h:handle-xyz']}})

    def test_header_image_without_handle_raises(self):
        data = self.base_data(header={'format': 'IMAGE'})
        with self.assertRaises(ValidationError):
            services.build_components(data)


class TemplateManagementServiceTests(BaseChatsTestCase):
    """services.create/edit/delete_template + get_template_detail: validação de nome e mapeamento de detalhe."""

    @patch('apps.chats.whatsapp.create_template')
    def test_create_template_builds_payload_and_delegates_to_boundary(self, mock_create):
        mock_create.return_value = {'id': 'tpl-1', 'status': 'PENDING', 'category': 'MARKETING'}
        data = {'name': 'boas_vindas', 'language': 'pt_BR', 'category': 'MARKETING', 'body_text': 'Oi!', 'body_examples': []}

        result = services.create_template(self.company, self.account.pk, data)

        self.assertEqual(result['id'], 'tpl-1')
        account_arg, payload_arg = mock_create.call_args.args
        self.assertEqual(account_arg, self.account)
        self.assertEqual(payload_arg['name'], 'boas_vindas')
        self.assertEqual(payload_arg['components'], [{'type': 'BODY', 'text': 'Oi!'}])

    @patch('apps.chats.whatsapp.create_template')
    def test_create_template_with_invalid_name_raises_without_calling_boundary(self, mock_create):
        data = {'name': 'Boas Vindas!', 'language': 'pt_BR', 'category': 'MARKETING', 'body_text': 'Oi!', 'body_examples': []}
        with self.assertRaises(ValidationError):
            services.create_template(self.company, self.account.pk, data)
        mock_create.assert_not_called()

    @patch('apps.chats.whatsapp.create_template')
    def test_create_template_on_foreign_account_returns_404(self, mock_create):
        data = {'name': 'boas_vindas', 'language': 'pt_BR', 'category': 'MARKETING', 'body_text': 'Oi!', 'body_examples': []}
        with self.assertRaises(NotFound):
            services.create_template(self.company, self.other_account.pk, data)
        mock_create.assert_not_called()

    @patch('apps.chats.whatsapp.edit_template')
    def test_edit_template_delegates_components_and_category(self, mock_edit):
        mock_edit.return_value = {'success': True}
        data = {'name': 'oferta', 'language': 'pt_BR', 'category': 'UTILITY', 'body_text': 'Novo texto', 'body_examples': []}

        result = services.edit_template(self.company, self.account.pk, 'tpl-1', data)

        self.assertEqual(result, {'success': True})
        args, kwargs = mock_edit.call_args
        self.assertEqual(args[0], self.account)
        self.assertEqual(args[1], 'tpl-1')
        self.assertEqual(args[2], [{'type': 'BODY', 'text': 'Novo texto'}])
        self.assertEqual(args[3], 'UTILITY')

    @patch('apps.chats.whatsapp.create_template')
    def test_create_template_with_image_header_saves_media(self, mock_create):
        mock_create.return_value = {'id': 'tpl-1', 'status': 'PENDING', 'category': 'MARKETING'}
        data = {
            'name': 'promo', 'language': 'pt_BR', 'category': 'MARKETING', 'body_text': 'Oi!', 'body_examples': [],
            'header': {'format': 'IMAGE', 'handle': 'h:xyz', 'media_id': 'media-abc'},
        }

        services.create_template(self.company, self.account.pk, data)

        saved = TemplateHeaderMedia.objects.get(account=self.account, template_name='promo', template_language='pt_BR')
        self.assertEqual(saved.media_id, 'media-abc')
        self.assertEqual(saved.media_type, 'image')

    @patch('apps.chats.whatsapp.create_template')
    def test_create_template_without_media_header_does_not_save(self, mock_create):
        mock_create.return_value = {'id': 'tpl-1', 'status': 'PENDING', 'category': 'MARKETING'}
        data = {'name': 'boas_vindas', 'language': 'pt_BR', 'category': 'MARKETING', 'body_text': 'Oi!', 'body_examples': []}

        services.create_template(self.company, self.account.pk, data)

        self.assertFalse(TemplateHeaderMedia.objects.filter(account=self.account, template_name='boas_vindas').exists())

    @patch('apps.chats.whatsapp.edit_template')
    def test_edit_template_updates_saved_media(self, mock_edit):
        mock_edit.return_value = {'success': True}
        TemplateHeaderMedia.objects.create(
            account=self.account, template_name='promo', template_language='pt_BR', media_id='media-antiga', media_type='image'
        )
        data = {
            'name': 'promo', 'language': 'pt_BR', 'category': 'MARKETING', 'body_text': 'Novo texto', 'body_examples': [],
            'header': {'format': 'IMAGE', 'handle': 'h:novo', 'media_id': 'media-nova'},
        }

        services.edit_template(self.company, self.account.pk, 'tpl-1', data)

        self.assertEqual(TemplateHeaderMedia.objects.filter(account=self.account, template_name='promo').count(), 1)
        saved = TemplateHeaderMedia.objects.get(account=self.account, template_name='promo')
        self.assertEqual(saved.media_id, 'media-nova')

    @patch('apps.chats.whatsapp.delete_template')
    def test_delete_template_delegates_to_boundary(self, mock_delete):
        services.delete_template(self.company, self.account.pk, 'boas_vindas', 'tpl-1')
        mock_delete.assert_called_once_with(self.account, 'boas_vindas', 'tpl-1')

    @patch('apps.chats.whatsapp.list_templates')
    def test_get_template_detail_maps_full_structure(self, mock_list):
        mock_list.return_value = TEMPLATES_FIXTURE

        detail = services.get_template_detail(self.company, self.account.pk, 'tpl-oferta')

        self.assertEqual(detail['id'], 'tpl-oferta')
        self.assertEqual(detail['header'], {'format': 'TEXT', 'text': 'Olá!', 'example': ''})
        self.assertEqual(detail['body_text'], 'Oi {{1}}, temos {{2}} pra você.')
        self.assertEqual(detail['footer_text'], 'Chatly')
        self.assertEqual(detail['buttons'], [])
        self.assertFalse(detail['has_header_media'])

    @patch('apps.chats.whatsapp.list_templates')
    def test_get_template_detail_reports_saved_media(self, mock_list):
        mock_list.return_value = TEMPLATES_FIXTURE
        TemplateHeaderMedia.objects.create(
            account=self.account, template_name='promo_imagem', template_language='pt_BR', media_id='media-abc', media_type='image'
        )

        detail = services.get_template_detail(self.company, self.account.pk, 'tpl-promo-imagem')

        self.assertTrue(detail['has_header_media'])

    @patch('apps.chats.whatsapp.list_templates')
    def test_get_template_detail_without_header_or_footer(self, mock_list):
        mock_list.return_value = TEMPLATES_FIXTURE

        detail = services.get_template_detail(self.company, self.account.pk, 'tpl-janela')

        self.assertIsNone(detail['header'])
        self.assertEqual(detail['footer_text'], '')

    @patch('apps.chats.whatsapp.list_templates')
    def test_get_template_detail_not_found_raises_404(self, mock_list):
        mock_list.return_value = TEMPLATES_FIXTURE
        with self.assertRaises(NotFound):
            services.get_template_detail(self.company, self.account.pk, 'tpl-inexistente')

    @patch('apps.chats.whatsapp.upload_media')
    @patch('apps.chats.whatsapp.upload_sample_media')
    def test_upload_template_media_delegates_with_mime_and_name(self, mock_upload_sample, mock_upload_media):
        mock_upload_sample.return_value = 'h:handle-xyz'
        mock_upload_media.return_value = 'media-abc'
        file = SimpleUploadedFile('foto.png', b'conteudo', content_type='image/png')

        result = services.upload_template_media(self.company, self.account.pk, file)

        self.assertEqual(result, {'handle': 'h:handle-xyz', 'media_id': 'media-abc'})
        mock_upload_sample.assert_called_once_with(self.account, b'conteudo', 'image/png', 'foto.png')
        mock_upload_media.assert_called_once_with(self.account, b'conteudo', 'image/png', 'foto.png')

    @patch('apps.chats.whatsapp.upload_sample_media')
    def test_upload_template_media_rejects_unsupported_type(self, mock_upload):
        file = SimpleUploadedFile('audio.mp3', b'x', content_type='audio/mpeg')
        with self.assertRaises(ValidationError):
            services.upload_template_media(self.company, self.account.pk, file)
        mock_upload.assert_not_called()

    @patch('apps.chats.whatsapp.upload_sample_media')
    def test_upload_template_media_document_requires_pdf(self, mock_upload):
        file = SimpleUploadedFile('planilha.xlsx', b'x', content_type='application/vnd.ms-excel')
        with self.assertRaises(ValidationError):
            services.upload_template_media(self.company, self.account.pk, file)
        mock_upload.assert_not_called()

    @patch('apps.chats.whatsapp.upload_sample_media')
    def test_upload_template_media_rejects_oversized_file(self, mock_upload):
        big_content = b'x' * (5 * 2**20 + 1)
        file = SimpleUploadedFile('foto.png', big_content, content_type='image/png')
        with self.assertRaises(ValidationError):
            services.upload_template_media(self.company, self.account.pk, file)
        mock_upload.assert_not_called()


class TemplateEndpointTests(BaseChatsTestCase):
    """Endpoints de template: leitura é member, mutação é só owner (get_owned_company)."""

    def setUp(self):
        super().setUp()
        self.member = User.objects.create_user(email='member@a.com', password='strongpass123')
        Membership.objects.create(company=self.company, user=self.member)
        self.member_auth = auth_header(self.member)

    def create_payload(self, **overrides) -> dict:
        payload = {
            'name': 'boas_vindas', 'language': 'pt_BR', 'category': 'MARKETING',
            'body_text': 'Oi, tudo bem?', 'body_examples': [],
        }
        payload.update(overrides)
        return payload

    @patch('apps.chats.whatsapp.list_templates')
    def test_get_template_detail(self, mock_list):
        mock_list.return_value = TEMPLATES_FIXTURE
        response = self.client.get(
            f'/api/companies/{self.company.pk}/accounts/{self.account.pk}/templates/tpl-oferta', headers=self.auth
        )
        self.assertEqual(response.status_code, 200, response.json())
        self.assertEqual(response.json()['name'], 'oferta')

    @patch('apps.chats.whatsapp.list_templates')
    def test_get_template_detail_on_foreign_account_returns_404(self, mock_list):
        response = self.client.get(
            f'/api/companies/{self.company.pk}/accounts/{self.other_account.pk}/templates/tpl-oferta', headers=self.auth
        )
        self.assertEqual(response.status_code, 404)
        mock_list.assert_not_called()

    @patch('apps.chats.whatsapp.create_template')
    def test_owner_creates_template(self, mock_create):
        mock_create.return_value = {'id': 'tpl-1', 'status': 'PENDING', 'category': 'MARKETING'}
        response = self.client.post(
            f'/api/companies/{self.company.pk}/accounts/{self.account.pk}/templates',
            data=json.dumps(self.create_payload()), content_type='application/json', headers=self.auth,
        )
        self.assertEqual(response.status_code, 201, response.json())
        self.assertEqual(response.json(), {'id': 'tpl-1', 'status': 'PENDING', 'category': 'MARKETING'})

    @patch('apps.chats.whatsapp.create_template')
    def test_member_cannot_create_template(self, mock_create):
        response = self.client.post(
            f'/api/companies/{self.company.pk}/accounts/{self.account.pk}/templates',
            data=json.dumps(self.create_payload()), content_type='application/json', headers=self.member_auth,
        )
        self.assertEqual(response.status_code, 404)
        mock_create.assert_not_called()

    @patch('apps.chats.whatsapp.create_template')
    def test_create_template_meta_error_leaks_real_message(self, mock_create):
        mock_create.side_effect = ValidationError('Nome de template duplicado')
        response = self.client.post(
            f'/api/companies/{self.company.pk}/accounts/{self.account.pk}/templates',
            data=json.dumps(self.create_payload()), content_type='application/json', headers=self.auth,
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['detail'], 'Nome de template duplicado')

    @patch('apps.chats.whatsapp.edit_template')
    def test_owner_edits_template(self, mock_edit):
        mock_edit.return_value = {'success': True}
        response = self.client.patch(
            f'/api/companies/{self.company.pk}/accounts/{self.account.pk}/templates/tpl-1',
            data=json.dumps(self.create_payload()), content_type='application/json', headers=self.auth,
        )
        self.assertEqual(response.status_code, 200, response.json())
        self.assertEqual(response.json(), {'success': True})

    @patch('apps.chats.whatsapp.edit_template')
    def test_member_cannot_edit_template(self, mock_edit):
        response = self.client.patch(
            f'/api/companies/{self.company.pk}/accounts/{self.account.pk}/templates/tpl-1',
            data=json.dumps(self.create_payload()), content_type='application/json', headers=self.member_auth,
        )
        self.assertEqual(response.status_code, 404)
        mock_edit.assert_not_called()

    @patch('apps.chats.whatsapp.delete_template')
    def test_owner_deletes_template(self, mock_delete):
        response = self.client.delete(
            f'/api/companies/{self.company.pk}/accounts/{self.account.pk}/templates/tpl-1?name=boas_vindas',
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 204)
        mock_delete.assert_called_once_with(self.account, 'boas_vindas', 'tpl-1')

    @patch('apps.chats.whatsapp.delete_template')
    def test_member_cannot_delete_template(self, mock_delete):
        response = self.client.delete(
            f'/api/companies/{self.company.pk}/accounts/{self.account.pk}/templates/tpl-1?name=boas_vindas',
            headers=self.member_auth,
        )
        self.assertEqual(response.status_code, 404)
        mock_delete.assert_not_called()

    @patch('apps.chats.whatsapp.upload_media')
    @patch('apps.chats.whatsapp.upload_sample_media')
    def test_owner_uploads_template_media(self, mock_upload_sample, mock_upload_media):
        mock_upload_sample.return_value = 'h:handle-xyz'
        mock_upload_media.return_value = 'media-abc'
        file = SimpleUploadedFile('foto.png', b'conteudo', content_type='image/png')

        response = self.client.post(
            f'/api/companies/{self.company.pk}/accounts/{self.account.pk}/templates/media',
            data={'file': file}, headers=self.auth,
        )

        self.assertEqual(response.status_code, 201, response.json())
        self.assertEqual(response.json(), {'handle': 'h:handle-xyz', 'media_id': 'media-abc'})

    @patch('apps.chats.whatsapp.upload_sample_media')
    def test_member_cannot_upload_template_media(self, mock_upload):
        file = SimpleUploadedFile('foto.png', b'conteudo', content_type='image/png')
        response = self.client.post(
            f'/api/companies/{self.company.pk}/accounts/{self.account.pk}/templates/media',
            data={'file': file}, headers=self.member_auth,
        )
        self.assertEqual(response.status_code, 404)
        mock_upload.assert_not_called()


class AccountMediaUploadTests(BaseChatsTestCase):
    """POST /accounts/{id}/media: upload de mídia real pra envio (Media Upload API, não a Resumable de exemplo)."""

    @patch('apps.chats.whatsapp.upload_media')
    def test_upload_returns_media_id(self, mock_upload):
        mock_upload.return_value = 'media-abc'
        file = SimpleUploadedFile('foto.png', b'conteudo', content_type='image/png')

        response = self.client.post(
            f'/api/companies/{self.company.pk}/accounts/{self.account.pk}/media',
            data={'file': file}, headers=self.auth,
        )

        self.assertEqual(response.status_code, 201, response.json())
        self.assertEqual(response.json(), {'media_id': 'media-abc'})
        mock_upload.assert_called_once_with(self.account, b'conteudo', 'image/png', 'foto.png')

    @patch('apps.chats.whatsapp.upload_media')
    def test_upload_on_foreign_account_returns_404(self, mock_upload):
        file = SimpleUploadedFile('foto.png', b'conteudo', content_type='image/png')
        response = self.client.post(
            f'/api/companies/{self.company.pk}/accounts/{self.other_account.pk}/media',
            data={'file': file}, headers=self.auth,
        )
        self.assertEqual(response.status_code, 404)
        mock_upload.assert_not_called()


class PipedriveToolsTests(BaseChatsTestCase):
    """Tools de CRM na task: presentes só com PipedriveAccount, erro vira string (nunca retry)."""

    def setUp(self):
        super().setUp()
        patcher = patch('apps.chats.tasks.update_contact_memory.delay')
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run_task_and_get_tools(self, mock_generate, mock_send):
        mock_generate.return_value = 'ok'
        mock_send.return_value = 'wamid.out'
        incoming = Message.objects.get(wa_message_id='wamid.1')
        tasks.process_incoming_message(incoming.pk)
        return mock_generate.call_args.kwargs['tools']

    @patch('apps.chats.whatsapp.send_text')
    @patch('apps.ai.generate_reply')
    def test_no_pipedrive_account_no_crm_tools(self, mock_generate, mock_send):
        tools = self._run_task_and_get_tools(mock_generate, mock_send)
        self.assertEqual([t.__name__ for t in tools], ['transferir_para_atendente'])

    @patch('apps.chats.whatsapp.send_text')
    @patch('apps.ai.generate_reply')
    def test_pipedrive_account_adds_crm_tools(self, mock_generate, mock_send):
        PipedriveAccount.objects.create(company=self.company, api_key='pd-key', default_pipeline_id=1, default_stage_id=2)
        tools = self._run_task_and_get_tools(mock_generate, mock_send)
        self.assertEqual(
            [t.__name__ for t in tools], ['transferir_para_atendente', 'criar_negocio', 'anotar_crm']
        )

    @patch('apps.crm.pipedrive.list_open_deals', return_value=[])
    @patch('apps.crm.pipedrive.create_deal')
    @patch('apps.crm.pipedrive.search_persons')
    @patch('apps.chats.whatsapp.send_text')
    @patch('apps.ai.generate_reply')
    def test_criar_negocio_finds_person_and_creates_deal(self, mock_generate, mock_send, mock_search, mock_create_deal, _mock_open):
        PipedriveAccount.objects.create(company=self.company, api_key='pd-key', default_pipeline_id=1, default_stage_id=2)
        mock_search.return_value = [{'id': 88, 'name': 'Cliente A'}]
        mock_create_deal.return_value = {'id': 7, 'title': 'Cliente A'}
        tools = self._run_task_and_get_tools(mock_generate, mock_send)

        result = tools[1]('Cliente A')

        self.assertEqual(result, 'Negócio criado no CRM (id 7)')
        mock_create_deal.assert_called_once_with('pd-key', 'Cliente A', 88, 1, 2, None)
        self.contact.refresh_from_db()
        self.assertEqual(self.contact.pipedrive_person_id, 88)

    @patch('apps.crm.pipedrive.create_note')
    @patch('apps.crm.pipedrive.create_person')
    @patch('apps.crm.pipedrive.search_persons')
    @patch('apps.chats.whatsapp.send_text')
    @patch('apps.ai.generate_reply')
    def test_anotar_crm_creates_person_and_note(self, mock_generate, mock_send, mock_search, mock_create_person, mock_note):
        PipedriveAccount.objects.create(company=self.company, api_key='pd-key')
        mock_search.return_value = []
        mock_create_person.return_value = {'id': 51}
        tools = self._run_task_and_get_tools(mock_generate, mock_send)

        result = tools[2]('Orçamento de 5 mil')

        self.assertEqual(result, 'Anotação salva no CRM')
        mock_note.assert_called_once_with('pd-key', 'Orçamento de 5 mil', person_id=51)
        self.contact.refresh_from_db()
        self.assertEqual(self.contact.pipedrive_person_id, 51)

    @patch('apps.crm.pipedrive.search_persons')
    @patch('apps.chats.whatsapp.send_text')
    @patch('apps.ai.generate_reply')
    def test_tool_error_returns_string_and_task_does_not_retry(self, mock_generate, mock_send, mock_search):
        PipedriveAccount.objects.create(company=self.company, api_key='pd-key', default_pipeline_id=1, default_stage_id=2)
        mock_search.side_effect = PipedriveError('Pipedrive rate limit reached, try again soon')
        # IA chama a tool no meio da geração: erro do Pipedrive não pode estourar a task
        mock_generate.side_effect = lambda *args, **kwargs: kwargs['tools'][1]('Cliente A')
        mock_send.return_value = 'wamid.out'
        incoming = Message.objects.get(wa_message_id='wamid.1')

        tasks.process_incoming_message(incoming.pk)  # sem raise = sem retry

        reply = mock_send.call_args.args[2]
        self.assertEqual(reply, 'Falha ao criar negócio: Pipedrive rate limit reached, try again soon')

    @patch('apps.crm.pipedrive.list_open_deals', return_value=[])
    @patch('apps.crm.pipedrive.search_persons')
    @patch('apps.chats.whatsapp.send_text')
    @patch('apps.ai.generate_reply')
    def test_criar_negocio_without_defaults_returns_error_string(self, mock_generate, mock_send, mock_search, _mock_open):
        PipedriveAccount.objects.create(company=self.company, api_key='pd-key')
        mock_search.return_value = [{'id': 88}]
        tools = self._run_task_and_get_tools(mock_generate, mock_send)

        result = tools[1]('Cliente A')

        self.assertEqual(result, 'Falha ao criar negócio: default pipeline and stage not set')
        mock_search.assert_not_called()


class PrivateMediaStorageTests(TestCase):
    """Mídia privada: URL assinada no endereço público (Caddy /media), nunca pública nem no host interno."""

    @override_settings(MINIO_PUBLIC_URL='https://app.chatly.ia.br')
    def test_url_is_signed_for_public_host_with_bucket_in_path(self):
        from urllib.parse import parse_qs, urlparse

        from core.storage import PrivateMediaStorage

        storage = PrivateMediaStorage(
            endpoint_url='http://minio:9000', access_key='k', secret_key='s', bucket_name='media',
            region_name='us-east-1', querystring_expire=7200,
        )
        url = urlparse(storage.url('chat/abc/foto.jpg'))
        query = parse_qs(url.query)
        self.assertEqual((url.scheme, url.netloc, url.path), ('https', 'app.chatly.ia.br', '/media/chat/abc/foto.jpg'))
        self.assertEqual(query['X-Amz-Expires'], ['7200'])
        self.assertIn('X-Amz-Signature', query)

    def test_default_storage_is_private(self):
        from django.conf import settings

        options = settings.STORAGES['default']['OPTIONS']
        self.assertEqual(settings.STORAGES['default']['BACKEND'], 'core.storage.PrivateMediaStorage')
        self.assertIsNone(options['default_acl'])
        self.assertTrue(options['querystring_auth'])


class EnsureMediaBucketTests(TestCase):
    def _run(self, client, **kwargs):
        from io import StringIO

        from django.core.management import call_command

        storage = MagicMock(bucket_name='media')
        storage.connection.meta.client = client
        out = StringIO()
        with patch('apps.chats.management.commands.ensure_media_bucket.default_storage', storage):
            call_command('ensure_media_bucket', stdout=out, **kwargs)
        return out.getvalue()

    def test_existing_bucket_is_left_alone(self):
        client = MagicMock()
        self.assertIn('ok', self._run(client))
        client.create_bucket.assert_not_called()

    def test_missing_bucket_is_created(self):
        from botocore.exceptions import ClientError

        client = MagicMock()
        client.head_bucket.side_effect = ClientError({'Error': {'Code': '404'}}, 'HeadBucket')
        self.assertIn('criado', self._run(client))
        client.create_bucket.assert_called_once_with(Bucket='media')
        client.put_bucket_policy.assert_not_called()  # nunca público

    @patch('apps.chats.management.commands.ensure_media_bucket.time.sleep')
    def test_waits_for_minio_to_come_up(self, mock_sleep):
        from botocore.exceptions import EndpointConnectionError

        client = MagicMock()
        client.head_bucket.side_effect = [EndpointConnectionError(endpoint_url='http://minio:9000'), None]
        self.assertIn('ok', self._run(client, wait=30))
        mock_sleep.assert_called_once()


class ImportMediaCommandTests(BaseChatsTestCase):
    @patch.dict('os.environ', {
        'OLD_MINIO_ENDPOINT_URL': 'https://s3.antigo', 'OLD_MINIO_ACCESS_KEY': 'k',
        'OLD_MINIO_SECRET_KEY': 's', 'OLD_MINIO_BUCKET_NAME': 'hub',
    })
    def test_copies_only_referenced_files_keeping_content_type(self):
        from io import StringIO

        from botocore.exceptions import ClientError
        from django.core.management import call_command

        Message.objects.filter(wa_message_id='wamid.1').update(media_file='chat/a/foto.jpg')
        Sticker.objects.bulk_create([Sticker(company=self.company, file='chat/b/s.webp'), Sticker(company=self.company, file='chat/c/sumiu.webp')])

        def get_object(Bucket, Key):
            if Key == 'chat/c/sumiu.webp':
                raise ClientError({'Error': {'Code': 'NoSuchKey'}}, 'GetObject')
            return {'Body': MagicMock(read=MagicMock(return_value=b'bytes-' + Key.encode())), 'ContentType': 'image/jpeg' if Key.endswith('.jpg') else ''}

        old = MagicMock(get_object=MagicMock(side_effect=get_object))
        storage = MagicMock(bucket_name='media')
        storage.exists.side_effect = lambda name: name == 'chat/b/s.webp'  # já migrado antes
        out = StringIO()
        with patch('apps.chats.management.commands.import_media.boto3.client', return_value=old), \
                patch('apps.chats.management.commands.import_media.default_storage', storage):
            call_command('import_media', stdout=out, stderr=StringIO())

        put = storage.connection.meta.client.put_object
        put.assert_called_once_with(Bucket='media', Key='chat/a/foto.jpg', Body=b'bytes-chat/a/foto.jpg', ContentType='image/jpeg')
        self.assertIn('3 referenciados: 1 copiados, 1 já existiam, 1 faltando na origem', out.getvalue())


class CampaignFlowAndTagsTests(BaseChatsTestCase):
    """Campanha com fluxo no clique e filtro por etiqueta."""

    url = CampaignTests.url
    campaign_payload = CampaignTests.campaign_payload
    post_campaign = CampaignTests.post_campaign

    def setUp(self):
        super().setUp()
        self.mock_delay = self.start_patch('apps.chats.tasks.run_campaign.delay')
        self.start_patch('apps.chats.whatsapp.list_templates', return_value=TEMPLATES_FIXTURE)
        self.vip = Tag.objects.create(company=self.company, name='vip')
        self.blocked = Tag.objects.create(company=self.company, name='bloqueado')
        self.second = Contact.objects.create(account=self.account, wa_id='5511911112222')

    def start_patch(self, target, **kwargs):
        patcher = patch(target, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def make_flow(self, **kwargs):
        from apps.automations.models import Flow

        defaults = dict(company=self.company, account=self.account, name='F', trigger=Flow.Trigger.CAMPAIGN)
        defaults.update(kwargs)
        return Flow.objects.create(**defaults)

    def test_campaign_saves_flow(self):
        flow = self.make_flow()
        response = self.post_campaign(self.company.pk, flow_id=flow.pk)
        self.assertEqual(response.status_code, 201, response.json())
        self.assertEqual(response.json()['flow'], flow.pk)

    def test_campaign_rejects_flow_with_other_trigger_or_company(self):
        from apps.automations.models import Flow

        wrong_trigger = self.make_flow(trigger=Flow.Trigger.KEYWORD)
        foreign = self.make_flow(company=self.other_company, account=self.other_account)
        for flow in (wrong_trigger, foreign):
            with self.subTest(flow=flow.pk):
                self.assertEqual(self.post_campaign(self.company.pk, flow_id=flow.pk).status_code, 400)
        self.mock_delay.assert_not_called()

    def test_tag_groups_filter_all_contacts(self):
        self.second.tags.add(self.vip)
        response = self.post_campaign(self.company.pk, tag_groups=[[self.vip.pk]])
        self.assertEqual(response.status_code, 201, response.json())
        self.mock_delay.assert_called_once_with(response.json()['id'], [self.second.pk])

    def test_tag_groups_are_or_of_and(self):
        x, y, z = (Tag.objects.create(company=self.company, name=n) for n in ('x', 'y', 'z'))
        only_x = Contact.objects.create(account=self.account, wa_id='5511900000001')
        x_and_y = Contact.objects.create(account=self.account, wa_id='5511900000002')
        only_z = Contact.objects.create(account=self.account, wa_id='5511900000003')
        z_blocked = Contact.objects.create(account=self.account, wa_id='5511900000004')
        only_x.tags.add(x)
        x_and_y.tags.add(x, y)
        only_z.tags.add(z)
        z_blocked.tags.add(z, self.blocked)
        audience = services.campaign_audience(self.account, [[x.pk, y.pk], [z.pk], []], [self.blocked.pk])
        self.assertEqual(set(audience), {x_and_y, only_z})
        self.assertEqual(services.campaign_audience(self.account, []).count(), Contact.objects.filter(account=self.account).count())

        response = self.client.post(
            f'/api/companies/{self.company.pk}/campaigns/audience',
            data=json.dumps({'account_id': self.account.pk, 'tag_groups': [[x.pk, y.pk], [z.pk]], 'exclude_tag_ids': [self.blocked.pk]}),
            content_type='application/json', headers=self.auth,
        )
        self.assertEqual(response.json()['count'], 2)
        response = self.client.post(
            f'/api/companies/{self.company.pk}/campaigns/audience',
            data=json.dumps({'account_id': self.other_account.pk}), content_type='application/json', headers=self.auth,
        )
        self.assertEqual(response.status_code, 404)

    def test_exclude_tags_applies_to_every_mode(self):
        self.second.tags.add(self.blocked)
        response = self.post_campaign(
            self.company.pk, contact_ids=[], numbers=[{'number': self.second.wa_id}], exclude_tag_ids=[self.blocked.pk]
        )
        self.assertEqual(response.status_code, 400)  # o único destinatário do CSV está bloqueado
        response = self.post_campaign(self.company.pk, exclude_tag_ids=[self.blocked.pk])
        self.mock_delay.assert_called_once_with(response.json()['id'], [self.contact.pk])


@patch('apps.chats.events.publish')
class TagTests(BaseChatsTestCase):
    def setUp(self):
        super().setUp()
        self.base = f'/api/companies/{self.company.pk}'

    def test_create_list_and_case_insensitive_reuse(self, mock_pub):
        r1 = self.client.post(f'{self.base}/tags', data={'name': 'Bloqueado', 'color': 'error'}, content_type='application/json', headers=self.auth)
        r2 = self.client.post(f'{self.base}/tags', data={'name': ' bloqueado '}, content_type='application/json', headers=self.auth)
        self.assertEqual(r1.status_code, 201)
        self.assertEqual(r1.json()['id'], r2.json()['id'])
        listed = self.client.get(f'{self.base}/tags', headers=self.auth).json()
        self.assertEqual([t['name'] for t in listed], ['Bloqueado'])

    def test_add_remove_tag_on_contact_and_list_filter(self, mock_pub):
        tag = Tag.objects.create(company=self.company, name='vip')
        r = self.client.post(f'{self.base}/contacts/{self.contact.pk}/tags/{tag.pk}', headers=self.auth)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['tags'], [{'id': tag.pk, 'name': 'vip', 'color': 'neutral'}])
        listed = self.client.get(f'{self.base}/whatsapp-accounts/{self.account.pk}/contacts?tag={tag.pk}', headers=self.auth).json()
        self.assertEqual([c['id'] for c in listed['items']], [self.contact.pk])
        r = self.client.delete(f'{self.base}/contacts/{self.contact.pk}/tags/{tag.pk}', headers=self.auth)
        self.assertEqual(r.json()['tags'], [])

    def test_foreign_tag_or_company_is_404(self, mock_pub):
        foreign = Tag.objects.create(company=self.other_company, name='alheia')
        r = self.client.post(f'{self.base}/contacts/{self.contact.pk}/tags/{foreign.pk}', headers=self.auth)
        self.assertEqual(r.status_code, 404)
        r = self.client.get(f'/api/companies/{self.other_company.pk}/tags', headers=self.auth)
        self.assertEqual(r.status_code, 404)
        r = self.client.delete(f'{self.base}/tags/{foreign.pk}', headers=self.auth)
        self.assertEqual(r.status_code, 404)

    def test_delete_tag(self, mock_pub):
        tag = Tag.objects.create(company=self.company, name='vip')
        self.contact.tags.add(tag)
        self.assertEqual(self.client.delete(f'{self.base}/tags/{tag.pk}', headers=self.auth).status_code, 204)
        self.assertFalse(self.contact.tags.exists())


@patch('apps.chats.events.publish')
class ContactFieldTests(BaseChatsTestCase):
    url = CampaignTests.url
    campaign_payload = CampaignTests.campaign_payload
    post_campaign = CampaignTests.post_campaign

    def setUp(self):
        super().setUp()
        self.base = f'/api/companies/{self.company.pk}'
        self.plano = ContactField.objects.create(company=self.company, key='plano', label='Plano')

    def post(self, path, data):
        return self.client.post(f'{self.base}{path}', data=json.dumps(data), content_type='application/json', headers=self.auth)

    def test_create_field_slugifies_and_rejects_duplicate_or_reserved(self, mock_pub):
        r = self.post('/contact-fields', {'label': 'Data de Nascimento'})
        self.assertEqual(r.status_code, 201, r.json())
        self.assertEqual(r.json()['key'], 'data_de_nascimento')
        self.assertEqual(self.post('/contact-fields', {'label': 'data de nascimento'}).status_code, 409)
        self.assertEqual(self.post('/contact-fields', {'label': 'Nome'}).status_code, 400)
        listed = self.client.get(f'{self.base}/contact-fields', headers=self.auth).json()
        self.assertEqual([f['key'] for f in listed], ['data_de_nascimento', 'plano'])
        self.assertEqual(self.client.get(f'/api/companies/{self.other_company.pk}/contact-fields', headers=self.auth).status_code, 404)

    def test_patch_contact_sets_and_clears_fields(self, mock_pub):
        url = f'{self.base}/contacts/{self.contact.pk}'
        r = self.client.patch(url, data={'custom_fields': {'plano': ' Gold '}}, content_type='application/json', headers=self.auth)
        self.assertEqual(r.json()['custom_fields'], {'plano': 'Gold'})
        r = self.client.patch(url, data={'custom_fields': {'plano': ''}}, content_type='application/json', headers=self.auth)
        self.assertEqual(r.json()['custom_fields'], {})
        r = self.client.patch(url, data={'custom_fields': {'cpf': '1'}}, content_type='application/json', headers=self.auth)
        self.assertEqual(r.status_code, 400)

    def test_resolve_params_uses_fields_and_refuses_blank(self, mock_pub):
        self.contact.custom_fields = {'plano': 'Gold'}
        self.assertEqual(
            services.resolve_params(['{{nome}}', 'plano {{ plano }}', '{{telefone}}'], self.contact),
            ['Cliente A', 'plano Gold', '5511999999999'],
        )
        self.contact.custom_fields = {}
        with self.assertRaises(ValidationError):
            services.resolve_params(['{{plano}}'], self.contact)

    @patch('apps.chats.tasks.run_campaign.delay')
    @patch('apps.chats.whatsapp.list_templates', return_value=TEMPLATES_FIXTURE)
    def test_campaign_csv_saves_fields_and_rejects_unknown_variable(self, mock_list, mock_delay, mock_pub):
        r = self.post_campaign(self.company.pk, body_params=['{{nome}}', '{{cpf}}'])
        self.assertEqual(r.status_code, 400)
        self.assertIn('{{cpf}}', r.json()['detail'])
        r = self.post_campaign(
            self.company.pk, body_params=['{{nome}}', '{{plano}}'], contact_ids=[],
            numbers=[{'number': '5511988887777', 'name': 'Novo', 'fields': {'plano': 'Prata', 'ignorada': 'x'}}],
        )
        self.assertEqual(r.status_code, 201, r.json())
        self.assertEqual(Contact.objects.get(wa_id='5511988887777').custom_fields, {'plano': 'Prata'})

    @patch('apps.chats.tasks.run_campaign.delay')
    @patch('apps.chats.whatsapp.list_templates', return_value=TEMPLATES_FIXTURE)
    def test_import_tags_all_imported_contacts(self, mock_list, mock_delay, mock_pub):
        lista = Tag.objects.create(company=self.company, name='lista_outubro')
        r = self.post(f'/whatsapp-accounts/{self.account.pk}/contacts/import', {
            'numbers': [{'number': self.contact.wa_id}, {'number': '5511977776666'}], 'tag_ids': [lista.pk],
        })
        self.assertEqual(r.status_code, 200, r.json())
        self.assertEqual(Contact.objects.filter(tags=lista).count(), 2)

        r = self.post_campaign(self.company.pk, contact_ids=[], numbers=[{'number': '5511955554444'}], import_tag_ids=[lista.pk])
        self.assertEqual(r.status_code, 201, r.json())
        self.assertTrue(Contact.objects.get(wa_id='5511955554444').tags.filter(pk=lista.pk).exists())

        foreign = Tag.objects.create(company=self.other_company, name='alheia')
        r = self.post(f'/whatsapp-accounts/{self.account.pk}/contacts/import', {'numbers': [{'number': '5511911110000'}], 'tag_ids': [foreign.pk]})
        self.assertEqual(r.status_code, 404)

    def test_import_contacts_updates_fields_of_existing(self, mock_pub):
        r = self.post(f'/whatsapp-accounts/{self.account.pk}/contacts/import', {
            'numbers': [{'number': self.contact.wa_id, 'fields': {'plano': 'Gold'}}, {'number': '5511977776666', 'fields': {'plano': ''}}],
        })
        self.assertEqual(r.json(), {'created': 1, 'existing': 1})
        self.contact.refresh_from_db()
        self.assertEqual(self.contact.custom_fields, {'plano': 'Gold'})
        self.assertEqual(Contact.objects.get(wa_id='5511977776666').custom_fields, {})

    @patch('apps.chats.whatsapp.send_template', return_value='wamid.ok')
    @patch('apps.chats.whatsapp.list_templates', return_value=TEMPLATES_FIXTURE)
    def test_run_campaign_fails_only_contact_without_value(self, mock_list, mock_send, mock_pub):
        self.contact.custom_fields = {'plano': 'Gold'}
        self.contact.save()
        empty = Contact.objects.create(account=self.account, wa_id='5511966665555')
        campaign = Campaign.objects.create(
            account=self.account, name='c', template_name='oferta', template_language='pt_BR', body_params=['{{nome}}', '{{plano}}'], total=2
        )
        tasks.run_campaign(campaign.pk, [self.contact.pk, empty.pk])
        campaign.refresh_from_db()
        self.assertEqual((campaign.sent, campaign.failed), (1, 1))
        self.assertEqual(mock_send.call_args.args[4], ['Cliente A', 'Gold'])

    def test_ai_memory_includes_filled_fields(self, mock_pub):
        self.contact.memory = 'Gosta de café.'
        self.contact.custom_fields = {'plano': 'Gold', 'apagado': 'x'}
        self.assertEqual(tasks._memory_with_fields(self.contact), 'Gosta de café.\n\nDados cadastrados:\nPlano: Gold')


class ScheduledCampaignTests(BaseChatsTestCase):
    url = CampaignTests.url
    campaign_payload = CampaignTests.campaign_payload
    post_campaign = CampaignTests.post_campaign

    def setUp(self):
        super().setUp()
        for target, kwargs in (('apps.chats.whatsapp.list_templates', {'return_value': TEMPLATES_FIXTURE}),):
            patcher = patch(target, **kwargs)
            self.addCleanup(patcher.stop)
            patcher.start()

    @patch('apps.chats.tasks.run_campaign.delay')
    def test_scheduled_campaign_waits_then_dispatches_once(self, mock_delay):
        at = timezone.now() + timedelta(hours=2)
        response = self.post_campaign(self.company.pk, scheduled_at=at.isoformat())
        self.assertEqual(response.status_code, 201, response.json())
        body = response.json()
        self.assertEqual(body['status'], 'scheduled')
        mock_delay.assert_not_called()

        tasks.dispatch_scheduled_campaigns()  # ainda não venceu
        mock_delay.assert_not_called()

        Campaign.objects.filter(pk=body['id']).update(scheduled_at=timezone.now() - timedelta(seconds=1))
        tasks.dispatch_scheduled_campaigns()
        tasks.dispatch_scheduled_campaigns()  # próximo minuto do beat não repete
        mock_delay.assert_called_once_with(body['id'], [self.contact.pk])
        self.assertEqual(Campaign.objects.get(pk=body['id']).status, 'sending')

    @patch('apps.chats.tasks.run_campaign.delay')
    def test_past_schedule_is_rejected_and_cancel_only_scheduled(self, mock_delay):
        response = self.post_campaign(self.company.pk, scheduled_at=(timezone.now() - timedelta(minutes=1)).isoformat())
        self.assertEqual(response.status_code, 400)

        scheduled = self.post_campaign(self.company.pk, scheduled_at=(timezone.now() + timedelta(days=1)).isoformat()).json()
        cancel_url = f'{self.url(self.company.pk)}/{scheduled["id"]}/cancel'
        response = self.client.post(cancel_url, headers=self.auth)
        self.assertEqual(response.json()['status'], 'canceled')
        self.assertEqual(self.client.post(cancel_url, headers=self.auth).status_code, 409)

        Campaign.objects.filter(pk=scheduled['id']).update(scheduled_at=timezone.now() - timedelta(seconds=1))
        tasks.dispatch_scheduled_campaigns()  # cancelada não dispara
        mock_delay.assert_not_called()



@patch('apps.chats.events.publish')
class AIToolsTests(BaseChatsTestCase):
    def tools(self, *native):
        from apps.chats import ai_tools

        Agent.objects.filter(pk=self.agent.pk).update(native_tools=list(native))
        self.contact.refresh_from_db()
        tools, state = ai_tools.build_tools(self.contact)
        return {getattr(t, 'name', None) or t.__name__: t for t in tools}, state

    @staticmethod
    def call(tool, **kwargs):
        return tool.entrypoint(**kwargs) if hasattr(tool, 'entrypoint') else tool(**kwargs)

    def test_only_enabled_native_tools_are_offered(self, mock_pub):
        ContactField.objects.create(company=self.company, key='cad', label='CAD')
        Tag.objects.create(company=self.company, name='quer_teste')
        names, _ = self.tools('handoff')
        self.assertEqual(list(names), ['transferir_para_atendente'])
        names, _ = self.tools('handoff', 'followup', 'save_field', 'tag')
        self.assertEqual(list(names), ['transferir_para_atendente', 'agendar_retomada', 'salvar_dado_contato', 'etiquetar_contato'])
        ContactField.objects.all().delete()
        Tag.objects.all().delete()
        names, _ = self.tools('save_field', 'tag')
        self.assertEqual(names, {})  # sem campo/etiqueta cadastrada não adianta oferecer

    def test_handoff_logs_reason_and_flags_state(self, mock_pub):
        tools, state = self.tools('handoff')
        self.call(tools['transferir_para_atendente'], motivo='quer desconto no Pro anual')
        self.assertTrue(state['handed_off'])
        self.contact.refresh_from_db()
        self.assertTrue(self.contact.ai_paused)
        self.assertEqual(ToolCall.objects.get().arguments, {'motivo': 'quer desconto no Pro anual'})

    def test_followup_schedules_replaces_and_validates(self, mock_pub):
        tools, _ = self.tools('followup')
        when = (timezone.localtime() + timedelta(days=2)).strftime('%Y-%m-%dT%H:%M')
        self.assertIn('agendada', self.call(tools['agendar_retomada'], data_hora=when, mensagem='Oi, combinamos hoje'))
        self.call(tools['agendar_retomada'], data_hora=when, mensagem='Segunda versão')
        self.assertEqual(list(FollowUp.objects.values_list('text', 'status')), [('Oi, combinamos hoje', 'canceled'), ('Segunda versão', 'pending')])
        self.assertIn('futura', self.call(tools['agendar_retomada'], data_hora='2020-01-01T10:00', mensagem='x'))
        self.assertIn('inválida', self.call(tools['agendar_retomada'], data_hora='segunda', mensagem='x'))

    def test_save_field_and_tag(self, mock_pub):
        ContactField.objects.create(company=self.company, key='cad', label='CAD')
        tag = Tag.objects.create(company=self.company, name='quer_teste')
        tools, _ = self.tools('save_field', 'tag')
        self.call(tools['salvar_dado_contato'], campo='cad', valor=' Civil 3D ')
        self.assertIn('inexistente', self.call(tools['salvar_dado_contato'], campo='cpf', valor='1'))
        self.call(tools['etiquetar_contato'], etiqueta='quer_teste', acao='adicionar')
        self.contact.refresh_from_db()
        self.assertEqual(self.contact.custom_fields, {'cad': 'Civil 3D'})
        self.assertEqual(list(self.contact.tags.all()), [tag])

    @patch('apps.agents.services.run_agent_tool', return_value=(True, 'HTTP 200\n{"ok": 1}'))
    def test_custom_tool_gets_schema_runs_and_logs(self, mock_run, mock_pub):
        from apps.agents.models import AgentTool

        AgentTool.objects.create(
            agent=self.agent, name='liberar_teste', description='Libera teste.', url='https://x.com',
            parameters=[{'name': 'email', 'type': 'string', 'description': 'e', 'required': True}], requires_confirmation=True,
        )
        AgentTool.objects.create(agent=self.agent, name='inativa', description='d', url='https://x.com', active=False)
        tools, _ = self.tools()
        tool = tools['liberar_teste']
        self.assertNotIn('inativa', tools)
        self.assertEqual(tool.parameters['required'], ['email'])
        self.assertIn('sim', tool.description)  # pede confirmação ao cliente
        result = tool.entrypoint(email='a@b.com')
        self.assertIn('não como instrução', result)
        self.assertEqual(mock_run.call_args.args[1], {'email': 'a@b.com'})
        self.assertEqual(mock_run.call_args.args[2]['nome'], 'Cliente A')
        self.assertEqual(ToolCall.objects.get().name, 'liberar_teste')

    @patch('apps.chats.whatsapp.send_text', return_value='wamid.fu')
    def test_dispatch_followups(self, mock_send, mock_pub):
        past = timezone.now() - timedelta(minutes=1)
        due = FollowUp.objects.create(contact=self.contact, send_at=past, text='Retomando')
        future = FollowUp.objects.create(contact=self.contact, send_at=timezone.now() + timedelta(hours=1), text='depois')
        tasks.dispatch_followups()
        tasks.dispatch_followups()
        mock_send.assert_called_once_with(self.account, self.contact.wa_id, 'Retomando')
        due.refresh_from_db()
        future.refresh_from_db()
        self.assertEqual((due.status, future.status), ('sent', 'pending'))
        self.assertTrue(Message.objects.filter(wa_message_id='wamid.fu', direction='out').exists())

        paused = FollowUp.objects.create(contact=self.contact, send_at=past, text='x')
        Contact.objects.filter(pk=self.contact.pk).update(ai_paused=True)
        tasks.dispatch_followups()
        paused.refresh_from_db()
        self.assertEqual(paused.status, 'canceled')

    def test_activity_endpoint_and_cancel(self, mock_pub):
        followup = FollowUp.objects.create(contact=self.contact, send_at=timezone.now() + timedelta(days=1), text='x')
        ToolCall.objects.create(contact=self.contact, name='agendar_retomada', arguments={}, result='ok')
        base = f'/api/companies/{self.company.pk}/contacts/{self.contact.pk}'
        body = self.client.get(f'{base}/ai-activity', headers=self.auth).json()
        self.assertEqual([c['name'] for c in body['tool_calls']], ['agendar_retomada'])
        self.assertEqual(body['followups'][0]['status'], 'pending')
        self.assertEqual(self.client.post(f'{base}/followups/{followup.pk}/cancel', headers=self.auth).json()['status'], 'canceled')
        self.assertEqual(self.client.post(f'{base}/followups/{followup.pk}/cancel', headers=self.auth).status_code, 409)



class PipedriveOpenDealTests(BaseChatsTestCase):
    @patch('apps.crm.pipedrive.create_deal')
    @patch('apps.crm.pipedrive.list_open_deals', return_value=[{'id': 5, 'title': 'Negócio antigo', 'pipeline_id': 1}])
    def test_existing_open_deal_is_reused_not_duplicated(self, mock_open, mock_create):
        from apps.chats import ai_tools
        from apps.crm.models import PipedriveAccount

        PipedriveAccount.objects.create(company=self.company, api_key='pd-key', default_pipeline_id=1, default_stage_id=2)
        Contact.objects.filter(pk=self.contact.pk).update(pipedrive_person_id=88)
        self.contact.refresh_from_db()
        tools, _ = ai_tools.build_tools(self.contact)
        result = next(t for t in tools if t.__name__ == 'criar_negocio')(titulo='Cliente A')
        self.assertIn('Já existe negócio aberto', result)
        mock_create.assert_not_called()
        mock_open.assert_called_once_with('pd-key', 88)

    @patch('apps.crm.pipedrive.create_deal', return_value={'id': 9, 'title': 'Cliente A'})
    @patch('apps.crm.pipedrive.list_open_deals', return_value=[{'id': 5, 'title': 'Recuperação', 'pipeline_id': 7}])
    def test_open_deal_in_other_pipeline_is_not_reused(self, mock_open, mock_create):
        from apps.chats import ai_tools
        from apps.crm.models import PipedriveAccount

        PipedriveAccount.objects.create(company=self.company, api_key='pd-key', default_pipeline_id=1, default_stage_id=2)
        Contact.objects.filter(pk=self.contact.pk).update(pipedrive_person_id=88)
        self.contact.refresh_from_db()
        tools, _ = ai_tools.build_tools(self.contact)
        next(t for t in tools if t.__name__ == 'criar_negocio')(titulo='Cliente A')
        mock_create.assert_called_once_with('pd-key', 'Cliente A', 88, 1, 2, None)  # funil padrão, não o de recuperação



class PromptVariablesTests(BaseChatsTestCase):
    def test_render_prompt_fills_contact_fields(self):
        ContactField.objects.create(company=self.company, key='cad', label='CAD')
        ContactField.objects.create(company=self.company, key='plano', label='Plano')
        self.contact.custom_fields = {'cad': 'Civil 3D'}
        prompt = 'Cliente {{nome}} usa {{ cad }} no plano {{plano}}. Template usa {{1}}.'
        self.assertEqual(
            ai.render_prompt(prompt, tasks._prompt_variables(self.contact)),
            'Cliente Cliente A usa Civil 3D no plano não informado. Template usa {{1}}.',
        )



class PlaygroundTests(BaseChatsTestCase):
    def url(self):
        return f'/api/companies/{self.company.pk}/agents/{self.agent.pk}/playground'

    def post(self, **body):
        data = {'messages': [{'role': 'user', 'content': 'uso civil 3d, me chama segunda'}], **body}
        return self.client.post(self.url(), data=json.dumps(data), content_type='application/json', headers=self.auth)

    @patch('apps.ai.generate_reply')
    def test_tools_are_simulated_and_nothing_is_saved(self, mock_generate):
        ContactField.objects.create(company=self.company, key='cad', label='CAD')

        def fake_reply(agent, history, text, **kwargs):
            tools = {t.name: t for t in kwargs['tools']}
            tools['salvar_dado_contato'].entrypoint(campo='cad', valor='Civil 3D')
            tools['agendar_retomada'].entrypoint(data_hora='2030-01-01T10:00', mensagem='Oi')
            tools['transferir_para_atendente'].entrypoint(motivo='teste')
            self.assertEqual(kwargs['variables']['nome'], 'Ana')
            return 'Anotado!\n\nTe chamo segunda.'

        mock_generate.side_effect = fake_reply
        Agent.objects.filter(pk=self.agent.pk).update(split_messages=True)
        contacts_before = Contact.objects.count()
        response = self.post(name='Ana', native_tools=['handoff', 'followup', 'save_field'], prompt='Prompt não salvo')
        self.assertEqual(response.status_code, 200, response.json())
        body = response.json()
        self.assertEqual(body['parts'], ['Anotado!', 'Te chamo segunda.'])
        self.assertEqual([c['name'] for c in body['tool_calls']], ['salvar_dado_contato', 'agendar_retomada', 'transferir_para_atendente'])
        self.assertEqual(body['fields'], {'cad': 'Civil 3D'})
        self.assertEqual(mock_generate.call_args.args[0].prompt, 'Prompt não salvo')
        self.assertEqual((Contact.objects.count(), FollowUp.objects.count(), ToolCall.objects.count()), (contacts_before, 0, 0))
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.prompt, 'Você vende.')  # prompt do teste não foi salvo

    def test_last_message_must_be_from_client(self):
        response = self.post(messages=[{'role': 'assistant', 'content': 'oi'}])
        self.assertEqual(response.status_code, 400)

    @patch('apps.ai.generate_reply', return_value='Oi! Tudo bem?')
    def test_empty_conversation_ai_opens(self, mock_generate):
        response = self.post(messages=[])
        self.assertEqual(response.status_code, 200, response.json())
        self.assertEqual(mock_generate.call_args.args[1:], ([], ai_tools.OPENER))

    def test_foreign_agent_is_404(self):
        url = f'/api/companies/{self.company.pk}/agents/{self.other_agent.pk}/playground'
        response = self.client.post(url, data=json.dumps({'messages': [{'role': 'user', 'content': 'oi'}]}), content_type='application/json', headers=self.auth)
        self.assertEqual(response.status_code, 404)



class QuickReplyTests(BaseChatsTestCase):
    def url(self, reply_id=None, company_id=None):
        base = f'/api/companies/{company_id or self.company.pk}/quick-replies'
        return f'{base}/{reply_id}' if reply_id else base

    def send(self, method, url, data):
        return getattr(self.client, method)(url, data=json.dumps(data), content_type='application/json', headers=self.auth)

    def test_crud_normalizes_and_rejects_duplicate(self):
        r = self.send('post', self.url(), {'shortcut': '/BoasVindas', 'text': ' Olá {{nome}}! '})
        self.assertEqual(r.status_code, 201, r.json())
        self.assertEqual((r.json()['shortcut'], r.json()['text']), ('boasvindas', 'Olá {{nome}}!'))
        self.assertEqual(self.send('post', self.url(), {'shortcut': 'boasvindas', 'text': 'x'}).status_code, 409)
        self.assertEqual(self.send('post', self.url(), {'shortcut': 'com espaço', 'text': 'x'}).status_code, 400)

        reply_id = r.json()['id']
        r = self.send('put', self.url(reply_id), {'shortcut': 'boasvindas', 'text': 'Oi'})  # mesmo atalho, sem conflito consigo
        self.assertEqual(r.json()['text'], 'Oi')
        self.assertEqual([q['shortcut'] for q in self.client.get(self.url(), headers=self.auth).json()], ['boasvindas'])
        self.assertEqual(self.client.delete(self.url(reply_id), headers=self.auth).status_code, 204)

    def test_scoped_by_company(self):
        from apps.chats.models import QuickReply

        foreign = QuickReply.objects.create(company=self.other_company, shortcut='x', text='x')
        self.assertEqual(self.client.delete(self.url(foreign.pk), headers=self.auth).status_code, 404)
        self.assertEqual(self.client.get(self.url(company_id=self.other_company.pk), headers=self.auth).status_code, 404)


@override_settings(BILLING_ENABLED=True)
class PlanGateTests(BaseChatsTestCase):
    def expire(self):
        Company.objects.filter(pk=self.company.pk).update(plan='pro', plan_until=timezone.now() - timedelta(minutes=1))

    @patch('apps.ai.generate_reply')
    def test_expired_plan_keeps_message_but_ai_stays_quiet(self, mock_generate):
        self.expire()
        incoming = Message.objects.create(contact=self.contact, direction='in', content='Oi?', wa_message_id='wamid.vencido')

        tasks.process_incoming_message(incoming.pk)

        mock_generate.assert_not_called()

    def test_starter_blocks_second_number_and_campaigns(self):
        Company.objects.filter(pk=self.company.pk).update(plan='starter')
        payload = {
            'agent_id': self.agent.pk, 'phone_number_id': '222', 'waba_id': 'w2', 'access_token': 't',
            'app_secret': 's', 'verify_token': 'v2',
        }
        response = self.client.post(
            f'/api/companies/{self.company.pk}/whatsapp-accounts', data=payload, content_type='application/json', headers=self.auth
        )
        self.assertEqual(response.status_code, 402)
        self.company.refresh_from_db()
        with self.assertRaises(PaymentRequired):
            services.create_campaign(self.company, self.account.pk, 'c', 'tpl', 'pt_BR', [], contact_ids=[self.contact.pk])

    def test_billing_off_lets_everything_through(self):
        self.expire()
        self.company.refresh_from_db()
        with override_settings(BILLING_ENABLED=False):
            self.assertTrue(plans.is_active(self.company))
            plans.require_feature(self.company, 'campaigns')


@override_settings(**LOCAL_STORAGE_SETTINGS)
@patch('apps.chats.services.events.publish')
class SendFileToolTests(BaseChatsTestCase):
    def setUp(self):
        super().setUp()
        from apps.agents.models import AgentFile

        Agent.objects.filter(pk=self.agent.pk).update(native_tools=['send_file'])
        AgentFile.objects.create(
            agent=self.agent, name='Catálogo', description='quando pedir os planos',
            file=SimpleUploadedFile('catalogo.pdf', b'%PDF-1.4', content_type='application/pdf'), content_type='application/pdf',
        )

    def tool(self):
        from apps.chats import ai_tools

        self.contact.refresh_from_db()
        tools, _ = ai_tools.build_tools(self.contact)
        return next(t for t in tools if getattr(t, 'name', '') == 'enviar_arquivo')

    @patch('apps.chats.whatsapp.send_media', return_value='wamid.arquivo')
    @patch('apps.chats.whatsapp.upload_media', return_value='media-1')
    def test_sends_file_and_records_message(self, mock_upload, mock_send, mock_pub):
        tool = self.tool()
        self.assertIn('Catálogo (quando pedir os planos)', tool.description)
        self.assertIn('enviado', tool.entrypoint(arquivo='Catálogo', legenda='Segue'))
        self.assertEqual(mock_upload.call_args.args[1:], (b'%PDF-1.4', 'application/pdf', 'catalogo.pdf'))
        mock_send.assert_called_once_with(self.account, self.contact.wa_id, 'document', 'media-1', 'Segue', 'catalogo.pdf')
        msg = Message.objects.get(wa_message_id='wamid.arquivo')
        self.assertEqual((msg.message_type, msg.from_human, msg.content), ('document', False, 'Segue'))
        self.assertTrue(msg.media_file.name.endswith('catalogo.pdf'))

    @patch('apps.chats.whatsapp.upload_media', side_effect=httpx.ConnectError('fora'))
    def test_unknown_name_or_failure_reported_to_ai(self, mock_upload, mock_pub):
        tool = self.tool()
        self.assertIn('inexistente', tool.entrypoint(arquivo='Outro'))
        self.assertIn('Falha', tool.entrypoint(arquivo='Catálogo'))
        self.assertFalse(ToolCall.objects.filter(ok=True).exists())

    def test_upload_endpoint_validates(self, mock_pub):
        url = f'/api/companies/{self.company.pk}/agents/{self.agent.pk}/files'
        ok = self.client.post(url, {'file': SimpleUploadedFile('foto.png', b'png', content_type='image/png'), 'name': 'Foto'}, headers=self.auth)
        self.assertEqual(ok.status_code, 201)
        dup = self.client.post(url, {'file': SimpleUploadedFile('f.png', b'png', content_type='image/png'), 'name': 'Foto'}, headers=self.auth)
        self.assertEqual(dup.status_code, 409)
        bad = self.client.post(url, {'file': SimpleUploadedFile('f.gif', b'gif', content_type='image/gif'), 'name': 'Gif'}, headers=self.auth)
        self.assertEqual(bad.status_code, 400)
        self.assertEqual([f['name'] for f in self.client.get(url, headers=self.auth).json()], ['Catálogo', 'Foto'])


class AiToggleTests(BaseChatsTestCase):
    def disable(self):
        WhatsAppAccount.objects.filter(pk=self.account.pk).update(ai_enabled=False)

    @patch('apps.ai.generate_reply')
    def test_ai_off_keeps_message_and_does_not_reply(self, mock_generate):
        self.disable()
        incoming = Message.objects.create(contact=self.contact, direction='in', content='Oi?', wa_message_id='wamid.ia_off')

        tasks.process_incoming_message(incoming.pk)

        mock_generate.assert_not_called()
        self.assertTrue(Message.objects.filter(pk=incoming.pk).exists())

    @patch('apps.chats.whatsapp.send_text')
    def test_ai_off_cancels_followups(self, mock_send):
        self.disable()
        due = FollowUp.objects.create(contact=self.contact, send_at=timezone.now() - timedelta(minutes=1), text='Retomando')

        tasks.dispatch_followups()

        mock_send.assert_not_called()
        due.refresh_from_db()
        self.assertEqual(due.status, FollowUp.Status.CANCELED)

    def test_patch_toggles_ai(self):
        response = self.client.patch(
            f'/api/companies/{self.company.pk}/whatsapp-accounts/{self.account.pk}',
            data={'ai_enabled': False}, content_type='application/json', headers=self.auth,
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['ai_enabled'])
        self.account.refresh_from_db()
        self.assertFalse(self.account.ai_enabled)


class CampaignRecipientsTests(BaseChatsTestCase):
    def test_lists_saved_recipients(self):
        other = Contact.objects.create(account=self.account, wa_id='5511900000002', name='Bia')
        Contact.objects.create(account=self.account, wa_id='5511900000003', name='Fora')
        campaign = Campaign.objects.create(account=self.account, name='c', template_name='t', template_language='pt_BR', recipient_ids=[self.contact.pk, other.pk])

        response = self.client.get(f'/api/companies/{self.company.pk}/campaigns/{campaign.pk}/recipients', headers=self.auth)

        self.assertEqual(response.status_code, 200)
        self.assertEqual({c['id'] for c in response.json()}, {self.contact.pk, other.pk})

    def test_old_campaign_falls_back_to_sent_messages(self):
        campaign = Campaign.objects.create(account=self.account, name='c', template_name='t', template_language='pt_BR')
        Message.objects.create(contact=self.contact, direction='out', content='oi', wa_message_id='wamid.old', campaign=campaign)

        ids = [c.pk for c in services.campaign_recipients(self.company, campaign.pk)]

        self.assertEqual(ids, [self.contact.pk])

    def test_other_company_404(self):
        campaign = Campaign.objects.create(account=self.account, name='c', template_name='t', template_language='pt_BR')
        with self.assertRaises(NotFound):
            services.campaign_recipients(self.other_company, campaign.pk)
