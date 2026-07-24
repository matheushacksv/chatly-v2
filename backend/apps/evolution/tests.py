import base64
import tempfile
from datetime import timedelta
from unittest.mock import MagicMock, patch

import httpx
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import User
from apps.agents.models import Agent, Credential
from apps.chats.models import Contact, Sticker, WhatsAppAccount
from apps.companies.models import Company, Membership
from apps.evolution import evolution, services, tasks
from apps.evolution.models import EvolutionAccount, EvolutionContact, EvolutionMessage
from apps.testing import auth_header
from core.exceptions import Conflict, EvolutionError, Forbidden, NotFound, ValidationError

# mídia é escrita em disco (FileField); isola num temp pra não sujar o MEDIA_ROOT real
_MEDIA_TMP = tempfile.mkdtemp()


def _resp(json_data=None, status=200):
    resp = MagicMock()
    resp.json.return_value = json_data or {}
    if status >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError('err', request=MagicMock(), response=MagicMock(status_code=status))
    return resp


# * ----- Boundary (evolution.py) ------


@patch('apps.evolution.evolution.httpx.request')
class EvolutionBoundaryTests(TestCase):
    def test_create_instance_uses_global_key_and_sends_token(self, mock_request):
        # evolution-go exige um token client-supplied no body; a resposta ecoa em data.token
        mock_request.return_value = _resp({'data': {'token': 'inst-tok'}, 'message': 'success'})
        token = evolution.create_instance('c1u1')
        self.assertEqual(token, 'inst-tok')
        kwargs = mock_request.call_args.kwargs
        self.assertEqual(kwargs['headers'], {'apikey': evolution.EVOLUTION_GLOBAL_KEY})
        self.assertTrue(kwargs['url'].endswith('/instance/create'))
        self.assertEqual(kwargs['json']['name'], 'c1u1')
        self.assertTrue(kwargs['json'].get('token'))

    def test_connect_instance_returns_message(self, mock_request):
        # message é top-level na resposta do evolution-go, não dentro de data
        mock_request.return_value = _resp({'data': {'eventString': 'MESSAGE', 'jid': ''}, 'message': 'success'})
        result = evolution.connect_instance('inst-tok', 'https://hook', ['MESSAGE'])
        self.assertEqual(result, 'success')
        kwargs = mock_request.call_args.kwargs
        self.assertEqual(kwargs['headers'], {'apikey': 'inst-tok'})
        self.assertEqual(kwargs['json'], {'webhookUrl': 'https://hook', 'subscribe': ['MESSAGE']})

    def test_get_qr_code(self, mock_request):
        # chave qrcode minúscula; valor já é data URL completo
        mock_request.return_value = _resp({'data': {'qrcode': 'data:image/png;base64,AAAA'}})
        self.assertEqual(evolution.get_qr_code('inst-tok'), 'data:image/png;base64,AAAA')

    def test_delete_instance_resolves_uuid_by_name(self, mock_request):
        # /instance/all lista {id(UUID), name}; delete é por UUID
        mock_request.side_effect = [
            _resp({'data': [{'id': 'uuid-1', 'name': 'other'}, {'id': 'uuid-2', 'name': 'c1u1'}]}),
            _resp({'message': 'success'}),
        ]
        evolution.delete_instance('c1u1')
        self.assertEqual(mock_request.call_count, 2)
        delete_call = mock_request.call_args_list[1].kwargs
        self.assertEqual(delete_call['method'], 'DELETE')
        self.assertTrue(delete_call['url'].endswith('/instance/delete/uuid-2'))

    def test_delete_instance_missing_name_is_noop(self, mock_request):
        mock_request.return_value = _resp({'data': []})
        evolution.delete_instance('nao-existe')
        self.assertEqual(mock_request.call_count, 1)  # só o /instance/all, sem DELETE

    def test_get_instance_phone_extracts_from_jid(self, mock_request):
        mock_request.return_value = _resp({'data': [{'name': 'c1u1', 'jid': '5548999999999:12@s.whatsapp.net'}]})
        self.assertEqual(evolution.get_instance_phone('c1u1'), '5548999999999')

    def test_get_instance_phone_missing_returns_empty(self, mock_request):
        mock_request.return_value = _resp({'data': []})
        self.assertEqual(evolution.get_instance_phone('nope'), '')

    def test_get_pair_code(self, mock_request):
        mock_request.return_value = _resp({'data': {'PairingCode': 'WZ12-34AB'}})
        self.assertEqual(evolution.get_pair_code('inst-tok', '5511999999999'), 'WZ12-34AB')

    def test_send_text_posts_number_and_text(self, mock_request):
        mock_request.return_value = _resp({})
        evolution.send_text('inst-tok', '5511999999999', 'oi')
        kwargs = mock_request.call_args.kwargs
        self.assertTrue(kwargs['url'].endswith('/send/text'))
        self.assertEqual(kwargs['json'], {'number': '5511999999999', 'text': 'oi'})

    def test_send_sticker_posts_number_and_sticker(self, mock_request):
        mock_request.return_value = _resp({})
        evolution.send_sticker('inst-tok', '5511999999999', 'https://cdn.example/s.webp')
        kwargs = mock_request.call_args.kwargs
        self.assertTrue(kwargs['url'].endswith('/send/sticker'))
        self.assertEqual(kwargs['json'], {'number': '5511999999999', 'sticker': 'https://cdn.example/s.webp'})

    def test_download_media_parses_data_url_into_bytes_and_mime(self, mock_request):
        # resposta do evolution-go: data.base64 é um data URL completo
        b64 = base64.b64encode(b'hi').decode()
        mock_request.return_value = _resp({'data': {'base64': f'data:image/jpeg;base64,{b64}'}})
        raw, mime = evolution.download_media('inst-tok', {'imageMessage': {}})
        self.assertEqual(raw, b'hi')
        self.assertEqual(mime, 'image/jpeg')
        kwargs = mock_request.call_args.kwargs
        self.assertTrue(kwargs['url'].endswith('/message/downloadmedia'))
        self.assertEqual(kwargs['json'], {'message': {'imageMessage': {}}})

    @patch('apps.evolution.evolution.httpx.post')
    def test_send_media_file_posts_multipart(self, mock_post, mock_request):
        # upload multipart (não data URL): mais leve pra vídeo grande
        mock_post.return_value = _resp({})
        evolution.send_media_file('inst-tok', '5511999999999', 'document', b'PDFDATA', 'rel.pdf', 'application/pdf', 'legenda')
        kwargs = mock_post.call_args.kwargs
        self.assertEqual(kwargs['headers'], {'apikey': 'inst-tok'})
        self.assertEqual(kwargs['data'], {'number': '5511999999999', 'type': 'document', 'caption': 'legenda', 'filename': 'rel.pdf'})
        self.assertEqual(kwargs['files']['file'], ('rel.pdf', b'PDFDATA', 'application/pdf'))

    def test_check_number_returns_canonical_wa_id(self, mock_request):
        # evogo normaliza o formato: entra com nono dígito, sai o JID real (sem ele, no caso BR)
        mock_request.return_value = _resp(
            {'data': {'Users': [{'Query': '+5548996436646@s.whatsapp.net', 'IsInWhatsapp': True, 'JID': '554896436646@s.whatsapp.net'}]}}
        )
        self.assertEqual(evolution.check_number('inst-tok', '5548996436646'), '554896436646')
        kwargs = mock_request.call_args.kwargs
        self.assertTrue(kwargs['url'].endswith('/user/check'))
        self.assertEqual(kwargs['json'], {'number': ['5548996436646']})

    def test_check_number_absent_returns_empty(self, mock_request):
        # número que não existe some de Users (o evogo devolve null, não uma entrada com false)
        mock_request.return_value = _resp({'data': {'Users': None}})
        self.assertEqual(evolution.check_number('inst-tok', '5511000000000'), '')

    def test_check_number_not_on_whatsapp_returns_empty(self, mock_request):
        mock_request.return_value = _resp({'data': {'Users': [{'IsInWhatsapp': False, 'JID': '5511000000000@s.whatsapp.net'}]}})
        self.assertEqual(evolution.check_number('inst-tok', '5511000000000'), '')

    def test_get_device_contacts_maps_wa_id_to_name(self, mock_request):
        mock_request.return_value = _resp({'data': [
            {'Jid': '554197487225@s.whatsapp.net', 'FullName': 'Clube Da Barba', 'FirstName': 'Clube'},
            {'Jid': '555199899261@s.whatsapp.net', 'FullName': '', 'FirstName': 'Eduardo'},
            {'Jid': '554899592104@s.whatsapp.net', 'FullName': '', 'FirstName': '', 'BusinessName': 'Padaria'},
            {'Jid': '554811111111@s.whatsapp.net', 'FullName': '', 'FirstName': '', 'PushName': 'Ze'},
            {'Jid': '554822222222@s.whatsapp.net', 'FullName': ''},          # sem nome nenhum: fica de fora
            {'Jid': '135544906506468@lid', 'FullName': 'Anônimo'},           # @lid não é número
            {'Jid': '12345-678@g.us', 'FullName': 'Grupo'},                  # grupo não entra
        ]})
        self.assertEqual(
            evolution.get_device_contacts('inst-tok'),
            {'554197487225': 'Clube Da Barba', '555199899261': 'Eduardo', '554899592104': 'Padaria', '554811111111': 'Ze'},
        )
        self.assertTrue(mock_request.call_args.kwargs['url'].endswith('/user/contacts'))

    def test_get_device_contacts_empty_payload(self, mock_request):
        mock_request.return_value = _resp({'data': None})
        self.assertEqual(evolution.get_device_contacts('inst-tok'), {})

    def test_get_user_avatar_sends_full_jid(self, mock_request):
        # regressão: com número puro o evogo monta '+<num>@s.whatsapp.net' e a chamada
        # só falha depois de 75s ("info query timed out")
        mock_request.return_value = _resp({'data': {'url': 'https://pps.whatsapp.net/v/foto.jpg', 'type': 'preview'}})
        self.assertEqual(evolution.get_user_avatar('inst-tok', '554896436646'), 'https://pps.whatsapp.net/v/foto.jpg')
        kwargs = mock_request.call_args.kwargs
        self.assertTrue(kwargs['url'].endswith('/user/avatar'))
        self.assertEqual(kwargs['json'], {'number': '554896436646@s.whatsapp.net', 'preview': True})

    def test_get_user_avatar_full_size(self, mock_request):
        mock_request.return_value = _resp({'data': {'url': 'https://pps.whatsapp.net/v/full.jpg', 'type': 'image'}})
        evolution.get_user_avatar('inst-tok', '554896436646', preview=False)
        self.assertIs(mock_request.call_args.kwargs['json']['preview'], False)

    def test_get_user_avatar_without_picture_returns_empty(self, mock_request):
        # "that user or group does not have a profile picture" chega como 500, não é erro de infra
        mock_request.return_value = _resp(status=500)
        self.assertEqual(evolution.get_user_avatar('inst-tok', '5511000000000'), '')

    def test_get_user_avatar_404_returns_empty(self, mock_request):
        mock_request.return_value = _resp(status=404)
        self.assertEqual(evolution.get_user_avatar('inst-tok', '5511000000000'), '')

    def test_get_user_avatar_payload_without_url_returns_empty(self, mock_request):
        mock_request.return_value = _resp({'data': {}})
        self.assertEqual(evolution.get_user_avatar('inst-tok', '554896436646'), '')

    def test_404_raises_not_found(self, mock_request):
        mock_request.return_value = _resp(status=404)
        with self.assertRaises(NotFound):
            evolution.get_qr_code('inst-tok')

    def test_500_raises_evolution_error(self, mock_request):
        mock_request.return_value = _resp(status=500)
        with self.assertRaises(EvolutionError) as ctx:
            evolution.get_qr_code('inst-tok')
        self.assertIn('500', ctx.exception.message)

    def test_network_error_raises_evolution_error(self, mock_request):
        mock_request.side_effect = httpx.ConnectError('down')
        with self.assertRaises(EvolutionError):
            evolution.get_qr_code('inst-tok')


