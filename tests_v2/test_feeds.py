"""Read-only allowlisted feeds, content-addressed cache, empirical validation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sask_v2.feeds import EmpiricalValidator, FeedError, FeedRegistry, load_csv

CSV_BYTES = b"x,y\n1,2\n3,4\n"
GOOD_URL = "https://raw.githubusercontent.com/example/datasets/main/sample.csv"


class FeedRegistryTest(unittest.TestCase):
    def test_allowlist_and_scheme_enforced(self):
        with tempfile.TemporaryDirectory() as raw:
            registry = FeedRegistry(raw, transport=lambda url, timeout: CSV_BYTES)
            with self.assertRaises(FeedError):
                registry.fetch("http://raw.githubusercontent.com/x/y.csv")
            with self.assertRaises(FeedError):
                registry.fetch("https://evil.example/x.csv")

    def test_fetch_caches_by_content_hash(self):
        calls = []

        def transport(url, timeout):
            calls.append(url)
            return CSV_BYTES

        with tempfile.TemporaryDirectory() as raw:
            registry = FeedRegistry(raw, transport=transport)
            first = registry.fetch(GOOD_URL)
            self.assertFalse(first["cached"])
            self.assertEqual(first["bytes"], len(CSV_BYTES))
            cached_file = Path(first["path"])
            self.assertTrue(cached_file.is_file())
            self.assertEqual(cached_file.read_bytes(), CSV_BYTES)

            second = registry.fetch(GOOD_URL)
            self.assertTrue(second["cached"])
            self.assertEqual(second["sha256"], first["sha256"])
            self.assertEqual(len(calls), 1)

    def test_size_cap_enforced(self):
        with tempfile.TemporaryDirectory() as raw:
            registry = FeedRegistry(
                raw, transport=lambda url, timeout: b"a" * 100, max_bytes=10
            )
            with self.assertRaises(FeedError):
                registry.fetch(GOOD_URL)

    def test_non_bytes_transport_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            registry = FeedRegistry(raw, transport=lambda url, timeout: "text")
            with self.assertRaises(FeedError):
                registry.fetch(GOOD_URL)

    def test_ingest_csv_parses_numbers(self):
        with tempfile.TemporaryDirectory() as raw:
            registry = FeedRegistry(raw, transport=lambda url, timeout: CSV_BYTES)
            dataset = registry.ingest_csv("sample", GOOD_URL)
            self.assertEqual(len(dataset.rows), 2)
            self.assertEqual(dataset.rows[0]["x"], 1.0)
            self.assertEqual(dataset.rows[1]["y"], 4.0)
            self.assertEqual(len(dataset.sha256), 64)
            self.assertEqual(len(load_csv(dataset.cached_path)), 2)


class EmpiricalValidatorTest(unittest.TestCase):
    rows = [{"x": 1.0, "y": 2.0}, {"x": 2.0, "y": 4.0}]

    def test_matching_hypothesis_passes(self):
        result = EmpiricalValidator.validate(lambda row: 2 * row["x"], self.rows, "y")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["rows"], 2)
        self.assertEqual(result["max_error"], 0.0)

    def test_mismatched_hypothesis_reports_failures(self):
        result = EmpiricalValidator.validate(lambda row: row["x"], self.rows, "y")
        self.assertFalse(result["ok"])
        self.assertTrue(result["failures"])

    def test_missing_target_column(self):
        result = EmpiricalValidator.validate(lambda row: 0.0, self.rows, "z")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "no_comparable_rows")


if __name__ == "__main__":
    unittest.main()
