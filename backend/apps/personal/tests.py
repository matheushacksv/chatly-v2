import base64
import hashlib
import hmac
import json
import tempfile
from datetime import timedelta
from unittest.mock import MagicMock, patch

import httpx
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import User
from apps.agents.models import Agent, Credential
from apps.chats.models import Contact, Sticker, WhatsAppAccount
from apps.companies.models import Company, Membership
from apps.personal import gateway, services, tasks
from apps.personal.models import PersonalAccount, PersonalContact, PersonalMessage
from apps.testing import auth_header
from core.exceptions import Conflict, Forbidden, GatewayError, NotFound, ValidationError

# mídia é escrita em disco (FileField); isola num temp pra não sujar o MEDIA_ROOT real
_MEDIA_TMP = tempfile.mkdtemp()
LOCAL_STORAGE = dict(
    MEDIA_ROOT=_MEDIA_TMP,
    STORAGES={
        'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    },
)
OPENWA = dict(
    OPENWA_API_URL='http://openwa:2785',
    OPENWA_API_KEY='chave-global',
    OPENWA_WEBHOOK_SECRET='segredo-webhook',
    OPENWA_WEBHOOK_URL='http://api:8000/api/personal/webhook',
    OPENWA_JID_SUFFIX='@s.whatsapp.net',
)
SESSION = 'b1f0c2d4-0000-4000-8000-000000000001'
ME = '5511888888888'
CLIENT = '5511999999999'


def _resp(json_data=None, status=200, content=None, headers=None):
    resp = MagicMock()
    resp.json.return_value = json_data if json_data is not None else {}
    resp.content = content if content is not None else (b'{}' if json_data is None else json.dumps(json_data).encode())
    resp.headers = headers or {}
    if status >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            'err', request=MagicMock(), response=MagicMock(status_code=status, text='erro')
        )
    return resp


def message_payload(event='message.received', wa_message_id='WAID1', **data):
    """Envelope + mensagem plana do OpenWA (formato validado em produção no pipe_chat)."""
    base = {
        'id': wa_message_id,
        'from': f'{CLIENT}@s.whatsapp.net',
        'to': f'{ME}@s.whatsapp.net',
        'body': 'oi',
        'type': 'text',
        'timestamp': 1719312000,
        'fromMe': False,
        'isGroup': False,
        'kind': 'individual',
        'contact': {'name': 'Fulano'},
    }
    base.update(data)
    return {'event': event, 'sessionId': SESSION, 'timestamp': '2026-09-28T12:00:00.000Z', 'data': base}


def from_me_payload(wa_message_id='WAIDOUT', **data):
    """Eco do que a própria pessoa mandou (painel ou celular): o contato é o destino."""
    return message_payload(
        'message.sent', wa_message_id, **{'from': f'{ME}@s.whatsapp.net', 'to': f'{CLIENT}@s.whatsapp.net', 'fromMe': True, 'contact': {'name': 'Eu Mesmo'}, **data}
    )


# * ----- Boundary (gateway.py) ------