# * ----- Services ------


class BaseEvolutionTestCase(TestCase):
    """Owner + membro + outsider numa company; helpers de auth."""

    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(email='owner@a.com', password='strongpass123')
        cls.member = User.objects.create_user(email='member@a.com', password='strongpass123')
        cls.outsider = User.objects.create_user(email='out@a.com', password='strongpass123')
        cls.company = Company.objects.create(name='Minha Empresa', owner=cls.owner)
        Membership.objects.create(company=cls.company, user=cls.member)

    def setUp(self):
        self.auth = auth_header(self.owner)
        self.member_auth = auth_header(self.member)
        self.outsider_auth = auth_header(self.outsider)
        self.base = f'/api/evolution/{self.company.id}'


class EvolutionConnectServiceTests(BaseEvolutionTestCase):
    @patch('apps.evolution.evolution.get_qr_code', return_value='QRDATA')
    @patch('apps.evolution.evolution.connect_instance', return_value='success')
    @patch('apps.evolution.evolution.create_instance', return_value='inst-tok')
    def test_connect_creates_account_and_returns_qr(self, mock_create, mock_connect, mock_qr):
        result = services.create_and_connect_evolution_instance(self.company, self.owner, 'c1u1')
        self.assertEqual(result, 'QRDATA')
        account = EvolutionAccount.objects.get(company=self.company, user=self.owner)
        self.assertEqual(account.status, EvolutionAccount.Status.CONNECTING)
        self.assertEqual(account.instance_api_key, 'inst-tok')

    @patch('apps.evolution.evolution.get_pair_code', return_value='WZ12-34AB')
    @patch('apps.evolution.evolution.connect_instance', return_value='success')
    @patch('apps.evolution.evolution.create_instance', return_value='inst-tok')
    def test_connect_with_phone_returns_pair_code(self, mock_create, mock_connect, mock_pair):
        result = services.create_and_connect_evolution_instance(self.company, self.owner, 'c1u1', '5511999999999')
        self.assertEqual(result, 'WZ12-34AB')
        mock_pair.assert_called_once_with('inst-tok', '5511999999999')

    @patch('apps.evolution.evolution.delete_instance')
    @patch('apps.evolution.evolution.connect_instance', return_value='fail')
    @patch('apps.evolution.evolution.create_instance', return_value='inst-tok')
    def test_connect_failure_rolls_back_account_and_instance(self, mock_create, mock_connect, mock_delete):
        with self.assertRaises(EvolutionError):
            services.create_and_connect_evolution_instance(self.company, self.owner, 'c1u1')
        self.assertFalse(EvolutionAccount.objects.filter(company=self.company, user=self.owner).exists())
        mock_delete.assert_called_once_with('c1u1')  # não deixa órfão no evogo

    @patch('apps.evolution.evolution.get_qr_code', side_effect=EvolutionError('no QR yet'))
    @patch('apps.evolution.evolution.connect_instance', return_value='success')
    @patch('apps.evolution.evolution.create_instance', return_value='inst-tok')
    def test_connect_qr_not_ready_returns_empty_keeps_account(self, mock_create, mock_connect, mock_qr):
        # QR não pronto (~1s) não é fatal: retorna vazio, conta permanece, front busca depois
        result = services.create_and_connect_evolution_instance(self.company, self.owner, 'c1u1')
        self.assertEqual(result, '')
        self.assertTrue(EvolutionAccount.objects.filter(company=self.company, user=self.owner).exists())

    def test_connect_duplicate_raises_conflict(self):
        EvolutionAccount.objects.create(company=self.company, user=self.owner, instance_name='c1u1')
        with self.assertRaises(Conflict):
            services.create_and_connect_evolution_instance(self.company, self.owner, 'c1u1')

    @patch('apps.evolution.evolution.delete_instance')
    def test_disconnect_deletes_instance_and_account(self, mock_delete):
        account = EvolutionAccount.objects.create(company=self.company, user=self.owner, instance_name='c1u1', instance_api_key='inst-tok')
        services.disconnect(self.company, self.owner, account.id)
        mock_delete.assert_called_once_with('c1u1')
        self.assertFalse(EvolutionAccount.objects.filter(pk=account.id).exists())

    @patch('apps.evolution.evolution.get_connection_status', side_effect=EvolutionError('down'))
    def test_get_my_connection_returns_own(self, mock_status):
        account = EvolutionAccount.objects.create(company=self.company, user=self.owner, instance_name='c1u1')
        self.assertEqual(services.get_my_connection(self.company, self.owner), account)
        self.assertIsNone(services.get_my_connection(self.company, self.member))


