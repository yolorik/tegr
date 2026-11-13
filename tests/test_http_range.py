import unittest

from tegr.backend.http_range import parse_range_header


class RangeHeaderTests(unittest.TestCase):
    SIZE = 100

    def check(self, header, expected):
        self.assertEqual(parse_range_header(header, self.SIZE), expected)

    def test_no_header_returns_full_file(self):
        self.check(None, (0, 99, 200))
        self.check('', (0, 99, 200))

    def test_open_ended(self):
        self.check('bytes=10-', (10, 99, 206))

    def test_closed(self):
        self.check('bytes=10-19', (10, 19, 206))

    def test_end_is_clipped_to_file_size(self):
        self.check('bytes=90-200', (90, 99, 206))

    def test_suffix_returns_last_bytes(self):
        self.check('bytes=-10', (90, 99, 206))

    def test_suffix_larger_than_file_returns_all(self):
        self.check('bytes=-200', (0, 99, 206))

    def test_zero_suffix_is_full_file_like_before(self):
        self.check('bytes=-0', (0, 99, 200))

    def test_first_byte(self):
        self.check('bytes=0-0', (0, 0, 206))

    def test_start_past_end_is_unsatisfiable(self):
        self.check('bytes=200-', None)

    def test_reversed_range_is_unsatisfiable(self):
        self.check('bytes=50-10', None)

    def test_invalid_header_falls_back_to_full_file(self):
        self.check('bytes=abc', (0, 99, 200))

    def test_multi_range_uses_first(self):
        self.check('bytes=0-1,4-5', (0, 1, 206))

    def test_case_and_spaces(self):
        self.check(' BYTES = 5-9 ', (0, 99, 200))
        self.check('BYTES=5-9', (5, 9, 206))

    def test_empty_file_is_unsatisfiable(self):
        self.assertIsNone(parse_range_header(None, 0))
        self.assertIsNone(parse_range_header('bytes=-10', 0))


if __name__ == '__main__':
    unittest.main()
