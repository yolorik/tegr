import asyncio
import os
import sys
import tempfile
import time
import unittest

# Windows CI может запускать тесты с кодировкой cp1252; backend пишет
# русские строки в stdout, и print падал бы с UnicodeEncodeError.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

from tgplayer.backend.backend import TelegramBackend
from tgplayer.backend.cache_math import DownloadTracker


class _Document:
    id = 1
    size = 8


class _Stream:
    """Один поток iter_download: отдаёт заранее заданные куски."""

    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.chunks:
            raise StopAsyncIteration
        return self.chunks.pop(0)

    async def aclose(self):
        self.closed = True


class _ErrorStream:
    """Поток, который сразу падает: имитация временной сетевой ошибки."""

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise RuntimeError('boom')

    async def aclose(self):
        pass


class _Flood(Exception):
    """Подделка FloodWaitError: воркер смотрит на тип и seconds."""

    seconds = 0


class _FloodStream:
    """Поток, который просит подождать вместо выдачи данных."""

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise _Flood()

    async def aclose(self):
        pass


class _Client:
    def __init__(self, streams):
        self.streams = list(streams)
        self.calls = 0

    def iter_download(self, _source, **_kwargs):
        self.calls += 1
        if self.streams:
            return self.streams.pop(0)
        return _Stream([])


def make_entry(path):
    return {
        'fh': open(path, 'r+b', buffering=1024 * 1024),
        'rfh': None,
        'size': 8,
        'complete': False,
        'error': False,
        'tasks': [],
        'document': _Document(),
        'message': None,
        'path': path,
        'pieces': [(0, 8)],
        'piece_count': 1,
        'piece_done': set(),
        'downloaded_bytes': 0,
        'tracker': DownloadTracker(8),
        'queue': asyncio.PriorityQueue(),
        'queued': {0},
        'active': set(),
        'boosted': set(),
        'data_event': asyncio.Event(),
        'speed_bytes': 0,
        'speed_total': 0,
        'first_byte_ts': 0.0,
        'speed_window_bytes': 0,
        'speed_window_ts': time.monotonic(),
        'speed_bps': 0.0,
        'last_meta_save': 0.0,
        'started_ts': time.monotonic(),
        'wanted': {},
        'read_gen': 0,
    }


class CacheWorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.part = os.path.join(self.dir, '1.part')
        with open(self.part, 'wb') as f:
            f.truncate(8)
        self.payload = b'abcdefgh'

    def tearDown(self):
        for name in os.listdir(self.dir):
            try:
                os.remove(os.path.join(self.dir, name))
            except Exception:
                pass
        os.rmdir(self.dir)

    def make_backend(self, client):
        backend = TelegramBackend.__new__(TelegramBackend)
        backend.client = client
        backend._bytes_from_telegram = 0
        return backend

    async def test_worker_downloads_piece_and_finalizes_file(self):
        client = _Client([_Stream([self.payload])])
        backend = self.make_backend(client)
        e = make_entry(self.part)

        queue = e['queue']
        queue.put_nowait((0, 0))

        task = asyncio.create_task(
            backend._cache_worker(_Document(), e, queue, 0, 0.0))
        await asyncio.wait_for(task, 5)

        self.assertEqual(client.calls, 1)
        self.assertEqual(e['piece_done'], {0})
        self.assertEqual(e['tracker'].available_bytes_from(0), 8)
        self.assertTrue(e['complete'])
        self.assertTrue(e['path'].endswith('.mp4'))
        self.assertTrue(os.path.exists(e['path']))
        with open(e['path'], 'rb') as f:
            self.assertEqual(f.read(), self.payload)

        # Читателя для готового файла открывает сама финализация.
        self.assertIsNotNone(e['rfh'])
        e['rfh'].close()

    async def test_floodwait_requeues_without_reconnect(self):
        client = _Client([_FloodStream(), _Stream([self.payload])])
        backend = self.make_backend(client)
        e = make_entry(self.part)

        reconnect_calls = []

        async def reconnect(force=False):
            reconnect_calls.append(force)
            return True

        backend.reconnect = reconnect
        queue = e['queue']
        queue.put_nowait((0, 0))

        import tgplayer.backend.backend as backend_module
        old_error = backend_module.telethon_boot.FloodWaitError
        real_sleep = backend_module.asyncio.sleep

        async def fast_sleep(_delay):
            await real_sleep(0)

        backend_module.telethon_boot.FloodWaitError = _Flood
        backend_module.asyncio.sleep = fast_sleep
        try:
            task = asyncio.create_task(
                backend._cache_worker(_Document(), e, queue, 0, 0.0))
            await asyncio.wait_for(task, 5)
        finally:
            backend_module.telethon_boot.FloodWaitError = old_error
            backend_module.asyncio.sleep = real_sleep

        # FloodWait — не обрыв маршрута: переподключаться не надо, а кусок
        # после ожидания возвращается в очередь и качается заново.
        self.assertEqual(reconnect_calls, [])
        self.assertEqual(client.calls, 2)
        self.assertEqual(e['piece_done'], {0})
        self.assertTrue(e['complete'])
        with open(e['path'], 'rb') as f:
            self.assertEqual(f.read(), self.payload)
        if e.get('rfh'):
            e['rfh'].close()

    async def test_worker_retries_after_transient_error(self):
        client = _Client([_ErrorStream(), _Stream([self.payload])])
        backend = self.make_backend(client)
        e = make_entry(self.part)

        async def reconnect(force=False):
            return True

        backend.reconnect = reconnect
        queue = e['queue']
        queue.put_nowait((0, 0))

        # Первая попытка падает, воркер ждёт перед повтором. Ускоряем
        # ожидание, чтобы тест не спал впустую; на логику это не влияет.
        import tgplayer.backend.backend as backend_module
        real_sleep = backend_module.asyncio.sleep

        async def fast_sleep(_delay):
            await real_sleep(0)

        backend_module.asyncio.sleep = fast_sleep
        try:
            task = asyncio.create_task(
                backend._cache_worker(_Document(), e, queue, 0, 0.0))
            await asyncio.wait_for(task, 5)
        finally:
            backend_module.asyncio.sleep = real_sleep

        self.assertEqual(client.calls, 2)
        self.assertEqual(e['piece_done'], {0})
        self.assertTrue(e['complete'])
        self.assertTrue(e['path'].endswith('.mp4'))
        with open(e['path'], 'rb') as f:
            self.assertEqual(f.read(), self.payload)
        if e.get('rfh'):
            e['rfh'].close()


if __name__ == '__main__':
    unittest.main()
