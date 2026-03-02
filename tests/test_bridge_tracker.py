"""Tests for the AI Bridge token tracker."""

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

# Add bridge directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bridge"))

from tracker import TokenTracker


def run_async(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


class TestTokenTracker(unittest.TestCase):
    """Tests for TokenTracker SQLite persistence."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = Path(self.tmp) / "test_tokens.db"
        self.tracker = TokenTracker(self.db_path)
        run_async(self.tracker.initialize())

    def tearDown(self):
        run_async(self.tracker.close())

    def test_initialize_creates_db(self):
        """Database file is created on initialize."""
        self.assertTrue(self.db_path.exists())

    def test_record_and_recent(self):
        """Recorded usage appears in recent results."""
        run_async(self.tracker.record(
            caller_id="grim",
            model="claude-sonnet-4-6",
            input_tokens=1000,
            output_tokens=500,
        ))
        results = run_async(self.tracker.recent(limit=10))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["caller_id"], "grim")
        self.assertEqual(results[0]["model"], "claude-sonnet-4-6")
        self.assertEqual(results[0]["input_tokens"], 1000)
        self.assertEqual(results[0]["output_tokens"], 500)
        self.assertEqual(results[0]["total_tokens"], 1500)

    def test_record_with_cache_tokens(self):
        """Cache read/create tokens are recorded."""
        run_async(self.tracker.record(
            caller_id="grim",
            model="claude-sonnet-4-6",
            input_tokens=2000,
            output_tokens=800,
            cache_read=500,
            cache_create=200,
        ))
        results = run_async(self.tracker.recent(limit=1))
        self.assertEqual(results[0]["cache_read"], 500)
        self.assertEqual(results[0]["cache_create"], 200)

    def test_multiple_records(self):
        """Multiple records are stored and retrievable."""
        for i in range(5):
            run_async(self.tracker.record(
                caller_id="grim",
                model="claude-sonnet-4-6",
                input_tokens=100 * (i + 1),
                output_tokens=50 * (i + 1),
            ))
        results = run_async(self.tracker.recent(limit=10))
        self.assertEqual(len(results), 5)
        # Recent returns newest first
        self.assertEqual(results[0]["input_tokens"], 500)

    def test_recent_limit(self):
        """Recent respects the limit parameter."""
        for i in range(10):
            run_async(self.tracker.record(
                caller_id="grim", model="test", input_tokens=100, output_tokens=50,
            ))
        results = run_async(self.tracker.recent(limit=3))
        self.assertEqual(len(results), 3)

    def test_recent_max_limit(self):
        """Recent caps at 500."""
        results = run_async(self.tracker.recent(limit=9999))
        # Should not error, just returns empty (no records)
        self.assertEqual(len(results), 0)

    def test_summary_totals(self):
        """Summary aggregates totals correctly."""
        run_async(self.tracker.record("grim", "claude-sonnet-4-6", 1000, 500))
        run_async(self.tracker.record("grim", "claude-sonnet-4-6", 2000, 800))
        run_async(self.tracker.record("ironclaw", "claude-sonnet-4-6", 500, 200))

        summary = run_async(self.tracker.summary(days=30))
        self.assertEqual(summary["totals"]["input_tokens"], 3500)
        self.assertEqual(summary["totals"]["output_tokens"], 1500)
        self.assertEqual(summary["totals"]["total_tokens"], 5000)
        self.assertEqual(summary["totals"]["calls"], 3)

    def test_summary_by_caller(self):
        """Summary groups by caller correctly."""
        run_async(self.tracker.record("grim", "test", 1000, 500))
        run_async(self.tracker.record("grim", "test", 2000, 800))
        run_async(self.tracker.record("ironclaw", "test", 500, 200))

        summary = run_async(self.tracker.summary(days=30))
        self.assertIn("grim", summary["by_caller"])
        self.assertIn("ironclaw", summary["by_caller"])
        self.assertEqual(summary["by_caller"]["grim"]["calls"], 2)
        self.assertEqual(summary["by_caller"]["grim"]["input_tokens"], 3000)
        self.assertEqual(summary["by_caller"]["ironclaw"]["calls"], 1)

    def test_summary_by_model(self):
        """Summary groups by model correctly."""
        run_async(self.tracker.record("grim", "claude-sonnet-4-6", 1000, 500))
        run_async(self.tracker.record("grim", "claude-opus-4-6", 2000, 800))

        summary = run_async(self.tracker.summary(days=30))
        self.assertIn("claude-sonnet-4-6", summary["by_model"])
        self.assertIn("claude-opus-4-6", summary["by_model"])

    def test_by_day_returns_daily_aggregates(self):
        """by_day returns daily grouped data."""
        run_async(self.tracker.record("grim", "test", 1000, 500))
        run_async(self.tracker.record("grim", "test", 2000, 800))

        days = run_async(self.tracker.by_day(days=30))
        self.assertGreaterEqual(len(days), 1)
        # Both records are today
        self.assertEqual(days[0]["calls"], 2)
        self.assertEqual(days[0]["input_tokens"], 3000)

    def test_by_day_filters_by_caller(self):
        """by_day filters by caller_id."""
        run_async(self.tracker.record("grim", "test", 1000, 500))
        run_async(self.tracker.record("ironclaw", "test", 2000, 800))

        grim_days = run_async(self.tracker.by_day(days=30, caller_id="grim"))
        self.assertEqual(len(grim_days), 1)
        self.assertEqual(grim_days[0]["input_tokens"], 1000)

    def test_empty_db_queries(self):
        """Queries on empty database return sensible defaults."""
        summary = run_async(self.tracker.summary())
        self.assertEqual(summary["totals"]["calls"], 0)
        self.assertEqual(summary["totals"]["total_tokens"], 0)
        self.assertEqual(summary["by_caller"], {})

        days = run_async(self.tracker.by_day())
        self.assertEqual(days, [])

        recent = run_async(self.tracker.recent())
        self.assertEqual(recent, [])

    def test_record_failure_does_not_raise(self):
        """Record is fire-and-forget — DB errors don't propagate."""
        # Close the connection to force an error
        run_async(self.tracker.close())
        # Should not raise
        run_async(self.tracker.record("grim", "test", 100, 50))


if __name__ == "__main__":
    unittest.main()
