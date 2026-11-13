import os
import tempfile
import unittest

from tegr.backend.cache_math import (
    DownloadTracker, _piece_ranges, index_at_end, piece_index_for)


class DownloadTrackerTests(unittest.TestCase):
    def test_empty_tracker_has_nothing(self):
        tr = DownloadTracker(1000)
        self.assertEqual(tr.available_bytes_from(0), 0)
        self.assertEqual(tr.ranges, [])

    def test_single_range(self):
        tr = DownloadTracker(1000)
        tr.add_range(100, 199)
        self.assertEqual(tr.available_bytes_from(100), 100)
        self.assertEqual(tr.available_bytes_from(150), 50)
        self.assertEqual(tr.available_bytes_from(99), 0)
        self.assertEqual(tr.available_bytes_from(200), 0)

    def test_adjacent_ranges_merge(self):
        tr = DownloadTracker(1000)
        tr.add_range(0, 99)
        tr.add_range(100, 199)
        self.assertEqual(tr.ranges, [(0, 199)])
        self.assertEqual(tr.available_bytes_from(0), 200)

    def test_overlapping_ranges_merge(self):
        tr = DownloadTracker(1000)
        tr.add_range(0, 100)
        tr.add_range(50, 150)
        self.assertEqual(tr.ranges, [(0, 150)])

    def test_out_of_order_ranges_are_sorted(self):
        tr = DownloadTracker(1000)
        tr.add_range(400, 499)
        tr.add_range(100, 199)
        self.assertEqual(tr.ranges, [(100, 199), (400, 499)])

    def test_range_is_clipped_to_size(self):
        tr = DownloadTracker(1000)
        tr.add_range(900, 2000)
        self.assertEqual(tr.ranges, [(900, 999)])
        self.assertEqual(tr.available_bytes_from(900), 100)

    def test_start_past_size_is_ignored(self):
        tr = DownloadTracker(1000)
        tr.add_range(1000, 1100)
        self.assertEqual(tr.ranges, [])

    def test_reversed_range_is_ignored(self):
        tr = DownloadTracker(1000)
        tr.add_range(500, 400)
        self.assertEqual(tr.ranges, [])


class PieceIndexTests(unittest.TestCase):
    PIECES = [(0, 10), (10, 10), (20, 10), (30, 10)]

    def test_first_piece(self):
        self.assertEqual(piece_index_for(self.PIECES, 0), 0)
        self.assertEqual(piece_index_for(self.PIECES, 9), 0)

    def test_boundaries(self):
        self.assertEqual(piece_index_for(self.PIECES, 10), 1)
        self.assertEqual(piece_index_for(self.PIECES, 19), 1)
        self.assertEqual(piece_index_for(self.PIECES, 20), 2)

    def test_last_and_beyond(self):
        self.assertEqual(piece_index_for(self.PIECES, 39), 3)
        self.assertEqual(piece_index_for(self.PIECES, 999), 3)


class PieceRangesTests(unittest.TestCase):
    def test_zero_size_has_no_pieces(self):
        self.assertEqual(_piece_ranges(0), [])

    def test_pieces_cover_file_without_gaps(self):
        for size in (1, 100, 1024 * 1024, 12 * 1024 * 1024,
                     50 * 1024 * 1024 + 123):
            pieces = _piece_ranges(size)
            self.assertTrue(pieces, size)
            pos = 0
            for start, length in pieces:
                self.assertEqual(start, pos, (size, pieces))
                self.assertGreater(length, 0, (size, pieces))
                pos += length
            self.assertEqual(pos, size, (size, pieces))


class IndexAtEndTests(unittest.TestCase):
    @staticmethod
    def box(kind, payload=b''):
        body = kind + payload
        return len(body).to_bytes(4, 'big') + body

    def write(self, data):
        fd, path = tempfile.mkstemp()
        os.write(fd, data)
        os.close(fd)
        self.addCleanup(os.remove, path)
        return path

    def test_moov_before_mdat_needs_no_tail(self):
        data = self.box(b'moov') + self.box(b'mdat', b'x' * 16)
        self.assertFalse(index_at_end(self.write(data), len(data)))

    def test_mdat_before_moov_needs_tail(self):
        data = self.box(b'mdat', b'x' * 16) + self.box(b'moov')
        self.assertTrue(index_at_end(self.write(data), len(data)))

    def test_ebml_needs_tail(self):
        data = b'\x1a\x45\xdf\xa3' + b'\x00' * 8
        self.assertTrue(index_at_end(self.write(data), len(data)))

    def test_short_or_missing_file_is_safe(self):
        self.assertFalse(index_at_end(self.write(b'abc'), 3))
        self.assertFalse(index_at_end('no-such-file', 100))


if __name__ == '__main__':
    unittest.main()