@override_settings(**OPENWA)
@patch('apps.personal.gateway.httpx.request')
class GatewayTests(TestCase):
    def test_uses_global_key_and_api_base(self, mock_request):
        mock_request.return_value = _resp({'id': SESSION})
        self.assertEqual(gateway.create_session('c1u1'), SESSION)
        args, kwargs = mock_request.call_args
        self.assertEqual(args, ('POST', 'http://openwa:2785/api/sessions'))
        self.assertEqual(kwargs['headers'], {'X-API-Key': 'chave-global'})
        self.assertEqual(kwargs['json'], {'name': 'c1u1'})

    def test_find_session_id_by_name(self, mock_request):
        mock_request.return_value = _resp([{'id': 'x', 'name': 'outra'}, {'id': SESSION, 'name': 'c1u1'}])
        self.assertEqual(gateway.find_session_id('c1u1'), SESSION)
        self.assertEqual(gateway.find_session_id('nenhuma'), '')

    def test_get_qr_code_reads_qrcode_field(self, mock_request):
        mock_request.return_value = _resp({'qrCode': 'data:image/png;base64,AAA'})
        self.assertEqual(gateway.get_qr_code(SESSION), 'data:image/png;base64,AAA')

    def test_register_webhook_sends_secret(self, mock_request):
        mock_request.side_effect = [_resp([]), _resp({'id': 'w1'})]
        gateway.register_webhook(SESSION, 'http://api:8000/hook', ['message.received'], 'segredo')
        self.assertEqual(
            mock_request.call_args.kwargs['json'], {'url': 'http://api:8000/hook', 'events': ['message.received'], 'secret': 'segredo'}
        )

    def test_register_webhook_is_idempotent_by_url(self, mock_request):
        mock_request.return_value = _resp([{'id': 'w1', 'url': 'http://api:8000/hook'}])
        gateway.register_webhook(SESSION, 'http://api:8000/hook', ['message.received'], 'segredo')
        self.assertEqual(mock_request.call_count, 1)  # só o GET, sem POST

    def test_send_text_builds_jid_and_returns_message_id(self, mock_request):
        mock_request.return_value = _resp({'messageId': 'MID1', 'timestamp': 1})
        self.assertEqual(gateway.send_text(SESSION, CLIENT, 'oi'), 'MID1')
        self.assertEqual(mock_request.call_args.kwargs['json'], {'chatId': f'{CLIENT}@s.whatsapp.net', 'text': 'oi'})
        self.assertTrue(mock_request.call_args.args[1].endswith(f'/sessions/{SESSION}/messages/send-text'))

    def test_send_image_goes_base64_with_caption(self, mock_request):
        mock_request.return_value = _resp({'messageId': 'MID2'})
        gateway.send_media(SESSION, CLIENT, b'jpeg', 'image', 'image/jpeg', 'foto.jpg', 'legenda')
        self.assertTrue(mock_request.call_args.args[1].endswith('/messages/send-image'))
        body = mock_request.call_args.kwargs['json']
        self.assertEqual(base64.b64decode(body['base64']), b'jpeg')
        self.assertEqual((body['mimetype'], body['filename'], body['caption']), ('image/jpeg', 'foto.jpg', 'legenda'))

    def test_send_sticker_goes_base64(self, mock_request):
        # base64 e não URL: não depende da mídia estar pública
        mock_request.return_value = _resp({'messageId': 'MID3'})
        gateway.send_media(SESSION, CLIENT, b'webp', 'sticker', 'image/webp')
        self.assertTrue(mock_request.call_args.args[1].endswith('/messages/send-sticker'))
        self.assertNotIn('url', mock_request.call_args.kwargs['json'])

    def test_webm_audio_is_converted_by_gateway_into_voice_note(self, mock_request):
        converted = base64.b64encode(b'opus').decode()
        mock_request.side_effect = [_resp({'base64': converted, 'mimetype': 'audio/ogg; codecs=opus'}), _resp({'messageId': 'MID4'})]
        gateway.send_media(SESSION, CLIENT, b'webm', 'audio', 'audio/webm')
        convert_call, send_call = mock_request.call_args_list
        self.assertTrue(convert_call.args[1].endswith('/media/convert/voice'))
        body = send_call.kwargs['json']
        self.assertEqual((body['base64'], body['mimetype'], body['ptt']), (converted, 'audio/ogg; codecs=opus', True))

    def test_ogg_audio_skips_conversion(self, mock_request):
        mock_request.return_value = _resp({'messageId': 'MID5'})
        gateway.send_media(SESSION, CLIENT, b'ogg', 'audio', 'audio/ogg')
        self.assertEqual(mock_request.call_count, 1)
        self.assertTrue(mock_request.call_args.kwargs['json']['ptt'])

    def test_failed_conversion_sends_plain_audio(self, mock_request):
        mock_request.side_effect = [_resp(status=503), _resp({'messageId': 'MID6'})]
        gateway.send_media(SESSION, CLIENT, b'mp3', 'audio', 'audio/mpeg')
        body = mock_request.call_args.kwargs['json']
        self.assertNotIn('ptt', body)
        self.assertEqual(body['mimetype'], 'audio/mpeg')

    def test_download_media_returns_raw_bytes_and_mime(self, mock_request):
        mock_request.return_value = _resp(content=b'bytes', headers={'content-type': 'image/jpeg; charset=binary'})
        self.assertEqual(gateway.download_media(SESSION, CLIENT, 'MID'), (b'bytes', 'image/jpeg'))
        self.assertTrue(mock_request.call_args.args[1].endswith(f'/messages/{CLIENT}@s.whatsapp.net/MID/media'))

    def test_check_number(self, mock_request):
        mock_request.return_value = _resp({'number': CLIENT, 'exists': True, 'whatsappId': f'{CLIENT}@s.whatsapp.net'})
        self.assertEqual(gateway.check_number(SESSION, CLIENT), CLIENT)
        mock_request.return_value = _resp({'number': CLIENT, 'exists': False, 'whatsappId': None})
        self.assertEqual(gateway.check_number(SESSION, CLIENT), '')

    def test_list_contacts_maps_number_to_name(self, mock_request):
        mock_request.return_value = _resp([
            {'id': 'a', 'number': '5511111111111', 'name': 'Da Agenda', 'pushName': 'Apelido'},
            {'id': 'b', 'number': '5522222222222', 'pushName': 'Só Apelido'},
            {'id': 'c', 'number': '5533333333333'},  # sem nome: fora
            {'id': 'd', 'number': '123@lid', 'name': 'Oculto'},  # não é telefone: fora
        ])
        self.assertEqual(gateway.list_contacts(SESSION), {'5511111111111': 'Da Agenda', '5522222222222': 'Só Apelido'})

    def test_profile_picture(self, mock_request):
        mock_request.return_value = _resp({'url': 'https://pps.whatsapp.net/x.jpg'})
        self.assertEqual(gateway.get_profile_picture(SESSION, CLIENT), 'https://pps.whatsapp.net/x.jpg')
        self.assertTrue(mock_request.call_args.args[1].endswith(f'/contacts/{CLIENT}@s.whatsapp.net/profile-picture'))
        mock_request.return_value = _resp({'url': None})
        self.assertEqual(gateway.get_profile_picture(SESSION, CLIENT), '')

    def test_404_raises_not_found(self, mock_request):
        mock_request.return_value = _resp(status=404)
        with self.assertRaises(NotFound):
            gateway.session_state(SESSION)

    def test_error_status_raises_gateway_error(self, mock_request):
        mock_request.return_value = _resp(status=409)
        with self.assertRaises(GatewayError):
            gateway.send_text(SESSION, CLIENT, 'oi')

    def test_network_error_raises_gateway_error(self, mock_request):
        mock_request.side_effect = httpx.ConnectError('caiu')
        with self.assertRaises(GatewayError):
            gateway.session_state(SESSION)


# * ----- Services ------