class EvolutionSyncStatusTests(BaseEvolutionTestCase):
    def _account(self, status=EvolutionAccount.Status.CONNECTING, phone=''):
        return EvolutionAccount.objects.create(
            company=self.company, user=self.owner, instance_name='c1u1', instance_api_key='tok', status=status, phone_number=phone
        )

    @patch('apps.evolution.evolution.get_instance_phone', return_value='5548999999999')
    @patch('apps.evolution.evolution.get_connection_status', return_value={'LoggedIn': True, 'Connected': True})
    def test_logged_in_marks_connected_and_captures_phone(self, mock_status, mock_phone):
        account = self._account()
        services._sync_status(account)
        account.refresh_from_db()
        self.assertEqual(account.status, EvolutionAccount.Status.CONNECTED)
        self.assertEqual(account.phone_number, '5548999999999')

    @patch('apps.evolution.evolution.reconnect_instance')
    @patch('apps.evolution.evolution.get_connection_status', return_value={'LoggedIn': False, 'Connected': True})
    def test_connecting_paired_does_not_reconnect(self, mock_status, mock_reconnect):
        # durante o pareamento (socket up, ainda não logado) NÃO reconecta - isso quebraria o scan
        account = self._account(status=EvolutionAccount.Status.CONNECTING)
        services._sync_status(account)
        mock_reconnect.assert_not_called()
        account.refresh_from_db()
        self.assertEqual(account.status, EvolutionAccount.Status.CONNECTING)

    @patch('apps.evolution.evolution.reconnect_instance')
    @patch('apps.evolution.evolution.get_connection_status', return_value={'LoggedIn': False, 'Connected': True})
    def test_dropped_session_nudges_reconnect(self, mock_status, mock_reconnect):
        # já estava conectado e caiu pra socket-up-sem-login: aí sim reestabelece
        account = self._account(status=EvolutionAccount.Status.CONNECTED)
        services._sync_status(account)
        mock_reconnect.assert_called_once_with('tok')
        account.refresh_from_db()
        self.assertEqual(account.status, EvolutionAccount.Status.CONNECTED)

    @patch('apps.evolution.evolution.get_connection_status', return_value={'LoggedIn': False, 'Connected': False})
    def test_offline_while_connecting_does_not_downgrade(self, mock_status):
        # regressão "não pediu QR": conta recém-criada (socket ainda não subiu) NÃO vira disconnected
        account = self._account(status=EvolutionAccount.Status.CONNECTING)
        services._sync_status(account)
        account.refresh_from_db()
        self.assertEqual(account.status, EvolutionAccount.Status.CONNECTING)

    @patch('apps.evolution.evolution.get_connection_status', return_value={'LoggedIn': False, 'Connected': False})
    def test_offline_after_connected_downgrades(self, mock_status):
        account = self._account(status=EvolutionAccount.Status.CONNECTED)
        services._sync_status(account)
        account.refresh_from_db()
        self.assertEqual(account.status, EvolutionAccount.Status.DISCONNECTED)

    @patch('apps.evolution.evolution.get_connection_status', side_effect=EvolutionError('down'))
    def test_evogo_down_keeps_last_status(self, mock_status):
        account = self._account(status=EvolutionAccount.Status.CONNECTED)
        services._sync_status(account)
        account.refresh_from_db()
        self.assertEqual(account.status, EvolutionAccount.Status.CONNECTED)

    @patch('apps.evolution.evolution.get_connection_status', return_value={'LoggedIn': True, 'Connected': True})
    @patch('apps.evolution.evolution.get_instance_phone', return_value='5548999999999')
    def test_get_my_connection_syncs_status(self, mock_phone, mock_status):
        account = self._account(status=EvolutionAccount.Status.CONNECTING)
        result = services.get_my_connection(self.company, self.owner)
        self.assertIsNotNone(result)
        self.assertEqual(result.pk, account.pk)
        self.assertEqual(result.status, EvolutionAccount.Status.CONNECTED)


class EvolutionPermissionHelperTests(BaseEvolutionTestCase):
    def setUp(self):
        super().setUp()
        self.account = EvolutionAccount.objects.create(company=self.company, user=self.member, instance_name='c1um')

    def test_owner_can_access_member_account(self):
        self.assertEqual(services._get_own_or_owned_account(self.company, self.owner, self.account.id), self.account)

    def test_own_user_can_access(self):
        self.assertEqual(services._get_own_or_owned_account(self.company, self.member, self.account.id), self.account)

    def test_third_member_forbidden(self):
        other = User.objects.create_user(email='other@a.com', password='strongpass123')
        Membership.objects.create(company=self.company, user=other)
        with self.assertRaises(Forbidden):
            services._get_own_or_owned_account(self.company, other, self.account.id)

    def test_missing_account_not_found(self):
        with self.assertRaises(NotFound):
            services._get_own_or_owned_account(self.company, self.owner, 999999)


