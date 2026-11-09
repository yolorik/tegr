import unittest
from types import SimpleNamespace

from tgplayer.backend.backend import TelegramBackend


class _Client:
    def __init__(self, result=None, errors=1):
        self.result = result
        self.errors = errors
        self.calls = 0

    async def get_messages(self, peer_id, ids):
        self.calls += 1
        if self.calls <= self.errors:
            raise ConnectionError('network down')
        return self.result


def make_backend(client, reconnect_result=True):
    backend = TelegramBackend.__new__(TelegramBackend)
    backend.client = client

    async def reconnect(force=False):
        return reconnect_result

    backend.reconnect = reconnect
    return backend


def make_entry(doc_id=7):
    document = SimpleNamespace(id=doc_id, file_reference=b'old')
    message = SimpleNamespace(peer_id=1, id=2)
    return {'message': message, 'document': document}


class RefreshDocumentTests(unittest.IsolatedAsyncioTestCase):
    async def test_transient_error_retries_after_reconnect(self):
        fresh_doc = SimpleNamespace(id=7, file_reference=b'new')
        fresh = SimpleNamespace(media=SimpleNamespace(document=fresh_doc))
        client = _Client(result=fresh, errors=1)
        backend = make_backend(client)
        entry = make_entry()
        result = await backend._refresh_document(entry, 7)
        self.assertTrue(result)
        self.assertEqual(client.calls, 2)
        self.assertEqual(entry['document'], fresh_doc)
        self.assertEqual(entry['message'], fresh)

    async def test_network_failure_is_not_fatal(self):
        client = _Client(errors=99)
        backend = make_backend(client, reconnect_result=False)
        result = await backend._refresh_document(make_entry(), 7)
        self.assertIsNone(result)

    async def test_missing_media_is_fatal(self):
        client = _Client(result=SimpleNamespace(media=None), errors=0)
        backend = make_backend(client)
        result = await backend._refresh_document(make_entry(), 7)
        self.assertFalse(result)

    async def test_changed_document_is_fatal(self):
        fresh_doc = SimpleNamespace(id=8, file_reference=b'new')
        fresh = SimpleNamespace(media=SimpleNamespace(document=fresh_doc))
        client = _Client(result=fresh, errors=0)
        backend = make_backend(client)
        result = await backend._refresh_document(make_entry(), 7)
        self.assertFalse(result)


if __name__ == '__main__':
    unittest.main()