@override_settings(**OPENWA, **LOCAL_STORAGE)
class BasePersonalTestCase(TestCase):
    """Owner + membro + outsider numa company; conta pessoal do owner já com sessão."""

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
        self.base = f'/api/personal/{self.company.id}/accounts'
        patcher = patch('apps.chats.events.publish')
        self.mock_publish = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch('apps.personal.tasks.sync_avatars.delay')
        self.mock_avatar_delay = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch('apps.personal.tasks.fetch_message_media.delay')
        self.mock_media_delay = patcher.start()
        self.addCleanup(patcher.stop)

    def make_account(self, user=None, **fields):
        user = user or self.owner
        defaults = {'session_name': f'c{self.company.pk}u{user.pk}', 'session_id': SESSION, 'status': PersonalAccount.Status.CONNECTED}
        return PersonalAccount.objects.create(company=self.company, user=user, **{**defaults, **fields})


@patch('apps.personal.services.CLIENT_READY_INTERVAL', 0)
@patch('apps.personal.gateway.register_webhook')
@patch('apps.personal.gateway.start_session')
class ConnectServiceTests(BasePersonalTestCase):
    @patch('apps.personal.gateway.get_qr_code', return_value='QRDATA')
    @patch('apps.personal.gateway.session_state', return_value={'status': 'qr_ready'})
    @patch('apps.personal.gateway.create_session', return_value=SESSION)
    @patch('apps.personal.gateway.find_session_id', return_value='')
    def test_connect_creates_session_registers_webhook_and_returns_qr(self, mock_find, mock_create, mock_state, mock_qr, mock_start, mock_hook):
        result = services.connect(self.company, self.owner)
        self.assertEqual(result, {'qrcode': 'QRDATA'})
        account = PersonalAccount.objects.get(company=self.company, user=self.owner)
        self.assertEqual((account.session_id, account.session_name), (SESSION, f'c{self.company.pk}u{self.owner.pk}'))
        mock_hook.assert_called_once_with(SESSION, 'http://api:8000/api/personal/webhook', services.SUBSCRIBE, 'segredo-webhook')
        mock_start.assert_not_called()  # já estava de pé (qr_ready): start de novo dá 400 no gateway

    @patch('apps.personal.gateway.get_pair_code', return_value='ABCD-1234')
    @patch('apps.personal.gateway.session_state', side_effect=[{'status': 'created'}, {'status': 'created'}, {'status': 'qr_ready'}])  # sync, ensure, wait
    def test_connect_with_phone_starts_stopped_session_and_returns_pair_code(self, mock_state, mock_pair, mock_start, mock_hook):
        self.make_account(status=PersonalAccount.Status.DISCONNECTED)
        result = services.connect(self.company, self.owner, '(11) 98888-7777')
        self.assertEqual(result, {'pair_code': 'ABCD-1234'})
        mock_start.assert_called_once_with(SESSION)
        mock_pair.assert_called_once_with(SESSION, '5511988887777')  # normalizado

    @patch('apps.personal.gateway.get_qr_code', return_value='QR')
    @patch('apps.personal.gateway.create_session', return_value='novo-id')
    @patch('apps.personal.gateway.find_session_id', return_value='')
    def test_stale_session_id_is_recreated(self, mock_find, mock_create, mock_qr, mock_start, mock_hook):
        # id guardado que o gateway não conhece mais (volume apagado, migração)
        account = self.make_account(status=PersonalAccount.Status.DISCONNECTED)
        with patch('apps.personal.gateway.session_state', side_effect=[NotFound(), NotFound(), {'status': 'created'}, {'status': 'qr_ready'}]):
            services.connect(self.company, self.owner)
        account.refresh_from_db()
        self.assertEqual(account.session_id, 'novo-id')

    @patch('apps.personal.gateway.session_state', return_value={'status': 'ready', 'phone': f'{ME}:3@s.whatsapp.net'})
    def test_connect_when_already_connected_raises_conflict(self, mock_state, mock_start, mock_hook):
        self.make_account()
        with self.assertRaises(Conflict):
            services.connect(self.company, self.owner)

    @patch('apps.personal.gateway.delete_session')
    @patch('apps.personal.gateway.logout_session')
    def test_disconnect_logs_out_deletes_session_and_account(self, mock_logout, mock_delete, mock_start, mock_hook):
        account = self.make_account()
        services.disconnect(self.company, self.owner, account.pk)
        mock_logout.assert_called_once_with(SESSION)
        mock_delete.assert_called_once_with(SESSION)
        self.assertFalse(PersonalAccount.objects.exists())

    @patch('apps.personal.gateway.delete_session', side_effect=GatewayError())
    @patch('apps.personal.gateway.logout_session', side_effect=NotFound())
    def test_disconnect_survives_gateway_failure(self, mock_logout, mock_delete, mock_start, mock_hook):
        account = self.make_account()
        services.disconnect(self.company, self.owner, account.pk)
        self.assertFalse(PersonalAccount.objects.exists())


@patch('apps.personal.gateway.session_state')
class SyncStatusTests(BasePersonalTestCase):
    def test_ready_marks_connected_and_captures_phone(self, mock_state):
        mock_state.return_value = {'status': 'ready', 'phone': f'{ME}:3@s.whatsapp.net'}
        account = self.make_account(status=PersonalAccount.Status.CONNECTING)
        services.get_my_connection(self.company, self.owner)
        account.refresh_from_db()
        self.assertEqual((account.status, account.phone_number), (PersonalAccount.Status.CONNECTED, ME))

    def test_still_pairing_does_not_downgrade(self, mock_state):
        mock_state.return_value = {'status': 'qr_ready'}
        account = self.make_account(status=PersonalAccount.Status.CONNECTING)
        services.get_my_connection(self.company, self.owner)
        account.refresh_from_db()
        self.assertEqual(account.status, PersonalAccount.Status.CONNECTING)

    def test_disconnected_after_connected_downgrades(self, mock_state):
        mock_state.return_value = {'status': 'disconnected'}
        account = self.make_account()
        services.get_my_connection(self.company, self.owner)
        account.refresh_from_db()
        self.assertEqual(account.status, PersonalAccount.Status.DISCONNECTED)

    def test_gateway_down_keeps_last_status(self, mock_state):
        mock_state.side_effect = GatewayError()
        account = self.make_account()
        services.get_my_connection(self.company, self.owner)
        account.refresh_from_db()
        self.assertEqual(account.status, PersonalAccount.Status.CONNECTED)

    def test_no_account_returns_none(self, mock_state):
        self.assertIsNone(services.get_my_connection(self.company, self.owner))
        mock_state.assert_not_called()


