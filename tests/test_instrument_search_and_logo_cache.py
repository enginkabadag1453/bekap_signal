import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from starlette.background import BackgroundTasks

import app


class LogoResponse:
    def __init__(self, content: bytes, content_type: str):
        self.content = content
        self.headers = LogoHeaders(content_type)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, length=-1):
        return self.content if length < 0 else self.content[:length]


class LogoHeaders:
    def __init__(self, content_type):
        self.content_type = content_type

    def get_content_type(self):
        return self.content_type


class InstrumentSearchAndLogoCacheTests(unittest.TestCase):
    def test_search_returns_only_equities_and_etfs(self):
        payload = {"quotes": [
            {"quoteType": "EQUITY", "symbol": "AAPL", "shortname": "Apple Inc.", "exchDisp": "NASDAQ"},
            {"quoteType": "ETF", "symbol": "SPY", "shortname": "SPDR S&P 500 ETF", "exchDisp": "NYSE Arca"},
            {"quoteType": "MUTUALFUND", "symbol": "VFIAX", "shortname": "Vanguard Fund", "exchDisp": "Nasdaq"},
            {"quoteType": "CRYPTOCURRENCY", "symbol": "BTC-USD", "shortname": "Bitcoin", "exchDisp": "CCC"},
        ]}
        with patch.object(app, "urlopen", return_value=BytesIO(json.dumps(payload).encode("utf-8"))):
            results = app.search_yahoo_instruments("apple")
        self.assertEqual([item["symbol"] for item in results], ["AAPL", "SPY"])
        self.assertEqual([item["kind"] for item in results], ["Hisse", "ETF"])
        self.assertEqual(results[0]["currency"], "USD")

    def test_logo_serves_fallback_immediately_and_caches_png_for_next_request(self):
        png = b"\x89PNG\r\n\x1a\n" + b"test-image"
        with tempfile.TemporaryDirectory() as cache_dir, \
             patch.object(app, "LOGO_CACHE_DIR", Path(cache_dir)), \
             patch.object(app, "urlopen", return_value=LogoResponse(png, "image/png")) as fetch:
            background = BackgroundTasks()
            first = app.instrument_logo("AAPL", background, market="NASDAQ")
            self.assertEqual(first.media_type, "image/svg+xml")
            self.assertEqual(len(background.tasks), 1)
            self.assertEqual(fetch.call_count, 0)

            background.tasks[0].func(*background.tasks[0].args, **background.tasks[0].kwargs)
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual((Path(cache_dir) / "AAPL.png").read_bytes(), png)

            second = app.instrument_logo("AAPL", BackgroundTasks(), market="NASDAQ")
            self.assertEqual(second.media_type, "image/png")
            self.assertEqual(fetch.call_count, 1)


if __name__ == "__main__":
    unittest.main()
