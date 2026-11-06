import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import app


@contextmanager
def test_db():
    con = sqlite3.connect(app.DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
        con.commit()
    finally:
        con.close()


class WeeklyBrakeRegressionTests(unittest.TestCase):
    def setUp(self):
        self.daily = [
            {"date": "2026-01-02", "close": 100.0},
            {"date": "2026-01-05", "close": 90.0},
            {"date": "2026-01-06", "close": 79.0},
            {"date": "2026-01-09", "close": 78.0},
            {"date": "2026-01-12", "close": 80.0},
            {"date": "2026-01-13", "close": 81.0},
            {"date": "2026-01-16", "close": 82.0},
        ]
        self.weekly = [
            {"date": "2026-01-02", "close": 100.0},
            {"date": "2026-01-09", "close": 78.0},
            {"date": "2026-01-16", "close": 82.0},
        ]
        self.daily_rows = [
            {"date": bar["date"], "rsi": 35.0, "buy": False, "tp": False,
             "buy_diff": None, "tp_diff": None}
            for bar in self.daily
        ]
        self.weekly_rows = [
            {"date": bar["date"], "rsi": 35.0, "buy": False, "tp": False,
             "buy_diff": None, "tp_diff": None}
            for bar in self.weekly
        ]

    def test_weekly_buys_activate_at_21pct_and_daily_sat_ends_brake(self):
        self.daily_rows[0]["tp"] = True  # Daily SAT at 100 establishes the reference price.
        self.daily_rows[1]["buy"] = True  # Still daily before the brake.
        self.daily_rows[2]["buy"] = True  # Suppressed at a 21% confirmed-close drawdown.
        self.daily_rows[4]["tp"] = True  # Daily SAT ends the brake.
        self.daily_rows[5]["buy"] = True  # Daily AL resumes after the SAT.
        self.weekly_rows[1]["buy"] = True  # Confirmed weekly AL while brake is active.
        self.weekly_rows[2]["buy"] = True  # Suppressed after the daily SAT ended brake.

        events = app.weekly_brake_events(self.daily, self.daily_rows, self.weekly, self.weekly_rows)
        observed = [(event["event"], event["timeframe"], event["signal_date"]) for event in events]
        self.assertEqual(observed, [
            ("take_profit_confirmed", "1D", "2026-01-02"),
            ("buy_confirmed", "1D", "2026-01-05"),
            ("buy_confirmed", "1W", "2026-01-09"),
            ("take_profit_confirmed", "1D", "2026-01-12"),
            ("buy_confirmed", "1D", "2026-01-13"),
        ])

    def test_weekly_buy_is_not_allowed_above_the_close_threshold(self):
        self.daily[2]["close"] = 79.01
        self.daily[3]["close"] = 79.02
        self.daily_rows[0]["tp"] = True
        self.weekly_rows[1]["buy"] = True

        events = app.weekly_brake_events(self.daily, self.daily_rows, self.weekly, self.weekly_rows)
        self.assertFalse(any(event["event"] == "buy_confirmed" and event["timeframe"] == "1W"
                             for event in events))

    def test_daily_sat_during_brake_ends_it_before_same_week_weekly_buy(self):
        self.daily_rows[0]["tp"] = True
        self.daily[2]["close"] = 79.0
        self.daily_rows[3]["tp"] = True
        self.weekly_rows[1]["buy"] = True

        events = app.weekly_brake_events(self.daily, self.daily_rows, self.weekly, self.weekly_rows)
        self.assertFalse(any(event["event"] == "buy_confirmed" and event["timeframe"] == "1W"
                             for event in events))
        daily_sats = [event for event in events if event["event"] == "take_profit_confirmed"]
        self.assertEqual(daily_sats[-1]["signal_date"], "2026-01-09")
        self.assertTrue(daily_sats[-1]["brake_active"])

    def test_persisted_signals_follow_brake_and_stale_weekly_buy_is_deactivated(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(app, "DB_PATH", Path(tmp) / "signals.sqlite3"), \
                patch.object(app, "db", test_db):
            app.initialize()
            with app.db() as con:
                asset_id = con.execute("SELECT id FROM assets WHERE symbol='AAPL'").fetchone()[0]
                con.executemany(
                    "INSERT INTO bars(asset_id,timeframe,date,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?,?)",
                    [(asset_id, "1D", bar["date"], bar["close"], bar["close"] + 1,
                      bar["close"] - 1, bar["close"], 1000) for bar in self.daily]
                    + [(asset_id, "1W", bar["date"], bar["close"], bar["close"] + 1,
                        bar["close"] - 1, bar["close"], 5000) for bar in self.weekly])

            daily_flags = {"2026-01-02": (False, True), "2026-01-05": (True, False),
                           "2026-01-06": (True, False), "2026-01-12": (False, True),
                           "2026-01-13": (True, False)}
            weekly_flags = {"2026-01-09": (True, False), "2026-01-16": (True, False)}

            def fake_calculate(bars, symbol, market):
                flags = daily_flags if len(bars) > 3 else weekly_flags
                rows = []
                for bar in bars:
                    buy, tp = flags.get(bar["date"], (False, False))
                    rows.append({"date": bar["date"], "rsi": 35.0, "buy": buy, "tp": tp,
                                 "buy_diff": None, "tp_diff": None})
                return {"version": "test-v1", "profile": {"ticker": symbol, "buy": 40,
                        "tp": 65, "overbought": 70, "drop": .025, "rise": .03,
                        "filter": "NONE"}, "rows": rows}

            with patch.object(app.bekap_indicators, "calculate", side_effect=fake_calculate):
                app.run_weekly_brake_signals(asset_id, "2026-01-01", "2026-01-01")
                with app.db() as con:
                    active = [dict(row) for row in con.execute(
                        "SELECT event,timeframe,signal_date,event_key FROM signals WHERE asset_id=? AND is_current=1",
                        (asset_id,))]
                    pending_before = con.execute("SELECT COUNT(*) FROM outbox WHERE status='pending'").fetchone()[0]
                self.assertEqual(pending_before, 5)
                self.assertTrue(any(row["event"] == "buy_confirmed" and row["timeframe"] == "1W"
                                    and row["signal_date"] == "2026-01-09" for row in active))

                self.daily[2]["close"] = 79.01
                self.daily[3]["close"] = 79.02
                with app.db() as con:
                    con.executemany("UPDATE bars SET close=? WHERE asset_id=? AND timeframe='1D' AND date=?",
                                    [(bar["close"], asset_id, bar["date"]) for bar in self.daily])
                app.run_weekly_brake_signals(asset_id, "2026-01-01", "2026-01-01")
                with app.db() as con:
                    old_weekly = con.execute("SELECT is_current,event_key FROM signals WHERE asset_id=? AND event='buy_confirmed' AND timeframe='1W'",
                                             (asset_id,)).fetchone()
                    pending_stale = con.execute("SELECT COUNT(*) FROM outbox WHERE event_key=? AND status='pending'",
                                                (old_weekly["event_key"],)).fetchone()[0]
                self.assertEqual(old_weekly["is_current"], 0)
                self.assertEqual(pending_stale, 0)


if __name__ == "__main__":
    unittest.main()