class PermissionTests(BasePersonalTestCase):
    def setUp(self):
        super().setUp()
        self.account = self.make_account(user=self.member)

    def test_owner_can_access_member_account(self):
        self.assertEqual(services._get_own_or_owned_account(self.company, self.owner, self.account.pk), self.account)

    def test_own_user_can_access(self):
        self.assertEqual(services._get_own_or_owned_account(self.company, self.member, self.account.pk), self.account)

    def test_third_member_forbidden(self):
        third = User.objects.create_user(email='third@a.com', password='strongpass123')
        Membership.objects.create(company=self.company, user=third)
        with self.assertRaises(Forbidden):
            services._get_own_or_owned_account(self.company, third, self.account.pk)

    def test_missing_account_not_found(self):
        with self.assertRaises(NotFound):
            services._get_own_or_owned_account(self.company, self.owner, 99999)


class ListContactsTests(BasePersonalTestCase):
    def setUp(self):
        super().setUp()
        self.account = self.make_account()

    def _contact(self, wa_id, name='', created_at=None, synced=True):
        contact = PersonalContact.objects.create(
            account=self.account, wa_id=wa_id, name=name, avatar_synced_at=timezone.now() if synced else None
        )
        if created_at:  # auto_now_add: só via update
            PersonalContact.objects.filter(pk=contact.pk).update(created_at=created_at)
        return contact

    @patch('apps.personal.gateway.list_contacts', return_value={})
    def test_orders_by_last_message_then_created_at(self, mock_agenda):
        now = timezone.now()
        old_with_msg = self._contact('5511000000001', 'A', created_at=now - timedelta(days=5))
        recent_silent = self._contact('5511000000002', 'B', created_at=now - timedelta(days=1))
        msg = PersonalMessage.objects.create(contact=old_with_msg, direction='in', content='oi')
        PersonalMessage.objects.filter(pk=msg.pk).update(created_at=now)
        ids = [c.pk for c in services.list_contacts(self.company, self.owner, self.account.pk)]
        self.assertEqual(ids, [old_with_msg.pk, recent_silent.pk])

    @patch('apps.personal.gateway.list_contacts', return_value={'5511000000001': 'Da Agenda'})
    def test_fills_only_missing_names_from_agenda(self, mock_agenda):
        unnamed = self._contact('5511000000001')
        named = self._contact('5511000000002', 'Já Tem')
        services.list_contacts(self.company, self.owner, self.account.pk)
        unnamed.refresh_from_db()
        named.refresh_from_db()
        self.assertEqual((unnamed.name, named.name), ('Da Agenda', 'Já Tem'))

    @patch('apps.personal.gateway.list_contacts')
    def test_all_named_skips_agenda_call(self, mock_agenda):
        self._contact('5511000000001', 'Nome')
        services.list_contacts(self.company, self.owner, self.account.pk)
        mock_agenda.assert_not_called()

    @patch('apps.personal.gateway.list_contacts', side_effect=GatewayError())
    def test_agenda_down_does_not_break_listing(self, mock_agenda):
        self._contact('5511000000001')
        self.assertEqual(len(services.list_contacts(self.company, self.owner, self.account.pk)), 1)

    @patch('apps.personal.gateway.list_contacts', return_value={})
    def test_queues_avatar_sync_only_when_stale(self, mock_agenda):
        self._contact('5511000000001', 'A')
        services.list_contacts(self.company, self.owner, self.account.pk)
        self.mock_avatar_delay.assert_not_called()
        self._contact('5511000000002', 'B', synced=False)
        services.list_contacts(self.company, self.owner, self.account.pk)
        self.mock_avatar_delay.assert_called_once_with(self.account.pk)