@patch('apps.evolution.tasks.sync_avatars.delay')
class EvolutionListContactsTests(BaseEvolutionTestCase):
    """Ordem por atividade + nomes preenchidos pela agenda do celular."""

    def setUp(self):
        super().setUp()
        self.account = EvolutionAccount.objects.create(company=self.company, user=self.owner, instance_name='c1u1', instance_api_key='tok')

    def _contact(self, wa_id, name='', created_at=None):
        contact = EvolutionContact.objects.create(account=self.account, wa_id=wa_id, name=name)
        if created_at:  # created_at é auto_now_add: só dá pra forjar via update
            EvolutionContact.objects.filter(pk=contact.pk).update(created_at=created_at)
        return contact

    def _message(self, contact, created_at):
        msg = EvolutionMessage.objects.create(contact=contact, direction=EvolutionMessage.Direction.IN, content='oi')
        EvolutionMessage.objects.filter(pk=msg.pk).update(created_at=created_at)
        return msg

    @patch('apps.evolution.evolution.get_device_contacts', return_value={})
    def test_queues_avatar_sync_when_something_is_stale(self, mock_agenda, mock_delay):
        self._contact('5511000000001', 'Sem foto')
        services.list_contacts(self.company, self.owner, self.account.id)
        mock_delay.assert_called_once_with(self.account.pk)

    @patch('apps.evolution.evolution.get_device_contacts', return_value={})
    def test_does_not_queue_when_all_avatars_are_fresh(self, mock_agenda, mock_delay):
        contact = self._contact('5511000000001', 'Com foto')
        EvolutionContact.objects.filter(pk=contact.pk).update(avatar_synced_at=timezone.now())
        services.list_contacts(self.company, self.owner, self.account.id)
        mock_delay.assert_not_called()

    @patch('apps.evolution.evolution.get_device_contacts', return_value={})
    def test_orders_by_last_message_then_created_at(self, mock_agenda, mock_delay):
        now = timezone.now()
        old = self._contact('5511000000001', 'Antigo', created_at=now - timedelta(hours=3))
        recent = self._contact('5511000000002', 'Recente', created_at=now - timedelta(hours=3))
        fresh = self._contact('5511000000003', 'Novo', created_at=now - timedelta(minutes=30))  # sem mensagem
        self._message(old, now - timedelta(hours=2))
        self._message(recent, now - timedelta(hours=1))
        result = services.list_contacts(self.company, self.owner, self.account.id)
        self.assertEqual([c.pk for c in result], [fresh.pk, recent.pk, old.pk])

    @patch('apps.evolution.evolution.get_device_contacts')
    def test_fills_only_missing_names_and_persists(self, mock_agenda, mock_delay):
        mock_agenda.return_value = {'5511000000001': 'Da Agenda', '5511000000002': 'Ignorado'}
        sem_nome = self._contact('5511000000001')
        com_nome = self._contact('5511000000002', 'PushName Antigo')
        services.list_contacts(self.company, self.owner, self.account.id)
        sem_nome.refresh_from_db()
        com_nome.refresh_from_db()
        self.assertEqual(sem_nome.name, 'Da Agenda')
        self.assertEqual(com_nome.name, 'PushName Antigo')  # nome existente nunca é sobrescrito

    @patch('apps.evolution.evolution.get_device_contacts')
    def test_all_named_skips_agenda_call(self, mock_agenda, mock_delay):
        self._contact('5511000000001', 'Já tem nome')
        services.list_contacts(self.company, self.owner, self.account.id)
        mock_agenda.assert_not_called()

    @patch('apps.evolution.evolution.get_device_contacts', return_value={})
    def test_contact_absent_from_agenda_stays_empty(self, mock_agenda, mock_delay):
        contact = self._contact('5511000000001')
        services.list_contacts(self.company, self.owner, self.account.id)
        contact.refresh_from_db()
        self.assertEqual(contact.name, '')

    @patch('apps.evolution.evolution.get_device_contacts', side_effect=EvolutionError('down'))
    def test_agenda_down_does_not_break_listing(self, mock_agenda, mock_delay):
        self._contact('5511000000001')
        result = services.list_contacts(self.company, self.owner, self.account.id)
        self.assertEqual(len(result), 1)


class EvolutionDirectoryTests(BaseEvolutionTestCase):
    """Diretório de destinatários: agenda do celular + contatos do canal oficial."""

    def setUp(self):
        super().setUp()
        self.account = EvolutionAccount.objects.create(company=self.company, user=self.owner, instance_name='c1u1', instance_api_key='tok')
        credential = Credential.objects.create(company=self.company, provider='anthropic', api_key='sk-x')
        agent = Agent.objects.create(company=self.company, name='Vendedor', prompt='...', credential=credential)
        self.official_account = WhatsAppAccount.objects.create(
            company=self.company, agent=agent, phone_number_id='111', waba_id='waba-1',
            access_token='tok', app_secret='sec', verify_token='v1',
        )

    def _official(self, wa_id, name):
        return Contact.objects.create(account=self.official_account, wa_id=wa_id, name=name)

    @patch('apps.evolution.evolution.get_device_contacts')
    def test_merges_agenda_and_official_sorted_by_name(self, mock_agenda):
        mock_agenda.return_value = {'5511000000001': 'Bruno'}
        self._official('5511000000002', 'Ana')
        result = services.list_directory(self.company, self.owner, self.account.id)
        self.assertEqual(result, [
            {'wa_id': '5511000000002', 'name': 'Ana', 'source': 'oficial'},
            {'wa_id': '5511000000001', 'name': 'Bruno', 'source': 'agenda'},
        ])

    @patch('apps.evolution.evolution.get_device_contacts')
    def test_agenda_wins_on_duplicate_wa_id(self, mock_agenda):
        mock_agenda.return_value = {'5511000000001': 'Nome Da Agenda'}
        self._official('5511000000001', 'Nome Do CRM')
        result = services.list_directory(self.company, self.owner, self.account.id)
        self.assertEqual(result, [{'wa_id': '5511000000001', 'name': 'Nome Da Agenda', 'source': 'agenda'}])

    @patch('apps.evolution.evolution.get_device_contacts', side_effect=EvolutionError('down'))
    def test_agenda_down_still_returns_official(self, mock_agenda):
        self._official('5511000000002', 'Ana')
        result = services.list_directory(self.company, self.owner, self.account.id)
        self.assertEqual(result, [{'wa_id': '5511000000002', 'name': 'Ana', 'source': 'oficial'}])

    @patch('apps.evolution.evolution.get_device_contacts', return_value={})
    def test_other_company_official_contacts_are_not_leaked(self, mock_agenda):
        other_company = Company.objects.create(name='Alheia', owner=self.outsider)
        credential = Credential.objects.create(company=other_company, provider='anthropic', api_key='sk-y')
        agent = Agent.objects.create(company=other_company, name='Outro', prompt='...', credential=credential)
        other_account = WhatsAppAccount.objects.create(
            company=other_company, agent=agent, phone_number_id='222', waba_id='waba-2',
            access_token='tok', app_secret='sec', verify_token='v2',
        )
        Contact.objects.create(account=other_account, wa_id='5511000000009', name='Alheio')
        self.assertEqual(services.list_directory(self.company, self.owner, self.account.id), [])

    def test_other_users_account_forbidden(self):
        with self.assertRaises(Forbidden):
            services.list_directory(self.company, self.member, self.account.id)


