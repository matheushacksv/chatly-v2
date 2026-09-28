from datetime import timedelta
from typing import cast
from unittest.mock import patch

from django.contrib.auth.tokens import default_token_generator
from django.test import TestCase
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from ninja_jwt.tokens import RefreshToken

from apps.accounts.models import SiteConfig, User
from apps.companies.models import Company, Invite


class RegisterTests(TestCase):
    url = '/api/auth/register'

    def test_register_success(self):
        response = self.client.post(
            self.url, data={'email': 'a@a.com', 'password': 'strongpass123', 'repeat_password': 'strongpass123'}, content_type='application/json'
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(User.objects.filter(email='a@a.com').exists())

    def test_register_duplicate_email(self):
        User.objects.create_user(email='a@a.com', password='strongpass123')
        response = self.client.post(
            self.url, data={'email': 'a@a.com', 'password': 'strongpass123', 'repeat_password': 'strongpass123'}, content_type='application/json'
        )
        self.assertEqual(response.status_code, 409)


class RegistrationControlTests(TestCase):
    url = '/api/auth/register'

    def _post(self, email, invite=None):
        data = {'email': email, 'password': 'strongpass123', 'repeat_password': 'strongpass123'}
        if invite is not None:
            data['invite'] = invite
        return self.client.post(self.url, data=data, content_type='application/json')

    def _invite(self, days_old=0):
        owner = User.objects.create_user(email=f'owner{days_old}@a.com', password='strongpass123')
        company = Company.objects.create(name='Acme', owner=owner)
        invite = Invite.objects.create(company=company)
        if days_old:
            # created_at é auto_now_add; sobrescreve via .update pra simular convite velho
            Invite.objects.filter(pk=invite.pk).update(created_at=timezone.now() - timedelta(days=days_old))
        return invite

    def test_open_by_default_no_config_row(self):
        self.assertTrue(SiteConfig.registration_is_open())
        self.assertEqual(self._post('open@a.com').status_code, 201)

    def test_closed_blocks_registration(self):
        SiteConfig.objects.create(registration_open=False)
        response = self._post('blocked@a.com')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {'detail': 'cadastros fechados no momento'})
        self.assertFalse(User.objects.filter(email='blocked@a.com').exists())

    def test_open_config_allows_registration(self):
        SiteConfig.objects.create(registration_open=True)
        self.assertEqual(self._post('allowed@a.com').status_code, 201)

    def test_closed_with_valid_invite_allows(self):
        SiteConfig.objects.create(registration_open=False)
        invite = self._invite()
        response = self._post('invited@a.com', invite=invite.token)
        self.assertEqual(response.status_code, 201)
        self.assertTrue(User.objects.filter(email='invited@a.com').exists())

    def test_closed_with_expired_invite_blocks(self):
        SiteConfig.objects.create(registration_open=False)
        invite = self._invite(days_old=8)
        self.assertEqual(self._post('late@a.com', invite=invite.token).status_code, 403)

    def test_closed_with_unknown_invite_blocks(self):
        SiteConfig.objects.create(registration_open=False)
        self.assertEqual(self._post('fake@a.com', invite='does-not-exist').status_code, 403)


class LoginTests(TestCase):
    url = '/api/auth/login'

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(email='a2@a.com', password='strongpass123')

    def test_login_success(self):
        response = self.client.post(self.url, data={'email': 'a2@a.com', 'password': 'strongpass123'}, content_type='application/json')
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn('access', body)
        self.assertIn('refresh', body)

    def test_login_invalid_password(self):
        response = self.client.post(self.url, data={'email': 'a2@a.com', 'password': '1234'}, content_type='application/json')
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {'detail': 'invalid credentials'})

    def test_login_invalid_email(self):
        response = self.client.post(self.url, data={'email': 'a124@a.com', 'password': 'strongpass123'}, content_type='application/json')
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {'detail': 'invalid credentials'})


class RefreshTests(TestCase):
    url = '/api/auth/refresh'

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(email='a3@a.com', password='strongpass123')

    def test_refresh_success(self):
        refresh = str(RefreshToken.for_user(self.user))
        response = self.client.post(self.url, data={'refresh': refresh}, content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertIn('access', response.json())

    def test_invalid_token(self):
        response = self.client.post(self.url, data={'refresh': 'not-a-token'}, content_type='application/json')
        self.assertEqual(response.status_code, 401)


class ChangePasswordTests(TestCase):
    url = '/api/auth/change-password'

    def setUp(self):
        self.user = User.objects.create_user(email='a@a.com', password='oldpass123')
        access = cast(RefreshToken, RefreshToken.for_user(self.user)).access_token
        self.auth = {'Authorization': f'Bearer {access}'}

    def test_change_password_success(self):
        response = self.client.post(
            self.url,
            data={'current_password': 'oldpass123', 'new_password': 'newpass12345', 'repeat_password': 'newpass12345'},
            content_type='application/json',
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 204)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('newpass12345'))

    def post(self, current='oldpass123', new='newpass12345', repeat=None, auth=True):
        return self.client.post(
            self.url,
            data={'current_password': current, 'new_password': new, 'repeat_password': repeat if repeat is not None else new},
            content_type='application/json',
            headers=self.auth if auth else {},
        )

    def test_wrong_current_password_is_400_not_401(self):
        # 401 faria o front tentar renovar o token; é erro de formulário
        response = self.post(current='errada')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'detail': 'senha atual incorreta'})
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('oldpass123'))

    def test_weak_new_password_is_rejected(self):
        response = self.post(new='123')
        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('oldpass123'))

    def test_repeat_mismatch_is_rejected(self):
        response = self.post(repeat='outra-coisa-123')
        self.assertEqual(response.status_code, 422)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('oldpass123'))

    def test_requires_login(self):
        self.assertEqual(self.post(auth=False).status_code, 401)


class ForgotPasswordTests(TestCase):
    url = '/api/auth/forgot-password'

    @patch('apps.accounts.services.send_reset_email_task')
    def test_forgot_existing_email(self, mock_task):
        user = User.objects.create_user(email='a@a.com', password='strongpass123')
        response = self.client.post(self.url, data={'email': 'a@a.com'}, content_type='application/json')
        self.assertEqual(response.status_code, 202)
        mock_task.delay.assert_called_once_with(user.pk)

    @patch('apps.accounts.services.send_reset_email_task')
    def test_forgot_unknown_email(self, mock_task):
        response = self.client.post(self.url, data={'email': 'x@x.com'}, content_type='application/json')
        self.assertEqual(response.status_code, 202)
        mock_task.delay.assert_not_called()


class ResetPasswordTests(TestCase):
    url = '/api/auth/reset-password'

    def setUp(self):
        self.user = User.objects.create_user(email='b@b.com', password='strongpass123')
        self.uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        self.token = default_token_generator.make_token(self.user)

    def test_reset_success(self):
        response = self.client.post(
            self.url,
            data={'uid': self.uid, 'token': self.token, 'new_password': 'newpass12345'},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('newpass12345'))