class DirectoryAndStartConversationTests(BasePersonalTestCase):
    def setUp(self):
        super().setUp()
        self.account = self.make_account()
        credential = Credential.objects.create(company=self.company, provider='anthropic', api_key='sk')
        agent = Agent.objects.create(company=self.company, name='A', prompt='p', credential=credential, model='m')
        official = WhatsAppAccount.objects.create(
            company=self.company, agent=agent, phone_number_id='1', waba_id='w', access_token='t', app_secret='s', verify_token='v'
        )
        Contact.objects.create(account=official, wa_id='5511000000003', name='Beto Oficial')
        Contact.objects.create(account=official, wa_id='5511000000001', name='Nome Oficial')
        other = Company.objects.create(name='Outra', owner=self.outsider)
        other_agent = Agent.objects.create(company=other, name='B', prompt='p', credential=Credential.objects.create(company=other, provider='anthropic', api_key='k'), model='m')
        other_official = WhatsAppAccount.objects.create(
            company=other, agent=other_agent, phone_number_id='2', waba_id='w2', access_token='t', app_secret='s', verify_token='v2'
        )
        Contact.objects.create(account=other_official, wa_id='5511000000009', name='Alheio')

    @patch('apps.personal.gateway.list_contacts', return_value={'5511000000001': 'Ana Agenda'})
    def test_directory_merges_agenda_and_official_without_leaking(self, mock_agenda):
        entries = services.list_directory(self.company, self.owner, self.account.pk)
        self.assertEqual(
            entries,
            [
                {'wa_id': '5511000000001', 'name': 'Ana Agenda', 'source': 'agenda'},  # agenda ganha no mesmo wa_id
                {'wa_id': '5511000000003', 'name': 'Beto Oficial', 'source': 'oficial'},
            ],
        )

    @patch('apps.personal.gateway.check_number', return_value='5548996436646')
    def test_start_conversation_uses_canonical_wa_id(self, mock_check):
        contact = services.start_conversation(self.company, self.owner, self.account.pk, '(48) 99643-6646', 'Novo')
        mock_check.assert_called_once_with(SESSION, '5548996436646')
        self.assertEqual((contact.wa_id, contact.name), ('5548996436646', 'Novo'))
        self.mock_avatar_delay.assert_called_once()

    @patch('apps.personal.gateway.check_number', return_value='')
    def test_number_without_whatsapp_is_rejected(self, mock_check):
        with self.assertRaises(ValidationError):
            services.start_conversation(self.company, self.owner, self.account.pk, '11999998888')
        self.assertFalse(PersonalContact.objects.exists())

    @patch('apps.personal.gateway.check_number')
    def test_invalid_number_never_reaches_gateway(self, mock_check):
        with self.assertRaises(ValidationError):
            services.start_conversation(self.company, self.owner, self.account.pk, '123')
        mock_check.assert_not_called()


@patch('apps.chats.events.publish')
class AvatarAndMediaTaskTests(BasePersonalTestCase):
    def setUp(self):
        super().setUp()
        self.account = self.make_account()

    @patch('apps.personal.gateway.get_profile_picture', side_effect=['https://pic/1.jpg', GatewayError()])
    def test_sync_avatars_stores_url_and_stamps_even_on_failure(self, mock_pic, mock_publish):
        a = PersonalContact.objects.create(account=self.account, wa_id='5511000000001')
        b = PersonalContact.objects.create(account=self.account, wa_id='5511000000002')
        tasks.sync_avatars(self.account.pk)
        a.refresh_from_db()
        b.refresh_from_db()
        self.assertEqual((a.avatar_url, b.avatar_url), ('https://pic/1.jpg', ''))
        self.assertIsNotNone(b.avatar_synced_at)  # não fica tentando em loop
        self.assertEqual(mock_publish.call_args.args[1]['type'], 'personal_contacts')

    @patch('apps.personal.gateway.get_profile_picture')
    def test_sync_avatars_skips_fresh(self, mock_pic, mock_publish):
        PersonalContact.objects.create(account=self.account, wa_id='5511000000001', avatar_synced_at=timezone.now())
        tasks.sync_avatars(self.account.pk)
        mock_pic.assert_not_called()

    @patch('apps.personal.gateway.download_media', return_value=(b'jpeg-bytes', 'image/jpeg'))
    def test_fetch_media_stores_file(self, mock_download, mock_publish):
        contact = PersonalContact.objects.create(account=self.account, wa_id=CLIENT)
        message = PersonalMessage.objects.create(contact=contact, direction='in', content='[imagem]', message_type='image', wa_message_id='MIDIMG')
        tasks.fetch_message_media(message.pk, 'foto.jpg')
        mock_download.assert_called_once_with(SESSION, CLIENT, 'MIDIMG')
        message.refresh_from_db()
        self.assertEqual((message.media_file.read(), message.mime_type), (b'jpeg-bytes', 'image/jpeg'))
        self.assertEqual(mock_publish.call_args.args[1]['channel'], 'personal')

    @patch('apps.personal.gateway.download_media', side_effect=NotFound())
    def test_fetch_media_failure_keeps_placeholder(self, mock_download, mock_publish):
        contact = PersonalContact.objects.create(account=self.account, wa_id=CLIENT)
        message = PersonalMessage.objects.create(contact=contact, direction='in', content='[imagem]', message_type='image', wa_message_id='MIDIMG')
        tasks.fetch_message_media(message.pk)
        message.refresh_from_db()
        self.assertFalse(message.media_file)