@patch('apps.evolution.tasks.sync_avatars.delay')
class EvolutionStartConversationTests(BaseEvolutionTestCase):
    def setUp(self):
        super().setUp()
        self.account = EvolutionAccount.objects.create(company=self.company, user=self.owner, instance_name='c1u1', instance_api_key='tok')

    @patch('apps.evolution.evolution.check_number', return_value='554896436646')
    def test_queues_avatar_sync_for_new_contact(self, mock_check, mock_delay):
        # contato novo busca a foto na hora, sem esperar a próxima listagem
        services.start_conversation(self.company, self.owner, self.account.id, '5548996436646')
        mock_delay.assert_called_once_with(self.account.pk)

    @patch('apps.evolution.evolution.check_number', return_value='554896436646')
    def test_creates_contact_with_canonical_wa_id(self, mock_check, mock_delay):
        # o front manda o número com nono dígito; quem manda no wa_id é o JID do evogo
        contact = services.start_conversation(self.company, self.owner, self.account.id, '+55 (48) 99643-6646', 'Patrícia')
        mock_check.assert_called_once_with('tok', '5548996436646')  # normalize_number tira a máscara
        self.assertEqual(contact.wa_id, '554896436646')
        self.assertEqual(contact.name, 'Patrícia')
        self.assertEqual(contact.account, self.account)

    @patch('apps.evolution.evolution.check_number', return_value='')
    def test_number_without_whatsapp_is_rejected(self, mock_check, mock_delay):
        with self.assertRaises(ValidationError):
            services.start_conversation(self.company, self.owner, self.account.id, '5511000000000')
        self.assertFalse(EvolutionContact.objects.exists())

    @patch('apps.evolution.evolution.check_number')
    def test_invalid_number_never_reaches_evogo(self, mock_check, mock_delay):
        with self.assertRaises(ValidationError):
            services.start_conversation(self.company, self.owner, self.account.id, '123')
        mock_check.assert_not_called()

    @patch('apps.evolution.evolution.check_number', return_value='5511999999999')
    def test_existing_contact_is_reused(self, mock_check, mock_delay):
        existing = EvolutionContact.objects.create(account=self.account, wa_id='5511999999999', name='Cliente')
        contact = services.start_conversation(self.company, self.owner, self.account.id, '5511999999999', 'Outro Nome')
        self.assertEqual(contact.pk, existing.pk)
        self.assertEqual(contact.name, 'Cliente')  # não renomeia conversa existente
        self.assertEqual(EvolutionContact.objects.count(), 1)

    @patch('apps.evolution.evolution.check_number', return_value='5511999999999')
    def test_other_users_account_forbidden(self, mock_check, mock_delay):
        with self.assertRaises(Forbidden):
            services.start_conversation(self.company, self.member, self.account.id, '5511999999999')


@patch('apps.chats.events.publish')
class EvolutionAvatarSyncTaskTests(BaseEvolutionTestCase):
    """Sync de fotos de perfil: roda no worker, publica SSE quando termina."""

    def setUp(self):
        super().setUp()
        self.account = EvolutionAccount.objects.create(company=self.company, user=self.owner, instance_name='c1u1', instance_api_key='tok')

    def _contact(self, wa_id, synced_at=None):
        contact = EvolutionContact.objects.create(account=self.account, wa_id=wa_id)
        if synced_at:
            EvolutionContact.objects.filter(pk=contact.pk).update(avatar_synced_at=synced_at)
        return contact

    @patch('apps.evolution.evolution.get_user_avatar', return_value='https://pps.whatsapp.net/v/foto.jpg')
    def test_stores_url_and_stamps_sync(self, mock_avatar, mock_publish):
        contact = self._contact('5511000000001')
        tasks.sync_avatars(self.account.pk)
        contact.refresh_from_db()
        self.assertEqual(contact.avatar_url, 'https://pps.whatsapp.net/v/foto.jpg')
        self.assertIsNotNone(contact.avatar_synced_at)
        mock_avatar.assert_called_once_with('tok', '5511000000001')
        mock_publish.assert_called_once_with(self.company.id, {'type': 'evolution_contacts', 'account_id': self.account.pk})

    @patch('apps.evolution.evolution.get_user_avatar')
    def test_reserves_batch_before_fetching(self, mock_avatar, mock_publish):
        # a reserva é o que impede task duplicada: enquanto a sync roda, uma nova
        # listagem não pode achar ninguém vencido (senão enfileira outra igual e
        # dobra as chamadas ao servidor do WhatsApp)
        self._contact('5511000000001')
        self._contact('5511000000002')
        pending_during_fetch = []

        def fake_avatar(api_key, wa_id):
            pending_during_fetch.append(tasks.stale_avatars(self.account).count())
            return 'https://pps.whatsapp.net/v/foto.jpg'

        mock_avatar.side_effect = fake_avatar
        tasks.sync_avatars(self.account.pk)
        self.assertEqual(pending_during_fetch, [0, 0])

    @patch('apps.evolution.evolution.get_user_avatar', return_value='')
    def test_contact_without_picture_is_still_stamped(self, mock_avatar, mock_publish):
        # sem carimbo, toda listagem re-perguntaria pelo mesmo contato sem foto
        contact = self._contact('5511000000001')
        tasks.sync_avatars(self.account.pk)
        contact.refresh_from_db()
        self.assertEqual(contact.avatar_url, '')
        self.assertIsNotNone(contact.avatar_synced_at)

    @patch('apps.evolution.evolution.get_user_avatar', return_value='https://pps.whatsapp.net/v/foto.jpg')
    def test_skips_recently_synced_contact(self, mock_avatar, mock_publish):
        self._contact('5511000000001', synced_at=timezone.now() - timedelta(days=1))
        tasks.sync_avatars(self.account.pk)
        mock_avatar.assert_not_called()
        mock_publish.assert_not_called()

    @patch('apps.evolution.evolution.get_user_avatar', return_value='https://pps.whatsapp.net/v/foto.jpg')
    def test_resyncs_contact_past_ttl(self, mock_avatar, mock_publish):
        self._contact('5511000000001', synced_at=timezone.now() - tasks.AVATAR_TTL - timedelta(minutes=1))
        tasks.sync_avatars(self.account.pk)
        mock_avatar.assert_called_once()

    @patch('apps.evolution.evolution.get_user_avatar')
    def test_no_contacts_is_noop(self, mock_avatar, mock_publish):
        tasks.sync_avatars(self.account.pk)
        mock_avatar.assert_not_called()
        mock_publish.assert_not_called()

    @patch('apps.evolution.evolution.get_user_avatar')
    def test_missing_account_is_noop(self, mock_avatar, mock_publish):
        tasks.sync_avatars(999999)
        mock_avatar.assert_not_called()

    @patch('apps.evolution.evolution.get_user_avatar', return_value='https://pps.whatsapp.net/v/foto.jpg')
    def test_other_accounts_contacts_are_untouched(self, mock_avatar, mock_publish):
        other = EvolutionAccount.objects.create(company=self.company, user=self.member, instance_name='c1um', instance_api_key='tok2')
        alheio = EvolutionContact.objects.create(account=other, wa_id='5511000000009')
        self._contact('5511000000001')
        tasks.sync_avatars(self.account.pk)
        alheio.refresh_from_db()
        self.assertEqual(alheio.avatar_url, '')
        self.assertIsNone(alheio.avatar_synced_at)


