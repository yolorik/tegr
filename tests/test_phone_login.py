import unittest
from types import SimpleNamespace

from tgplayer.backend.common import _normalize_phone
from tgplayer.backend.login import LoginMixin


class PhoneNormalizationTests(unittest.TestCase):
    def test_plus_and_spaces(self):
        self.assertEqual(_normalize_phone('+7 (999) 123-45-67'),
                         '+79991234567')

    def test_russian_trunk_eight(self):
        # Люди часто набирают российский номер как 8XXXXXXXXXX.
        self.assertEqual(_normalize_phone('8 (978) 578-27-89'),
                         '+79785782789')

    def test_explicit_plus_eight_is_not_rewritten(self):
        self.assertEqual(_normalize_phone('+89785782789'),
                         '+89785782789')

    def test_empty(self):
        self.assertEqual(_normalize_phone(''), '')
        self.assertEqual(_normalize_phone(None), '')


class _Signal:
    def emit(self, *_args):
        pass


class _Client:
    def __init__(self):
        self.phone = None
        self.sign_args = None

    async def send_code_request(self, phone):
        self.phone = phone
        return SimpleNamespace(phone_code_hash='demo-hash')

    async def sign_in(self, **kwargs):
        self.sign_args = kwargs
        return SimpleNamespace()

    async def get_me(self):
        return SimpleNamespace(username='demo', id=1)


class _Backend(LoginMixin):
    def __init__(self, answers):
        self.client = _Client()
        self.answers = list(answers)
        self.status_changed = _Signal()
        self.errors = []

    async def _ask_login(self, kind, message):
        if kind == 'error':
            self.errors.append(message)
            return None
        return self.answers.pop(0)


class PhoneLoginFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_phone_login_uses_international_format(self):
        backend = _Backend(['8 (978) 578-27-89', '12345'])
        status = await backend._interactive_login()

        self.assertEqual(status, 'ok')
        self.assertEqual(backend.client.phone, '+79785782789')
        self.assertEqual(backend.client.sign_args['phone'],
                         '+79785782789')
        self.assertEqual(backend.client.sign_args['code'], '12345')
        self.assertEqual(backend.client.sign_args['phone_code_hash'],
                         'demo-hash')

    async def test_short_phone_is_rejected_before_network(self):
        backend = _Backend(['123'])
        status = await backend._interactive_login()

        self.assertEqual(status, 'cancel')
        self.assertIsNone(backend.client.phone)
        self.assertTrue(backend.errors)


if __name__ == '__main__':
    unittest.main()