class SendServiceTests(BasePersonalTestCase):
    def setUp(self):
        super().setUp()
        self.account = self.make_account()
        self.contact = PersonalContact.objects.create(account=self.account, wa_id=CLIENT)

    @patch('apps.personal.gateway.send_text', return_value='MIDTXT')
    def test_send_text_records_gateway_id(self, mock_send):
        message = services.send_text_message(self.company, self.owner, self.account.pk, self.contact.pk, 'olá')
        mock_send.assert_called_once_with(SESSION, CLIENT, 'olá')
        self.assertEqual((message.wa_message_id, message.direction, message.status), ('MIDTXT', 'out', 'sent'))

    @patch('apps.personal.gateway.send_text', return_value='MIDTXT')
    def test_echo_arriving_before_http_response_does_not_duplicate(self, mock_send):
        # o webhook message.sent chegou antes da resposta do POST de envio
        services.handle_webhook(from_me_payload('MIDTXT', body='olá'))
        services.send_text_message(self.company, self.owner, self.account.pk, self.contact.pk, 'olá')
        self.assertEqual(PersonalMessage.objects.filter(wa_message_id='MIDTXT').count(), 1)

    @patch('apps.personal.gateway.send_text', return_value='MIDTXT')
    def test_echo_after_send_does_not_duplicate(self, mock_send):
        services.send_text_message(self.company, self.owner, self.account.pk, self.contact.pk, 'olá')
        services.handle_webhook(from_me_payload('MIDTXT', body='olá'))
        self.assertEqual(PersonalMessage.objects.count(), 1)

    @patch('apps.personal.gateway.send_text', side_effect=GatewayError())
    def test_send_failure_records_nothing(self, mock_send):
        with self.assertRaises(GatewayError):
            services.send_text_message(self.company, self.owner, self.account.pk, self.contact.pk, 'olá')
        self.assertFalse(PersonalMessage.objects.exists())

    @patch('apps.personal.gateway.send_media', return_value='MIDIMG')
    def test_send_image_persists_file(self, mock_send):
        file = SimpleUploadedFile('foto.jpg', b'jpeg-bytes', content_type='image/jpeg')
        message = services.send_media_message(self.company, self.owner, self.account.pk, self.contact.pk, file, 'legenda')
        mock_send.assert_called_once_with(SESSION, CLIENT, b'jpeg-bytes', 'image', 'image/jpeg', 'foto.jpg', 'legenda')
        self.assertEqual((message.content, message.message_type, message.media_file.read()), ('legenda', 'image', b'jpeg-bytes'))

    @patch('apps.personal.gateway.send_media', return_value='MIDDOC')
    def test_document_gets_named_placeholder(self, mock_send):
        file = SimpleUploadedFile('rel.pdf', b'%PDF', content_type='application/pdf')
        message = services.send_media_message(self.company, self.owner, self.account.pk, self.contact.pk, file)
        self.assertEqual((message.message_type, message.content), ('document', '[documento: rel.pdf]'))

    @patch('apps.personal.gateway.send_media', return_value='MIDSTK')
    def test_webp_goes_as_sticker(self, mock_send):
        file = SimpleUploadedFile('s.webp', b'webp', content_type='image/webp')
        services.send_media_message(self.company, self.owner, self.account.pk, self.contact.pk, file)
        self.assertEqual(mock_send.call_args.args[3], 'sticker')

    @patch('apps.personal.gateway.send_media', return_value='MIDAUD')
    def test_browser_webm_audio_is_accepted(self, mock_send):
        file = SimpleUploadedFile('voz.webm', b'webm', content_type='audio/webm')
        message = services.send_media_message(self.company, self.owner, self.account.pk, self.contact.pk, file)
        self.assertEqual(message.message_type, 'audio')

    @patch('apps.personal.gateway.send_media')
    def test_rejects_unsupported_mime(self, mock_send):
        file = SimpleUploadedFile('x.gif', b'gif', content_type='image/gif')
        with self.assertRaises(ValidationError):
            services.send_media_message(self.company, self.owner, self.account.pk, self.contact.pk, file)
        mock_send.assert_not_called()

    @patch('apps.personal.services.MAX_MEDIA_BYTES', 3)
    @patch('apps.personal.gateway.send_media')
    def test_rejects_file_above_gateway_cap(self, mock_send):
        file = SimpleUploadedFile('rel.pdf', b'%PDF-grande', content_type='application/pdf')
        with self.assertRaises(ValidationError):
            services.send_media_message(self.company, self.owner, self.account.pk, self.contact.pk, file)
        mock_send.assert_not_called()

    def test_save_sticker_creates_company_gallery_entry(self):
        message = PersonalMessage.objects.create(
            contact=self.contact, direction='in', content='[figurinha]', message_type='sticker', media_file=ContentFile(b'webp', name='s.webp')
        )
        sticker = services.save_sticker(self.company, self.owner, self.account.pk, message.pk)
        self.assertEqual((sticker.company, sticker.file.read()), (self.company, b'webp'))

    def test_save_sticker_non_sticker_not_found(self):
        message = PersonalMessage.objects.create(contact=self.contact, direction='in', content='oi')
        with self.assertRaises(NotFound):
            services.save_sticker(self.company, self.owner, self.account.pk, message.pk)

    @patch('apps.personal.gateway.send_media', return_value='MIDSTK')
    def test_send_saved_sticker_sends_bytes_and_persists(self, mock_send):
        sticker = Sticker.objects.create(company=self.company, file=ContentFile(b'webp-salvo', name='s.webp'))
        message = services.send_saved_sticker(self.company, self.owner, self.account.pk, self.contact.pk, sticker.pk)
        mock_send.assert_called_once_with(SESSION, CLIENT, b'webp-salvo', 'sticker', 'image/webp')
        self.assertEqual((message.wa_message_id, message.media_file.read()), ('MIDSTK', b'webp-salvo'))

    def test_send_saved_sticker_missing_not_found(self):
        with self.assertRaises(NotFound):
            services.send_saved_sticker(self.company, self.owner, self.account.pk, self.contact.pk, 99999)