@override_settings(MEDIA_ROOT=_MEDIA_TMP)
class EvolutionSendMediaServiceTests(BaseEvolutionTestCase):
    def setUp(self):
        super().setUp()
        self.account = EvolutionAccount.objects.create(company=self.company, user=self.owner, instance_name='c1u1', instance_api_key='tok')
        self.contact = EvolutionContact.objects.create(account=self.account, wa_id='5511999999999')

    def _send(self, file, caption=''):
        return services.send_media_message(self.company, self.owner, self.account.id, self.contact.id, file, caption)

    @patch('apps.evolution.evolution.send_media_file')
    def test_image_persists_and_calls_boundary(self, mock_send):
        msg = self._send(SimpleUploadedFile('foto.jpg', b'\xff\xd8\xff', content_type='image/jpeg'), 'legenda')
        self.assertEqual(msg.message_type, 'image')
        self.assertEqual(msg.mime_type, 'image/jpeg')
        self.assertEqual(msg.content, 'legenda')
        self.assertTrue(msg.media_file.name)
        args = mock_send.call_args.args  # (key, number, kind, content, filename, content_type, caption)
        self.assertEqual((args[0], args[1], args[2], args[5]), ('tok', '5511999999999', 'image', 'image/jpeg'))

    @patch('apps.evolution.evolution.send_media_file')
    def test_document_passes_filename_and_placeholder(self, mock_send):
        msg = self._send(SimpleUploadedFile('relatorio.pdf', b'%PDF-1.4', content_type='application/pdf'))
        self.assertEqual(msg.message_type, 'document')
        self.assertEqual(mock_send.call_args.args[4], 'relatorio.pdf')  # filename vai pro DocumentMessage.FileName
        self.assertEqual(msg.content, '[documento: relatorio.pdf]')

    @patch('apps.evolution.evolution.send_sticker')
    def test_webp_routes_to_sticker_by_url(self, mock_sticker):
        # /send/sticker busca a imagem por URL (data-URI é recusado com "invalid sticker URL"),
        # então a mensagem precisa ser persistida antes do envio pra ter URL de storage.
        msg = self._send(SimpleUploadedFile('s.webp', b'RIFF', content_type='image/webp'))
        self.assertEqual(msg.message_type, 'sticker')
        self.assertEqual(msg.content, '[figurinha]')
        self.assertEqual(mock_sticker.call_args.args[2], msg.media_file.url)

    def test_rejects_unsupported_mime(self):
        with self.assertRaises(ValidationError):
            self._send(SimpleUploadedFile('a.gif', b'GIF89a', content_type='image/gif'))


@override_settings(MEDIA_ROOT=_MEDIA_TMP)
class EvolutionStickerServiceTests(BaseEvolutionTestCase):
    """Galeria de figurinhas company-scoped (compartilhada com o canal oficial)."""

    def setUp(self):
        super().setUp()
        self.account = EvolutionAccount.objects.create(company=self.company, user=self.owner, instance_name='c1u1', instance_api_key='tok')
        self.contact = EvolutionContact.objects.create(account=self.account, wa_id='5511999999999')

    def _incoming_sticker(self, media=b'RIFFwebp'):
        return EvolutionMessage.objects.create(
            contact=self.contact,
            direction=EvolutionMessage.Direction.IN,
            content='[figurinha]',
            message_type=EvolutionMessage.Type.STICKER,
            media_file=ContentFile(media, name='s.webp') if media else None,
            mime_type='image/webp',
        )

    def test_save_sticker_creates_gallery_entry(self):
        msg = self._incoming_sticker()
        sticker = services.save_sticker(self.company, self.owner, self.account.id, msg.id)
        self.assertEqual(sticker.company, self.company)
        self.assertTrue(sticker.file.name)

    def test_save_sticker_non_sticker_message_not_found(self):
        text = EvolutionMessage.objects.create(contact=self.contact, direction=EvolutionMessage.Direction.IN, content='oi')
        with self.assertRaises(NotFound):
            services.save_sticker(self.company, self.owner, self.account.id, text.id)

    def test_save_sticker_without_file_not_found(self):
        msg = self._incoming_sticker(media=None)
        with self.assertRaises(NotFound):
            services.save_sticker(self.company, self.owner, self.account.id, msg.id)

    @patch('apps.evolution.evolution.send_sticker')
    def test_send_saved_sticker_posts_url_and_persists(self, mock_sticker):
        sticker = Sticker.objects.create(company=self.company, file=ContentFile(b'RIFFwebp', name='s.webp'))
        msg = services.send_saved_sticker(self.company, self.owner, self.account.id, self.contact.id, sticker.id)
        self.assertEqual(msg.direction, EvolutionMessage.Direction.OUT)
        self.assertEqual(msg.message_type, 'sticker')
        self.assertEqual(msg.content, '[figurinha]')
        self.assertTrue(msg.media_file.name)
        args = mock_sticker.call_args.args  # (key, number, sticker_url)
        self.assertEqual((args[0], args[1]), ('tok', '5511999999999'))
        self.assertEqual(args[2], sticker.file.url)  # URL fetchável, não data-URI

    def test_send_saved_sticker_missing_not_found(self):
        with self.assertRaises(NotFound):
            services.send_saved_sticker(self.company, self.owner, self.account.id, self.contact.id, 999999)


# * ----- Webhook dispatch ------


class EvolutionWebhookDispatchTests(TestCase):
    def test_routes_event_to_handler(self):
        mock = MagicMock()
        with patch.dict('apps.evolution.services.EVENT_HANDLERS', {'Message': mock}):
            payload = {'event': 'Message', 'data': {}}
            services.handle_webhook(payload)
        mock.assert_called_once_with(payload)

    def test_unknown_event_is_noop(self):
        services.handle_webhook({'event': 'Nope'})  # não deve levantar
        services.handle_webhook({})


# * ----- Webhook handlers ------


