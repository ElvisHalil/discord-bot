import asyncio
import sys
import unittest

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from src.tools.web import search_web


class TestConcurrency(unittest.IsolatedAsyncioTestCase):
    """Test suite verifying asynchronous concurrency for web search tools."""

    async def test_concurrent_searches(self):
        results = await asyncio.gather(
            asyncio.to_thread(search_web, 'apple'),
            asyncio.to_thread(search_web, 'banana')
        )
        self.assertEqual(len(results), 2)
        self.assertIsInstance(results[0], str)
        self.assertIsInstance(results[1], str)


if __name__ == "__main__":
    unittest.main()
