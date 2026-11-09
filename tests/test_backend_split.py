import unittest

from tgplayer.backend.backend import (
    TelegramBackend, _error_text, _normalize_phone)
from tgplayer.backend.cache import CacheMixin
from tgplayer.backend.catalog import CatalogMixin
from tgplayer.backend.connection import ConnectionMixin
from tgplayer.backend.download import DownloadMixin
from tgplayer.backend.login import LoginMixin
from tgplayer.backend.playback import PlaybackMixin
from tgplayer.backend.stream import StreamMixin
from tgplayer.backend.videos import VideoMixin


class BackendSplitTests(unittest.TestCase):
    def test_backend_is_assembled_from_mixins(self):
        for mixin in (ConnectionMixin, LoginMixin, CatalogMixin, VideoMixin,
                      CacheMixin, DownloadMixin, StreamMixin, PlaybackMixin):
            self.assertTrue(issubclass(TelegramBackend, mixin), mixin.__name__)

    def test_domains_expose_their_main_methods(self):
        expected = {
            'reconnect': ConnectionMixin,
            'answer_login': LoginMixin,
            'load_data': CatalogMixin,
            'load_videos_from_dialog': VideoMixin,
            'cleanup_cache': CacheMixin,
            'ensure_cache': DownloadMixin,
            'handle_stream': StreamMixin,
            'record_history': PlaybackMixin,
        }
        for name, mixin in expected.items():
            self.assertTrue(hasattr(TelegramBackend, name), name)
            self.assertTrue(hasattr(mixin, name), name)

    def test_runtime_helpers_are_reexported(self):
        # tgplayer_runtime.py берёт эти функции из backend.backend.
        self.assertEqual(_normalize_phone('+7 (999) 123-45-67'),
                         '+79991234567')
        self.assertEqual(_error_text(RuntimeError('')), 'RuntimeError')


if __name__ == '__main__':
    unittest.main()