@override_settings(MEDIA_ROOT=_MEDIA_TMP)
@patch('apps.chats.events.publish')
class EvolutionWebhookHandlerTests(BaseEvolutionTestCase):
    def setUp(self):
        super().setUp()
        self.account = EvolutionAccount.objects.create(
            company=self.company, user=self.owner, instance_name='c1u1', instance_api_key='inst-tok',
            status=EvolutionAccount.Status.CONNECTING,
        )

    def test_connection_open_marks_connected(self, mock_publish):
        services.handle_connection_webhook({
            'instanceToken': 'inst-tok',
            'data': {'status': 'open', 'jid': '5511999999999@s.whatsapp.net'},
        })
        self.account.refresh_from_db()
        self.assertEqual(self.account.status, EvolutionAccount.Status.CONNECTED)
        self.assertEqual(self.account.phone_number, '5511999999999')
        mock_publish.assert_called_once_with(
            self.company.id, {'type': 'evolution_status', 'account_id': self.account.pk, 'status': EvolutionAccount.Status.CONNECTED}
        )

    def test_logged_out_marks_disconnected(self, mock_publish):
        services.handle_disconnection_webhook({'instanceToken': 'inst-tok', 'data': {'Reason': 'banned'}})
        self.account.refresh_from_db()
        self.assertEqual(self.account.status, EvolutionAccount.Status.DISCONNECTED)
        self.assertEqual(self.account.last_logged_out_reason, 'banned')
        mock_publish.assert_called_once()

    def test_incoming_text_creates_message_and_publishes(self, mock_publish):
        services.handle_message_webhook({
            'instanceToken': 'inst-tok',
            'data': {
                'Info': {'IsFromMe': False, 'IsGroup': False, 'Sender': '5511999999999@s.whatsapp.net', 'Type': 'text', 'ID': 'MSG1'},
                'Message': {'conversation': 'olá'},
            },
        })
        self.assertEqual(EvolutionMessage.objects.filter(contact__account=self.account).count(), 1)
        contact = EvolutionContact.objects.get(account=self.account, wa_id='5511999999999')
        mock_publish.assert_called_once_with(
            self.company.id,
            {'type': 'message', 'contact_id': contact.pk, 'channel': 'evolution', 'account_id': self.account.pk},
        )

    @patch('apps.evolution.evolution.download_media', return_value=(b'\xff\xd8\xff', 'image/jpeg'))
    def test_incoming_media_downloads_and_stores_file(self, mock_download, mock_publish):
        services.handle_message_webhook({
            'instanceToken': 'inst-tok',
            'data': {
                'Info': {'IsFromMe': False, 'IsGroup': False, 'Sender': '5511999999999@s.whatsapp.net', 'Type': 'media', 'MediaType': 'image', 'ID': 'MSG2'},
                'Message': {'imageMessage': {'mimetype': 'image/jpeg'}, 'base64': 'ignored'},
            },
        })
        msg = EvolutionMessage.objects.get(contact__account=self.account)
        self.assertEqual(msg.message_type, 'image')
        self.assertEqual(msg.content, '[imagem]')
        self.assertEqual(msg.mime_type, 'image/jpeg')
        self.assertTrue(msg.media_file.name)  # arquivo persistido
        mock_download.assert_called_once()
        mock_publish.assert_called_once()

    @patch('apps.evolution.evolution.download_media', side_effect=EvolutionError('media unreachable'))
    def test_incoming_media_download_failure_keeps_placeholder(self, mock_download, mock_publish):
        # download falhou: não perde a mensagem, grava placeholder sem arquivo e publica
        services.handle_message_webhook({
            'instanceToken': 'inst-tok',
            'data': {
                'Info': {'IsFromMe': False, 'IsGroup': False, 'Sender': '5511999999999@s.whatsapp.net', 'Type': 'media', 'MediaType': 'audio', 'ID': 'MSG3'},
                'Message': {'audioMessage': {'mimetype': 'audio/ogg'}},
            },
        })
        msg = EvolutionMessage.objects.get(contact__account=self.account)
        self.assertEqual(msg.content, '[áudio]')
        self.assertFalse(msg.media_file)
        self.assertEqual(msg.mime_type, '')
        mock_publish.assert_called_once()

    def _text_webhook(self, push_name, wa_id='5511999999999', msg_id='MSG-PN'):
        services.handle_message_webhook({
            'instanceToken': 'inst-tok',
            'data': {
                'Info': {
                    'IsFromMe': False, 'IsGroup': False, 'Sender': f'{wa_id}@s.whatsapp.net',
                    'Type': 'text', 'ID': msg_id, 'PushName': push_name,
                },
                'Message': {'conversation': 'olá'},
            },
        })

    def test_incoming_message_saves_push_name(self, mock_publish):
        self._text_webhook('Fulano da Silva')
        self.assertEqual(EvolutionContact.objects.get(account=self.account, wa_id='5511999999999').name, 'Fulano da Silva')

    def test_push_name_fills_contact_without_name(self, mock_publish):
        contact = EvolutionContact.objects.create(account=self.account, wa_id='5511999999999')
        self._text_webhook('Fulano da Silva')
        contact.refresh_from_db()
        self.assertEqual(contact.name, 'Fulano da Silva')

    def test_push_name_does_not_overwrite_existing_name(self, mock_publish):
        # nome da agenda vale mais que o PushName (que a pessoa troca à vontade)
        contact = EvolutionContact.objects.create(account=self.account, wa_id='5511999999999', name='Nome Da Agenda')
        self._text_webhook('apelido do zap')
        contact.refresh_from_db()
        self.assertEqual(contact.name, 'Nome Da Agenda')

    def test_missing_push_name_keeps_contact_unnamed(self, mock_publish):
        self._text_webhook('')
        self.assertEqual(EvolutionContact.objects.get(account=self.account, wa_id='5511999999999').name, '')

    def test_from_me_is_ignored(self, mock_publish):
        services.handle_message_webhook({
            'instanceToken': 'inst-tok',
            'data': {'Info': {'IsFromMe': True, 'IsGroup': False, 'Sender': '5511@s', 'Type': 'text'}, 'Message': {'conversation': 'x'}},
        })
        self.assertEqual(EvolutionMessage.objects.count(), 0)
        mock_publish.assert_not_called()

    def test_unknown_instance_token_is_noop(self, mock_publish):
        services.handle_connection_webhook({'instanceToken': 'nope', 'data': {'status': 'open', 'jid': 'x@s'}})
        self.account.refresh_from_db()
        self.assertEqual(self.account.status, EvolutionAccount.Status.CONNECTING)
        mock_publish.assert_not_called()


# * ----- Endpoints ------