class WebhookHandlerTests(BasePersonalTestCase):
    def setUp(self):
        super().setUp()
        self.account = self.make_account()

    def test_incoming_text_creates_contact_message_and_publishes(self):
        services.handle_webhook(message_payload(body='ola'))
        contact = PersonalContact.objects.get(account=self.account, wa_id=CLIENT)
        message = PersonalMessage.objects.get(contact=contact)
        self.assertEqual((contact.name, message.content, message.direction, message.wa_message_id), ('Fulano', 'ola', 'in', 'WAID1'))
        self.mock_publish.assert_called_once_with(
            self.company.pk, {'type': 'message', 'contact_id': contact.pk, 'channel': 'personal', 'account_id': self.account.pk}
        )

    def test_redelivery_does_not_duplicate(self):
        services.handle_webhook(message_payload())
        services.handle_webhook(message_payload())
        self.assertEqual(PersonalMessage.objects.count(), 1)
        self.assertEqual(self.mock_publish.call_count, 1)

    def test_session_found_by_name_too(self):
        payload = message_payload()
        payload['sessionId'] = self.account.session_name
        services.handle_webhook(payload)
        self.assertEqual(PersonalMessage.objects.count(), 1)

    def test_message_sent_from_phone_shows_as_outgoing(self):
        services.handle_webhook(from_me_payload(body='mandei do celular'))
        message = PersonalMessage.objects.get()
        self.assertEqual((message.direction, message.status, message.content), ('out', 'sent', 'mandei do celular'))
        self.assertEqual(message.contact.wa_id, CLIENT)
        self.assertEqual(message.contact.name, '')  # o nome do eco é o nosso, não o do contato

    def test_group_is_ignored(self):
        services.handle_webhook(message_payload(kind='group', isGroup=True, **{'from': '120363@g.us'}))
        self.assertFalse(PersonalContact.objects.exists())

    def test_lid_resolves_through_sender_phone(self):
        services.handle_webhook(message_payload(**{'from': '99887766@lid', 'senderPhone': f'{CLIENT}@s.whatsapp.net'}))
        self.assertTrue(PersonalContact.objects.filter(wa_id=CLIENT).exists())

    def test_lid_without_phone_is_dropped(self):
        services.handle_webhook(message_payload(**{'from': '99887766@lid'}))
        self.assertFalse(PersonalContact.objects.exists())

    def test_unsupported_type_creates_no_ghost_contact(self):
        services.handle_webhook(message_payload(type='location'))
        self.assertFalse(PersonalContact.objects.exists())

    def test_incoming_media_stores_placeholder_and_queues_download(self):
        services.handle_webhook(message_payload(type='image', body='', filename=''))
        message = PersonalMessage.objects.get()
        self.assertEqual((message.message_type, message.content), ('image', '[imagem]'))
        self.mock_media_delay.assert_called_once_with(message.pk, '')

    def test_incoming_sticker_stores_placeholder_and_queues_download(self):
        services.handle_webhook(message_payload(type='sticker', body=''))
        message = PersonalMessage.objects.get()
        self.assertEqual((message.message_type, message.content), ('sticker', '[figurinha]'))
        self.mock_media_delay.assert_called_once_with(message.pk, '')

    def test_voice_note_becomes_audio(self):
        services.handle_webhook(message_payload(type='ptt', body=''))
        self.assertEqual(PersonalMessage.objects.get().message_type, 'audio')

    def test_document_uses_caption_or_filename(self):
        services.handle_webhook(message_payload(type='document', body='', filename='contrato.pdf'))
        self.assertEqual(PersonalMessage.objects.get().content, 'contrato.pdf')
        self.mock_media_delay.assert_called_once_with(PersonalMessage.objects.get().pk, 'contrato.pdf')

    def test_push_name_fills_only_unnamed_contact(self):
        PersonalContact.objects.create(account=self.account, wa_id=CLIENT, name='Nome Da Agenda')
        services.handle_webhook(message_payload())
        self.assertEqual(PersonalContact.objects.get().name, 'Nome Da Agenda')

    def test_unknown_session_is_noop(self):
        payload = message_payload()
        payload['sessionId'] = 'desconhecida'
        services.handle_webhook(payload)
        self.assertFalse(PersonalMessage.objects.exists())

    def test_unknown_event_is_noop(self):
        services.handle_webhook({'event': 'presence.update', 'sessionId': SESSION, 'data': {}})
        self.mock_publish.assert_not_called()

    def test_ready_marks_connected_with_phone(self):
        self.account.status = PersonalAccount.Status.CONNECTING
        self.account.save()
        services.handle_webhook({'event': 'session.status', 'sessionId': SESSION, 'data': {'status': 'ready', 'phone': f'{ME}:3@s.whatsapp.net'}})
        self.account.refresh_from_db()
        self.assertEqual((self.account.status, self.account.phone_number), ('connected', ME))
        self.assertEqual(self.mock_publish.call_args.args[1]['type'], 'personal_status')

    @patch('apps.personal.gateway.session_state', return_value={'phone': f'{ME}@s.whatsapp.net'})
    def test_ready_without_phone_asks_gateway(self, mock_state):
        self.account.phone_number = ''
        self.account.save()
        services.handle_webhook({'event': 'session.status', 'sessionId': SESSION, 'data': {'status': 'ready'}})
        self.account.refresh_from_db()
        self.assertEqual(self.account.phone_number, ME)

    def test_pairing_status_keeps_connecting(self):
        self.account.status = PersonalAccount.Status.CONNECTING
        self.account.save()
        services.handle_webhook({'event': 'session.status', 'sessionId': SESSION, 'data': {'status': 'qr_ready'}})
        self.account.refresh_from_db()
        self.assertEqual(self.account.status, 'connecting')

    def test_disconnected_stores_reason(self):
        services.handle_webhook({'event': 'session.disconnected', 'sessionId': SESSION, 'data': {'reason': 'logged out'}})
        self.account.refresh_from_db()
        self.assertEqual((self.account.status, self.account.last_logged_out_reason), ('disconnected', 'logged out'))


# * ----- Endpoints ------


