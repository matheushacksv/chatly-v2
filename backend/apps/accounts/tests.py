from typing import cast
from unittest.mock import patch

from django.contrib.auth.tokens import default_token_generator
from django.test import TestCase
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from ninja_jwt.tokens import RefreshToken

from apps.accounts.models import User


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