@override_settings(MEDIA_ROOT=_MEDIA_TMP)
class EvolutionEndpointTests(BaseEvolutionTestCase):
    @patch('apps.evolution.evolution.get_qr_code', return_value='QRDATA')
    @patch('apps.evolution.evolution.connect_instance', return_value='success')
    @patch('apps.evolution.evolution.create_instance', return_value='inst-tok')
    def test_connect_endpoint_creates_and_returns_qr(self, mock_create, mock_connect, mock_qr):
        response = self.client.post(f'{self.base}/evolution-accounts', {}, content_type='application/json', headers=self.member_auth)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['qrcode'], 'QRDATA')
        account = EvolutionAccount.objects.get(company=self.company, user=self.member)
        self.assertEqual(account.instance_name, f'c{self.company.id}u{self.member.id}')

    def test_connect_endpoint_outsider_blocked(self):
        response = self.client.post(f'{self.base}/evolution-accounts', {}, content_type='application/json', headers=self.outsider_auth)
        self.assertEqual(response.status_code, 404)

    @patch('apps.evolution.evolution.get_connection_status', side_effect=EvolutionError('down'))
    def test_me_returns_own_connection(self, mock_status):
        EvolutionAccount.objects.create(company=self.company, user=self.member, instance_name='c1um', phone_number='5511')
        response = self.client.get(f'{self.base}/evolution-accounts/me', headers=self.member_auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['phone_number'], '5511')

    def test_me_null_when_none(self):
        response = self.client.get(f'{self.base}/evolution-accounts/me', headers=self.member_auth)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json())

    def test_list_connections_owner(self):
        EvolutionAccount.objects.create(company=self.company, user=self.member, instance_name='c1um')
        response = self.client.get(f'{self.base}/evolution-accounts', headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)

    def test_list_connections_member_blocked(self):
        # owner-only: membro comum não lista conexões da equipe (get_owned_company -> 404)
        response = self.client.get(f'{self.base}/evolution-accounts', headers=self.member_auth)
        self.assertEqual(response.status_code, 404)

    @patch('apps.evolution.evolution.delete_instance')
    def test_disconnect_endpoint(self, mock_delete):
        account = EvolutionAccount.objects.create(company=self.company, user=self.member, instance_name='c1um', instance_api_key='tok')
        response = self.client.delete(f'{self.base}/evolution-accounts/{account.id}', headers=self.member_auth)
        self.assertEqual(response.status_code, 204)
        self.assertFalse(EvolutionAccount.objects.filter(pk=account.id).exists())

    def test_disconnect_other_users_account_forbidden(self):
        account = EvolutionAccount.objects.create(company=self.company, user=self.owner, instance_name='c1u1')
        response = self.client.delete(f'{self.base}/evolution-accounts/{account.id}', headers=self.member_auth)
        self.assertEqual(response.status_code, 403)

    @patch('apps.evolution.tasks.sync_avatars.delay')
    def test_list_contacts_and_messages(self, mock_delay):
        account = EvolutionAccount.objects.create(company=self.company, user=self.member, instance_name='c1um')
        contact = EvolutionContact.objects.create(account=account, wa_id='5511999999999', name='Cliente')
        EvolutionContact.objects.filter(pk=contact.pk).update(avatar_url='https://pps.whatsapp.net/v/foto.jpg')
        EvolutionMessage.objects.create(contact=contact, direction=EvolutionMessage.Direction.IN, content='oi')
        r1 = self.client.get(f'{self.base}/evolution-accounts/{account.id}/contacts', headers=self.member_auth)
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r1.json()[0]['wa_id'], '5511999999999')
        self.assertEqual(r1.json()[0]['avatar_url'], 'https://pps.whatsapp.net/v/foto.jpg')
        r2 = self.client.get(f'{self.base}/evolution-accounts/{account.id}/contacts/{contact.id}/messages', headers=self.member_auth)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.json()[0]['content'], 'oi')

    @patch('apps.evolution.evolution.get_device_contacts', return_value={'5511000000001': 'Bruno'})
    def test_directory_endpoint(self, mock_agenda):
        account = EvolutionAccount.objects.create(company=self.company, user=self.member, instance_name='c1um', instance_api_key='tok')
        response = self.client.get(f'{self.base}/evolution-accounts/{account.id}/directory', headers=self.member_auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [{'wa_id': '5511000000001', 'name': 'Bruno', 'source': 'agenda'}])

    @patch('apps.evolution.tasks.sync_avatars.delay')
    @patch('apps.evolution.evolution.check_number', return_value='554896436646')
    def test_start_conversation_endpoint(self, mock_check, mock_delay):
        account = EvolutionAccount.objects.create(company=self.company, user=self.member, instance_name='c1um', instance_api_key='tok')
        response = self.client.post(
            f'{self.base}/evolution-accounts/{account.id}/contacts',
            {'phone': '5548996436646', 'name': 'Patrícia'}, content_type='application/json', headers=self.member_auth,
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body['wa_id'], '554896436646')
        self.assertEqual(body['name'], 'Patrícia')
        self.assertTrue(EvolutionContact.objects.filter(account=account, wa_id='554896436646').exists())

    @patch('apps.evolution.evolution.check_number', return_value='')
    def test_start_conversation_endpoint_number_without_whatsapp(self, mock_check):
        account = EvolutionAccount.objects.create(company=self.company, user=self.member, instance_name='c1um', instance_api_key='tok')
        response = self.client.post(
            f'{self.base}/evolution-accounts/{account.id}/contacts',
            {'phone': '5511000000000'}, content_type='application/json', headers=self.member_auth,
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['detail'], 'number not on whatsapp')  # string que o front traduz no toast

    @patch('apps.evolution.evolution.check_number', return_value='5511999999999')
    def test_start_conversation_other_users_account_forbidden(self, mock_check):
        account = EvolutionAccount.objects.create(company=self.company, user=self.owner, instance_name='c1u1', instance_api_key='tok')
        response = self.client.post(
            f'{self.base}/evolution-accounts/{account.id}/contacts',
            {'phone': '5511999999999'}, content_type='application/json', headers=self.member_auth,
        )
        self.assertEqual(response.status_code, 403)

    @patch('apps.evolution.evolution.send_text')
    def test_send_message_endpoint(self, mock_send):
        account = EvolutionAccount.objects.create(company=self.company, user=self.member, instance_name='c1um', instance_api_key='tok')
        contact = EvolutionContact.objects.create(account=account, wa_id='5511999999999')
        response = self.client.post(
            f'{self.base}/evolution-accounts/{account.id}/contacts/{contact.id}/messages',
            {'content': 'resposta'}, content_type='application/json', headers=self.member_auth,
        )
        self.assertEqual(response.status_code, 201)
        mock_send.assert_called_once_with('tok', '5511999999999', 'resposta')
        self.assertEqual(EvolutionMessage.objects.get(contact=contact).direction, EvolutionMessage.Direction.OUT)

    @patch('apps.evolution.evolution.send_media_file')
    def test_send_media_endpoint_multipart(self, mock_send):
        account = EvolutionAccount.objects.create(company=self.company, user=self.member, instance_name='c1um', instance_api_key='tok')
        contact = EvolutionContact.objects.create(account=account, wa_id='5511999999999')
        file = SimpleUploadedFile('foto.jpg', b'\xff\xd8\xff', content_type='image/jpeg')
        response = self.client.post(
            f'{self.base}/evolution-accounts/{account.id}/contacts/{contact.id}/messages/media',
            {'file': file, 'caption': 'oi'}, headers=self.member_auth,
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body['message_type'], 'image')
        self.assertEqual(body['content'], 'oi')
        self.assertIsNotNone(body['media_url'])
        mock_send.assert_called_once()

    def test_save_sticker_endpoint(self):
        account = EvolutionAccount.objects.create(company=self.company, user=self.member, instance_name='c1um', instance_api_key='tok')
        contact = EvolutionContact.objects.create(account=account, wa_id='5511999999999')
        msg = EvolutionMessage.objects.create(
            contact=contact, direction=EvolutionMessage.Direction.IN, content='[figurinha]',
            message_type=EvolutionMessage.Type.STICKER, media_file=ContentFile(b'RIFFwebp', name='s.webp'), mime_type='image/webp',
        )
        response = self.client.post(
            f'{self.base}/evolution-accounts/{account.id}/stickers',
            {'message_id': msg.id}, content_type='application/json', headers=self.member_auth,
        )
        self.assertEqual(response.status_code, 201)
        self.assertIsNotNone(response.json()['url'])
        self.assertEqual(Sticker.objects.filter(company=self.company).count(), 1)

    @patch('apps.evolution.evolution.send_sticker')
    def test_send_saved_sticker_endpoint(self, mock_sticker):
        account = EvolutionAccount.objects.create(company=self.company, user=self.member, instance_name='c1um', instance_api_key='tok')
        contact = EvolutionContact.objects.create(account=account, wa_id='5511999999999')
        sticker = Sticker.objects.create(company=self.company, file=ContentFile(b'RIFFwebp', name='s.webp'))
        response = self.client.post(
            f'{self.base}/evolution-accounts/{account.id}/contacts/{contact.id}/messages/sticker',
            {'sticker_id': sticker.id}, content_type='application/json', headers=self.member_auth,
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['message_type'], 'sticker')
        mock_sticker.assert_called_once()