def sign(body: bytes, secret: str = 'segredo-webhook') -> str:
    return 'sha256=' + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class WebhookEndpointTests(BasePersonalTestCase):
    URL = '/api/personal/webhook'

    def setUp(self):
        super().setUp()
        self.account = self.make_account()

    def post(self, body: bytes, signature: str | None = None):
        return self.client.post(
            self.URL, data=body, content_type='application/json', headers={'X-OpenWA-Signature': sign(body) if signature is None else signature}
        )

    def test_signed_event_is_processed(self):
        response = self.post(json.dumps(message_payload()).encode())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(PersonalMessage.objects.count(), 1)

    def test_wrong_signature_returns_403_and_processes_nothing(self):
        body = json.dumps(message_payload()).encode()
        self.assertEqual(self.post(body, sign(body, 'outro-segredo')).status_code, 403)
        self.assertEqual(self.post(body, '').status_code, 403)
        self.assertFalse(PersonalMessage.objects.exists())

    def test_invalid_json_returns_400(self):
        self.assertEqual(self.post(b'{nao e json').status_code, 400)

    def test_internal_host_used_by_openwa_is_accepted(self):
        # o OpenWA chama http://api:8000: Host fora do ALLOWED_HOSTS virava 400 do Django e todo evento se perdia
        body = json.dumps(message_payload()).encode()
        response = self.client.post(
            self.URL, data=body, content_type='application/json', HTTP_HOST='api:8000', headers={'X-OpenWA-Signature': sign(body)}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(PersonalMessage.objects.count(), 1)
        # explícito: dev roda com ALLOWED_HOSTS=* e esconderia a falha; prod lista só o domínio
        self.assertIn('api', settings.ALLOWED_HOSTS)

    def test_non_object_json_returns_200(self):
        self.assertEqual(self.post(b'[1, 2]').status_code, 200)


@patch('apps.personal.services.CLIENT_READY_INTERVAL', 0)
class EndpointTests(BasePersonalTestCase):
    @patch('apps.personal.gateway.get_qr_code', return_value='QR')
    @patch('apps.personal.gateway.register_webhook')
    @patch('apps.personal.gateway.session_state', return_value={'status': 'qr_ready'})
    @patch('apps.personal.gateway.create_session', return_value=SESSION)
    @patch('apps.personal.gateway.find_session_id', return_value='')
    def test_connect_endpoint_returns_qr(self, *mocks):
        response = self.client.post(self.base, data={}, content_type='application/json', headers=self.member_auth)
        self.assertEqual(response.status_code, 201, response.json())
        self.assertEqual(response.json(), {'qrcode': 'QR', 'pair_code': ''})

    def test_connect_endpoint_outsider_blocked(self):
        response = self.client.post(self.base, data={}, content_type='application/json', headers=self.outsider_auth)
        self.assertEqual(response.status_code, 404)

    @patch('apps.personal.gateway.session_state', return_value={'status': 'ready', 'phone': ME})
    def test_me_returns_own_connection(self, mock_state):
        self.make_account()
        body = self.client.get(f'{self.base}/me', headers=self.auth).json()
        self.assertEqual((body['session_name'], body['status'], body['user_email']), (f'c{self.company.pk}u{self.owner.pk}', 'connected', 'owner@a.com'))

    def test_me_null_when_none(self):
        response = self.client.get(f'{self.base}/me', headers=self.auth)
        self.assertEqual((response.status_code, response.json()), (200, None))

    def test_list_connections_owner_only(self):
        self.make_account(user=self.member)
        self.assertEqual(len(self.client.get(self.base, headers=self.auth).json()), 1)
        self.assertEqual(self.client.get(self.base, headers=self.member_auth).status_code, 404)

    @patch('apps.personal.gateway.delete_session')
    @patch('apps.personal.gateway.logout_session')
    def test_disconnect_other_users_account_forbidden(self, mock_logout, mock_delete):
        account = self.make_account()
        third = User.objects.create_user(email='third@a.com', password='strongpass123')
        Membership.objects.create(company=self.company, user=third)
        response = self.client.delete(f'{self.base}/{account.pk}', headers=auth_header(third))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(PersonalAccount.objects.exists())

    @patch('apps.personal.gateway.send_text', return_value='MIDTXT')
    def test_send_message_and_list_messages(self, mock_send):
        account = self.make_account()
        contact = PersonalContact.objects.create(account=account, wa_id=CLIENT)
        url = f'{self.base}/{account.pk}/contacts/{contact.pk}/messages'
        response = self.client.post(url, data={'content': 'oi'}, content_type='application/json', headers=self.auth)
        self.assertEqual(response.status_code, 201, response.json())
        self.assertEqual(response.json()['wa_message_id'], 'MIDTXT')
        self.assertEqual([m['content'] for m in self.client.get(url, headers=self.auth).json()], ['oi'])

    @patch('apps.personal.gateway.send_media', return_value='MIDIMG')
    def test_send_media_endpoint_multipart(self, mock_send):
        account = self.make_account()
        contact = PersonalContact.objects.create(account=account, wa_id=CLIENT)
        file = SimpleUploadedFile('foto.jpg', b'jpeg-bytes', content_type='image/jpeg')
        response = self.client.post(
            f'{self.base}/{account.pk}/contacts/{contact.pk}/messages/media', data={'file': file, 'caption': 'olha'}, headers=self.auth
        )
        self.assertEqual(response.status_code, 201, response.json())
        self.assertIsNotNone(response.json()['media_url'])

    @patch('apps.personal.gateway.send_text', side_effect=GatewayError())
    def test_gateway_failure_returns_502(self, mock_send):
        account = self.make_account()
        contact = PersonalContact.objects.create(account=account, wa_id=CLIENT)
        response = self.client.post(
            f'{self.base}/{account.pk}/contacts/{contact.pk}/messages', data={'content': 'oi'}, content_type='application/json', headers=self.auth
        )
        self.assertEqual(response.status_code, 502)
