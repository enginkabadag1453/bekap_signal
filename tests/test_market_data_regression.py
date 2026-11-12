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

    def test_intraday_aggregation_can_include_only_current_partial_session(self):
        meta = {"exchangeTimezoneName": "America/New_York"}
        as_of = datetime(2026, 11, 6, 15, 30, tzinfo=timezone.utc)
        timestamps = [ts("2026-11-05T20:00:00Z"), ts("2026-11-06T14:30:00Z"), ts("2026-11-06T15:15:00Z")]
        quote = {"open": [10, 11, 12], "high": [11, 13, 14], "low": [9, 10, 11],
                 "close": [10.5, 12, 13.5], "volume": [100, 200, 300]}
        completed = app.aggregate_intraday_daily(timestamps, quote, meta, as_of)
        with_live = app.aggregate_intraday_daily(timestamps, quote, meta, as_of, include_current=True)
        self.assertEqual([bar["datetime"] for bar in completed], ["2026-11-05"])
        self.assertEqual([bar["datetime"] for bar in with_live], ["2026-11-05", "2026-11-06"])
        self.assertEqual(with_live[-1], {"datetime": "2026-11-06", "open": 11.0, "high": 14.0,
                                         "low": 10.0, "close": 13.5, "volume": 500.0})

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
        requests = []
        def fake_urlopen(request, timeout=0):
            requests.append(request.full_url)
            body = hourly if "interval=60m" in request.full_url else daily
            return BytesIO(json.dumps(body).encode())
        with patch.object(app, "urlopen", side_effect=fake_urlopen), \
             patch.object(app, "completed_daily_through", return_value=date(2026, 11, 5)):
            result = app.fetch_yahoo_chart("LLY", "NYSE")
        self.assertEqual(result["values"][-1]["datetime"], "2026-11-05")
        self.assertEqual(result["values"][-1]["close"], 1364.73)
        self.assertTrue(result["intraday_used"])
        self.assertIn("range=10y&interval=1d", requests[0])

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
            expected_ema = app.bekap_indicators.ema([101.0 + i for i in range(205)], 200)[-1]
            self.assertAlmostEqual(payload["indicators"][-1]["ema200"], expected_ema)
            self.assertEqual(payload["indicators"][-1]["date"], payload["bars"][-1]["date"])

    def test_live_partial_bar_updates_chart_but_is_not_confirmed_as_signal(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(app, "DB_PATH", Path(tmp) / "live-chart.sqlite3"), patch.object(app, "db", closed_db):
            app.initialize()
            with app.db() as con:
                asset_id = con.execute("SELECT id FROM assets WHERE symbol='AAPL'").fetchone()[0]
                bars = []
                for i in range(205):
                    close = 100.0 + i
                    bars.append((asset_id, "1D", f"2026-{i//28+1:02d}-{i%28+1:02d}", close, close+2, close-2, close+1, 1000.0))
                con.executemany("INSERT INTO bars(asset_id,timeframe,date,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?,?)", bars)
                con.execute("""INSERT INTO intraday_bars(asset_id,date,open,high,low,close,volume,updated_at)
                    VALUES(?,?,?,?,?,?,?,?)""", (asset_id, "2026-11-10", 306, 317, 305, 316, 50000, "2026-11-10T10:00:00Z"))
            with patch.object(app, "weekly_brake_events", return_value=[]) as events:
                payload = app.build_chart_payload(asset_id, "D")
            self.assertEqual(payload["bars"][-1]["date"], "2026-11-10")
            self.assertEqual(payload["bars"][-1]["close"], 316)
            self.assertTrue(payload["bars"][-1]["provisional"])
            self.assertTrue(payload["current_daily_provisional"])
            self.assertFalse(any(payload["indicators"][-1][key] for key in ("buy", "tp", "cross_up", "cross_down")))
            self.assertEqual(payload["last_confirmed_daily_bar_date"], bars[-1][2])
            self.assertEqual(events.call_args.args[0][-1]["date"], bars[-1][2])
            weekly = app.build_chart_payload(asset_id, "W")
            self.assertEqual(weekly["bars"][-1]["date"], "2026-11-09")
            self.assertEqual(weekly["bars"][-1]["close"], 316)
            self.assertTrue(weekly["current_bar_provisional"])
            self.assertFalse(any(weekly["indicators"][-1][key] for key in ("buy", "tp", "cross_up", "cross_down")))

    def test_intraday_refresh_only_updates_display_candles(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(app, "DB_PATH", Path(tmp) / "live-refresh.sqlite3"), patch.object(app, "db", closed_db):
            app.initialize()
            live = {"datetime": "2026-11-10", "open": 100, "high": 102, "low": 99,
                    "close": 101, "volume": 500}
            with patch.object(app, "fetch_yahoo_current_bar", return_value=live):
                self.assertEqual(app.sync_intraday_bars(), 7)
            with app.db() as con:
                candles = con.execute("SELECT COUNT(*) FROM intraday_bars").fetchone()[0]
                confirmed_bars = con.execute("SELECT COUNT(*) FROM bars").fetchone()[0]
                signals = con.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
            self.assertEqual(candles, 7)
            self.assertEqual(confirmed_bars, 0)
            self.assertEqual(signals, 0)

    def test_chart_draws_pine_ema200_instead_of_sma200(self):
        chart_js = (app.ROOT / "static" / "chart.js").read_text(encoding="utf-8")
        index_html = (app.ROOT / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn("addLine(chart, rows, 'ema200'", chart_js)
        self.assertNotIn("addLine(chart, rows, 'sma200'", chart_js)
        self.assertIn('<span>EMA 200</span>', index_html)
        self.assertNotIn('<span>SMA 200</span>', index_html)
        self.assertIn("data.chart_signals || []", chart_js)
        self.assertIn("data.strategy_signals || []", chart_js)
        self.assertIn("signal-badge-below", chart_js)

    def test_chart_bubbles_use_native_indicator_events_for_selected_timeframe(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(app, "DB_PATH", Path(tmp) / "signal-timeframe.sqlite3"), patch.object(app, "db", closed_db):
            app.initialize()
            with app.db() as con:
                asset_id = con.execute("SELECT id FROM assets WHERE symbol='TUPRS'").fetchone()[0]
                days = ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06"]
                con.executemany("INSERT INTO bars(asset_id,timeframe,date,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?,?)",
                    [(asset_id, "1D", day, 100, 102, 98, 100, 1000) for day in days])
            events = [
                {"event": "buy_confirmed", "timeframe": "1D", "signal_date": "2026-11-03", "close_price": 100, "change_pct": -4.0},
                {"event": "take_profit_confirmed", "timeframe": "1D", "signal_date": "2026-11-04", "close_price": 100, "change_pct": 5.0},
                {"event": "buy_confirmed", "timeframe": "1W", "signal_date": "2026-11-06", "close_price": 100, "change_pct": -6.63},
            ]
            def fake_calculate(bars, symbol, market):
                rows = []
                for bar in bars:
                    day = str(bar["date"])
                    weekly_bar = len(bars) == 1
                    rows.append({"date": day, "rsi": 35.0, "adx": 20.0,
                                 "buy": day == "2026-11-03" or weekly_bar and day == "2026-11-02",
                                 "tp": day == "2026-11-04" or weekly_bar and day == "2026-11-02",
                                 "buy_diff": -6.0, "tp_diff": 10.0,
                                 "last_buy": 100, "last_tp": 100, "cross_up": False, "cross_down": False})
                return {"version": "test-v1", "profile": {}, "rows": rows}
            with patch.object(app, "weekly_brake_events", return_value=events), patch.object(app.bekap_indicators, "calculate", side_effect=fake_calculate):
                daily = app.build_chart_payload(asset_id, "D")
                weekly = app.build_chart_payload(asset_id, "W")
            self.assertEqual([event["timeframe"] for event in daily["strategy_signals"]], ["1D", "1D"])
            self.assertEqual([event["timeframe"] for event in weekly["strategy_signals"]], ["1W"])
            self.assertEqual(weekly["strategy_signals"][0]["chart_date"], "2026-11-02")
            self.assertEqual([(event["timeframe"], event["signal_date"], event["event"]) for event in daily["chart_signals"]],
                             [("1D", "2026-11-03", "buy_confirmed"), ("1D", "2026-11-04", "take_profit_confirmed")])
            self.assertEqual([(event["timeframe"], event["signal_date"], event["event"]) for event in weekly["chart_signals"]],
                             [("1W", "2026-11-02", "buy_confirmed"), ("1W", "2026-11-02", "take_profit_confirmed")])

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
