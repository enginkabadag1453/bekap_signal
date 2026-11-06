import json
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from datetime import date, datetime, timezone
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import app


@contextmanager
def closed_db():
    con = sqlite3.connect(app.DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def ts(value):
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


class MarketDataRegressionTests(unittest.TestCase):
    def test_symbol_mapping_includes_bist_and_crypto(self):
        self.assertEqual(app.yahoo_ticker("THYAO", "BIST"), "THYAO.IS")
        self.assertEqual(app.yahoo_ticker("TUPRS", "BIST"), "TUPRS.IS")
        self.assertEqual(app.yahoo_ticker("LLY", "NYSE"), "LLY")
        self.assertEqual(app.yahoo_ticker("BTC", "CRYPTO"), "BTC-USD")

    def test_hourly_aggregation_uses_last_completed_session_only(self):
        meta = {"exchangeTimezoneName": "America/New_York"}
        as_of = datetime(2026, 11, 6, 1, 30, tzinfo=timezone.utc)
        timestamps = [ts("2026-11-05T20:00:00Z"), ts("2026-11-05T21:00:00Z"), ts("2026-11-06T14:30:00Z")]
        quote = {
            "open": [1400.0, 1433.73, 1360.0],
            "high": [1410.0, 1452.59, 1370.0],
            "low": [1390.0, 1356.0, 1350.0],
            "close": [1405.0, 1364.73, 1365.0],
            "volume": [100.0, 200.0, 50.0],
        }
        bars = app.aggregate_intraday_daily(timestamps, quote, meta, as_of)
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0]["datetime"], "2026-11-05")
        self.assertEqual(bars[0]["open"], 1400.0)
        self.assertEqual(bars[0]["high"], 1452.59)
        self.assertEqual(bars[0]["low"], 1356.0)
        self.assertEqual(bars[0]["close"], 1364.73)
        self.assertEqual(bars[0]["volume"], 300.0)

    def test_stale_daily_feed_is_completed_with_hourly_close(self):
        meta = {"exchangeTimezoneName": "America/New_York", "currency": "USD"}
        daily = {
            "chart": {"result": [{
                "meta": meta,
                "timestamp": [ts("2026-11-04T14:30:00Z")],
                "indicators": {"quote": [{"open": [1440.0], "high": [1445.96], "low": [1401.59],
                                           "close": [1417.99], "volume": [1000]}]},
                "events": {},
            }], "error": None}
        }
        hourly = {
            "chart": {"result": [{
                "meta": meta,
                "timestamp": [ts("2026-11-05T20:00:00Z"), ts("2026-11-05T21:00:00Z"), ts("2026-11-06T14:30:00Z")],
                "indicators": {"quote": [{"open": [1400.0, 1433.73, 1360.0],
                                           "high": [1410.0, 1452.59, 1370.0],
                                           "low": [1390.0, 1356.0, 1350.0],
                                           "close": [1405.0, 1364.73, 1365.0],
                                           "volume": [100.0, 200.0, 50.0]}]},
            }], "error": None}
        }
        def fake_urlopen(request, timeout=0):
            body = hourly if "interval=60m" in request.full_url else daily
            return BytesIO(json.dumps(body).encode())
        with patch.object(app, "urlopen", side_effect=fake_urlopen), \
             patch.object(app, "completed_daily_through", return_value=date(2026, 11, 5)):
            result = app.fetch_yahoo_chart("LLY", "NYSE")
        self.assertEqual(result["values"][-1]["datetime"], "2026-11-05")
        self.assertEqual(result["values"][-1]["close"], 1364.73)
        self.assertTrue(result["intraday_used"])

    def test_hourly_feed_does_not_replace_an_existing_daily_close(self):
        meta = {"exchangeTimezoneName": "America/New_York", "currency": "USD"}
        daily = {"chart": {"result": [{
            "meta": meta, "timestamp": [ts("2026-11-05T14:30:00Z")],
            "indicators": {"quote": [{"open": [38.72], "high": [38.80], "low": [37.33], "close": [37.36], "volume": [973400]}]},
            "events": {},
        }], "error": None}}
        hourly = {"chart": {"result": [{
            "meta": meta, "timestamp": [ts("2026-11-05T14:30:00Z"), ts("2026-11-05T20:30:00Z")],
            "indicators": {"quote": [{"open": [38.72, 37.41], "high": [38.78, 37.44],
                "low": [37.93, 37.32], "close": [37.97, 37.36], "volume": [179191, 112201]}]},
        }], "error": None}}
        def fake_urlopen(request, timeout=0):
            body = hourly if "interval=60m" in request.full_url else daily
            return BytesIO(json.dumps(body).encode())
        with patch.object(app, "urlopen", side_effect=fake_urlopen), \
             patch.object(app, "completed_daily_through", return_value=date(2026, 11, 5)):
            result = app.fetch_yahoo_chart("CPER", "NYSE Arca")
        self.assertEqual(result["values"][-1]["close"], 37.36)
        self.assertEqual(result["values"][-1]["volume"], 973400)
        self.assertFalse(result["intraday_used"])

    def test_legacy_manually_added_asset_gets_provider_record(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(app, "DB_PATH", Path(tmp) / "test.sqlite3"), patch.object(app, "db", closed_db):
            app.initialize()
            with app.db() as con:
                con.execute("""INSERT INTO assets(symbol,market,role,currency,target_weight,max_weight,tv_symbol,parameters_json,created_at)
                    VALUES('THYAO','BIST','lifetime','TRY',0,0,'BIST:THYAO','{}','2026-11-06T00:00:00Z')""")
            app.initialize()
            with app.db() as con:
                row = con.execute("""SELECT s.provider_symbol FROM market_data_state s
                    JOIN assets a ON a.id=s.asset_id WHERE a.symbol='THYAO'""").fetchone()
            self.assertEqual(row["provider_symbol"], "THYAO.IS")

    def test_historical_backfill_does_not_enqueue_telegram_notifications(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(app, "DB_PATH", Path(tmp) / "signals.sqlite3"), patch.object(app, "db", closed_db):
            app.initialize()
            with app.db() as con:
                asset_id = con.execute("SELECT id FROM assets WHERE symbol='LLY'").fetchone()[0]
                for i in range(21):
                    day = f"2026-10-{i+1:02d}"
                    close = 100.0 + i
                    con.execute("INSERT INTO bars(asset_id,timeframe,date,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?,?)",
                                (asset_id, "1D", day, close, close + 1, close - 1, close, 1000))
            profile = {"ticker": "LLY", "buy": 40, "tp": 65, "overbought": 70,
                       "drop": 0.1, "rise": 0.2, "filter": "NONE"}
            states = [{"buy": i in (10, 19), "tp": False, "rsi": 30.0} for i in range(21)]
            calculation = {"version": "test-v1", "profile": profile, "rows": states}
            with patch.object(app.bekap_indicators, "calculate", return_value=calculation):
                result = app.run_signals(asset_id, "1D", notify_after="2026-10-15")
            with app.db() as con:
                queued = con.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
                dates = [row[0] for row in con.execute("SELECT signal_date FROM signals ORDER BY signal_date")]
            self.assertEqual(result["created"], 2)
            self.assertEqual(dates, ["2026-10-11", "2026-10-20"])
            self.assertEqual(queued, 1)

    def test_chart_payload_latest_close_and_sma_match_bars(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(app, "DB_PATH", Path(tmp) / "chart.sqlite3"), patch.object(app, "db", closed_db):
            app.initialize()
            with app.db() as con:
                asset_id = con.execute("SELECT id FROM assets WHERE symbol='LLY'").fetchone()[0]
                bars = []
                for i in range(205):
                    close = 100.0 + i
                    bars.append((asset_id, "1D", f"2026-{i//28+1:02d}-{i%28+1:02d}", close, close+2, close-2, close+1, 1000.0))
                con.executemany("INSERT INTO bars(asset_id,timeframe,date,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?,?)", bars)
            payload = app.build_chart_payload(asset_id, "D")
            self.assertEqual(payload["bars"][-1]["close"], 305.0)
            self.assertEqual(payload["indicators"][-1]["sma200"], 205.5)
            self.assertEqual(payload["indicators"][-1]["date"], payload["bars"][-1]["date"])

    def test_weekly_chart_shows_current_partial_week_without_confirming_signals(self):
        class FrozenDate(date):
            @classmethod
            def today(cls):
                return cls(2026, 11, 5)

        with tempfile.TemporaryDirectory() as tmp, patch.object(app, "DB_PATH", Path(tmp) / "weekly.sqlite3"), patch.object(app, "db", closed_db):
            app.initialize()
            with app.db() as con:
                asset_id = con.execute("SELECT id FROM assets WHERE symbol='CPER'").fetchone()[0]
                dates = ["2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30",
                         "2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05"]
                con.executemany("INSERT INTO bars(asset_id,timeframe,date,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?,?)",
                    [(asset_id, "1D", d, 40, 41, 37, 38, 1000) for d in dates])
            def fake_calculate(bars, symbol, market):
                rows = [{"date": b["date"], "rsi": 43.54, "adx": 24.75, "buy": True, "tp": True,
                         "cross_up": True, "cross_down": True} for b in bars]
                return {"version": "test-v1", "profile": {}, "rows": rows}
            with patch.object(app, "date", FrozenDate), patch.object(app.bekap_indicators, "calculate", side_effect=fake_calculate):
                payload = app.build_chart_payload(asset_id, "W")
            self.assertEqual(payload["bars"][-1]["date"], "2026-11-02")
            self.assertTrue(payload["current_bar_provisional"])
            self.assertEqual(payload["last_confirmed_bar_date"], "2026-10-30")
            self.assertEqual(payload["indicators"][-1]["rsi"], 43.54)
            self.assertEqual(payload["indicators"][-1]["adx"], 24.75)
            self.assertFalse(any(payload["indicators"][-1][key] for key in ("buy", "tp", "cross_up", "cross_down")))


if __name__ == "__main__":
    unittest.main()
