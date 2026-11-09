import asyncio
import time
import unittest

from tgplayer.backend.backend import TelegramBackend


def make_entry(gen=1):
    return {
        'queue': asyncio.PriorityQueue(),
        'complete': False,
        'size': 100,
        'pieces': [(i * 10, 10) for i in range(10)],
        'piece_count': 10,
        'piece_done': set(),
        'active': set(),
        'queued': set(),
        'boosted': set(),
        'boost_gen': gen,
        'read_gen': gen,
        'wanted': {},
    }


class BoostStaleTests(unittest.TestCase):
    def setUp(self):
        self.backend = TelegramBackend.__new__(TelegramBackend)

    def test_current_generation_is_not_stale(self):
        e = make_entry(gen=1)
        self.assertFalse(self.backend._boost_stale(e, 1, 0))

    def test_old_generation_without_reader_is_stale(self):
        e = make_entry(gen=2)
        self.assertTrue(self.backend._boost_stale(e, 1, 0))

    def test_old_generation_still_wanted_is_not_stale(self):
        e = make_entry(gen=2)
        e['wanted'][0] = time.monotonic()
        self.assertFalse(self.backend._boost_stale(e, 1, 0))

    def test_normal_entry_is_never_stale(self):
        e = make_entry(gen=2)
        self.assertFalse(self.backend._boost_stale(e, None, 0))


class PrioritizeTests(unittest.TestCase):
    def setUp(self):
        self.backend = TelegramBackend.__new__(TelegramBackend)

    @staticmethod
    def drain(entry):
        items = []
        while not entry['queue'].empty():
            items.append(entry['queue'].get_nowait())
        return items

    def test_boost_entries_carry_generation(self):
        e = make_entry(gen=1)
        self.backend.prioritize_position(e, 25)  # кусок 2
        items = self.drain(e)
        self.assertTrue(items)
        self.assertTrue(all(len(item) == 3 for item in items))
        self.assertTrue(all(item[2] == 1 for item in items))

    def test_newer_boost_outranks_old(self):
        e = make_entry(gen=1)
        self.backend.prioritize_position(e, 25)
        old = self.drain(e)
        e['read_gen'] = 2
        self.backend.prioritize_position(e, 55)
        new = self.drain(e)
        self.assertTrue(old and new)
        self.assertLess(min(item[0] for item in new),
                        min(item[0] for item in old))

    def test_same_generation_does_not_duplicate_boost(self):
        e = make_entry(gen=1)
        self.backend.prioritize_position(e, 25)
        self.backend.prioritize_position(e, 25)
        items = self.drain(e)
        self.assertEqual(len(items), len(set(item[1] for item in items)))


if __name__ == '__main__':
    unittest.main()
