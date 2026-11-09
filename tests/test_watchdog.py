import time
import unittest

from tgplayer.backend.backend import TelegramBackend
from tgplayer.core import config


class WaitingReadersTests(unittest.TestCase):
    def setUp(self):
        self.backend = TelegramBackend.__new__(TelegramBackend)

    def test_fresh_reader_is_counted(self):
        self.backend.cache = {1: {'wanted': {0: time.monotonic()}}}
        self.assertTrue(self.backend._has_waiting_readers())

    def test_stale_reader_is_ignored(self):
        old = time.monotonic() - config.WANTED_TTL - 1
        self.backend.cache = {1: {'wanted': {0: old}}}
        self.assertFalse(self.backend._has_waiting_readers())

    def test_empty_wanted_is_ignored(self):
        self.backend.cache = {1: {'wanted': {}}}
        self.assertFalse(self.backend._has_waiting_readers())

    def test_complete_entry_is_ignored(self):
        self.backend.cache = {
            1: {'complete': True, 'wanted': {0: time.monotonic()}}}
        self.assertFalse(self.backend._has_waiting_readers())

    def test_fatal_error_entry_is_ignored(self):
        # У файла, который больше недоступен, читателя ждать нечего:
        # иначе наблюдатель зря запускал бы переподключение.
        self.backend.cache = {
            1: {'error': True, 'wanted': {0: time.monotonic()}}}
        self.assertFalse(self.backend._has_waiting_readers())


class ActiveDownloadsTests(unittest.TestCase):
    def setUp(self):
        self.backend = TelegramBackend.__new__(TelegramBackend)

    def test_active_piece_is_counted(self):
        self.backend.cache = {1: {'active': {0}}}
        self.assertTrue(self.backend._has_active_downloads())

    def test_fatal_error_is_not_active(self):
        self.backend.cache = {1: {'active': {0}, 'error': True}}
        self.assertFalse(self.backend._has_active_downloads())


class ReviveTests(unittest.IsolatedAsyncioTestCase):
    async def test_fatal_error_is_not_revived(self):
        backend = TelegramBackend.__new__(TelegramBackend)
        backend.cache = {
            1: {'error': True, 'complete': False, 'removed': False,
                'paused': False, 'tasks': [], 'document': object(),
                'fh': object(), 'queue': object()},
        }
        called = []

        async def ensure_cache(*args, **kwargs):
            called.append(True)

        backend.ensure_cache = ensure_cache
        await backend._revive_workers()
        self.assertEqual(called, [])


if __name__ == '__main__':
    unittest.main()
