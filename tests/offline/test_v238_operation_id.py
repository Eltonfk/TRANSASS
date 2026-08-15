import re
import unittest

from v238_base_materializer import new_operation_id


class OperationIdFormatTests(unittest.TestCase):
    def test_future_operation_id_has_one_utc_timestamp_and_is_unique(self):
        first = new_operation_id("R6B_TEST")
        second = new_operation_id("R6B_TEST")
        pattern = re.compile(r"^R6B_TEST_\d{8}T\d{6}Z$")
        self.assertRegex(first, pattern)
        self.assertRegex(second, pattern)
        self.assertNotEqual(first, second)
        timestamp = first.rsplit("_", 1)[-1]
        self.assertEqual(timestamp.count("T"), 1)
        self.assertTrue(timestamp.endswith("Z"))
