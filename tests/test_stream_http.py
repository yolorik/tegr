import asyncio
import os
import sys
import tempfile
import unittest

# Windows CI может запускать тесты с кодировкой cp1252; backend пишет
# русские строки в stdout, и print падал бы с UnicodeEncodeError.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from tgplayer.backend.backend import TelegramBackend
from tgplayer.backend.cache_math import DownloadTracker, _open_read


class _Document:
    id = 1
    size = 100
    mime_type = 'video/mp4'


class _Media:
    document = _Document()


class _Message:
    media = _Media()


class StreamHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        fd, self.path = tempfile.mkstemp()
        os.write(fd, bytes(range(100)))
        os.close(fd)

        tracker = DownloadTracker(100)
        tracker.add_range(0, 99)
        self.entry = {
            'path': self.path,
            'size': 100,
            'complete': True,
            'tracker': tracker,
            'rfh': _open_read(self.path),
            'pieces': [(0, 100)],
            'piece_count': 1,
            'piece_done': {0},
            'active': set(),
            'read_gen': 0,
            'tasks': [],
            'message': None,
        }

        backend = TelegramBackend.__new__(TelegramBackend)
        self.backend = backend
        backend.stream_token = 'secret'
        backend.current_messages = [_Message()]
        backend._playlists = {}
        backend.cache = {1: self.entry}

        async def ensure_cache(_message, **_kwargs):
            return self.entry

        backend.ensure_cache = ensure_cache
        backend.prioritize_position = lambda _entry, _pos: None
        backend._available_from = (
            lambda entry, start: entry['tracker'].available_bytes_from(start))

        app = web.Application()
        app.router.add_get('/stream', backend.handle_stream)
        self.server = TestServer(app)
        self.client = TestClient(self.server)
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        await self.server.close()
        try:
            self.entry['rfh'].close()
        except Exception:
            pass
        os.remove(self.path)

    async def get(self, range_header):
        return await self.client.get(
            '/stream?idx=0&t=secret', headers={'Range': range_header})

    async def test_full_file(self):
        response = await self.client.get('/stream?idx=0&t=secret')
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.read(), bytes(range(100)))
        self.assertEqual(response.headers['Accept-Ranges'], 'bytes')

    async def test_closed_range(self):
        response = await self.get('bytes=10-19')
        self.assertEqual(response.status, 206)
        self.assertEqual(
            response.headers['Content-Range'], 'bytes 10-19/100')
        self.assertEqual(await response.read(), bytes(range(10, 20)))

    async def test_suffix_range(self):
        response = await self.get('bytes=-10')
        self.assertEqual(response.status, 206)
        self.assertEqual(
            response.headers['Content-Range'], 'bytes 90-99/100')
        self.assertEqual(await response.read(), bytes(range(90, 100)))

    async def test_unsatisfiable_range(self):
        response = await self.get('bytes=200-')
        self.assertEqual(response.status, 416)
        self.assertEqual(
            response.headers['Content-Range'], 'bytes */100')

    async def test_missing_token_forbidden(self):
        response = await self.client.get('/stream?idx=0')
        self.assertEqual(response.status, 403)

    async def test_wrong_token_forbidden(self):
        response = await self.client.get('/stream?idx=0&t=wrong')
        self.assertEqual(response.status, 403)

    async def test_partial_file_waits_for_new_data(self):
        # Вторая половина файла ещё не скачана: читатель должен уснуть
        # на событии и проснуться, когда воркер запишет данные.
        self.entry['complete'] = False
        self.entry['tracker'] = DownloadTracker(100)
        self.entry['tracker'].add_range(0, 49)
        self.entry['rfh'].close()
        self.entry['rfh'] = None
        self.entry['data_event'] = asyncio.Event()

        loop = asyncio.get_running_loop()

        def prioritize(entry, _pos):
            def add_more():
                entry['tracker'].add_range(50, 99)
                entry['data_event'].set()
            loop.call_later(0.02, add_more)

        self.backend.prioritize_position = prioritize

        response = await self.client.get('/stream?idx=0&t=secret')
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.read(), bytes(range(100)))


if __name__ == '__main__':
    unittest.main()
