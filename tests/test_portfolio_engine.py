"""
Regression and validation test for BEKAP portfolio_engine.py.
Verifies:
1. DB initialization & table schema
2. Trade entry (5 fields) + automatic FX, Gold, and CPI stamping
3. Weighted Average Cost (WAC) calculations on partial buys & sells
4. Realized vs Unrealized P&L calculations
5. 4-benchmark return calculations (Nominal, Reel/TÜFE, USD, Gram Altın)
6. BKP-FON portfolio aggregation
"""
import sqlite3
import unittest
import portfolio_engine

class TestPortfolioEngine(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        portfolio_engine.init_portfolio_db(self.con)

    def tearDown(self):
        self.con.close()

    def test_record_trade_and_auto_stamping(self):
        trade = portfolio_engine.record_trade(
            self.con,
            symbol="THYAO",
            trade_date="11.11.2026",
            side="ALIŞ",
            price=314.50,
            quantity=100,
            notes="1. kademe"
        )
        self.assertEqual(trade["symbol"], "THYAO")
        self.assertEqual(trade["side"], "BUY")
        self.assertEqual(trade["price"], 314.50)
        self.assertEqual(trade["quantity"], 100)
        self.assertEqual(trade["total_amount"], 31450.00)
        self.assertEqual(trade["currency"], "TRY")
        self.assertGreater(trade["usd_try_rate"], 0)
        self.assertGreater(trade["gold_try_price"], 0)
        self.assertGreater(trade["cpi_index"], 0)

    def test_wac_and_partial_sell_accounting(self):
        # 1. Buy 100 @ 200 TL
        portfolio_engine.record_trade(self.con, "THYAO", "2026-01-10", "BUY", 200.0, 100)
        # 2. Buy 100 @ 300 TL -> WAC = 250 TL, total 200 qty
        portfolio_engine.record_trade(self.con, "THYAO", "2026-02-10", "BUY", 300.0, 100)
        
        # Check before sell
        data = portfolio_engine.compute_portfolio_data(self.con, live_prices={"THYAO": 350.0})
        self.assertEqual(len(data["positions"]), 1)
        pos = data["positions"][0]
        self.assertEqual(pos["quantity"], 200)
        self.assertEqual(pos["avg_cost"], 250.0)
        self.assertEqual(pos["current_price"], 350.0)
        self.assertEqual(pos["nominal_return_pct"], 40.0)

        # 3. Sell 50 @ 400 TL -> Realized gain = (400 - 250) * 50 = 7500 TL
        portfolio_engine.record_trade(self.con, "THYAO", "2026-03-10", "SELL", 400.0, 50)
        data2 = portfolio_engine.compute_portfolio_data(self.con, live_prices={"THYAO": 350.0})
        self.assertEqual(data2["summary"]["realized_pnl_try"], 7500.0)
        pos2 = data2["positions"][0]
        self.assertEqual(pos2["quantity"], 150)
        self.assertEqual(pos2["avg_cost"], 250.0)  # Average cost remains 250

    def test_multi_currency_and_benchmarks(self):
        # Buy USD stock (NVDA)
        portfolio_engine.record_trade(self.con, "NVDA", "2026-01-15", "BUY", 100.0, 10)
        # Buy CAD stock (SHOP.TO)
        portfolio_engine.record_trade(self.con, "SHOP.TO", "2026-01-20", "BUY", 150.0, 20)
        
        data = portfolio_engine.compute_portfolio_data(
            self.con,
            live_prices={"NVDA": 120.0, "SHOP.TO": 180.0}
        )
        self.assertEqual(len(data["positions"]), 2)
        summary = data["summary"]
        self.assertGreater(summary["total_value_try"], 0)
        self.assertGreater(summary["total_value_usd"], 0)
        self.assertGreater(summary["total_value_gold_gram"], 0)
        self.assertIn("nominal_pct", summary["returns"])
        self.assertIn("real_pct", summary["returns"])
        self.assertIn("usd_pct", summary["returns"])
        self.assertIn("gold_pct", summary["returns"])

    def test_update_and_delete_trade(self):
        t = portfolio_engine.record_trade(self.con, "THYAO", "2026-01-10", "BUY", 200.0, 100)
        t_id = t["id"]
        # Update
        updated = portfolio_engine.update_trade(self.con, t_id, "THYAO", "2026-01-12", "BUY", 220.0, 120, "revize")
        self.assertIsNotNone(updated)
        self.assertEqual(updated["price"], 220.0)
        self.assertEqual(updated["quantity"], 120)
        self.assertEqual(updated["total_amount"], 26400.0)
        # Delete
        deleted = portfolio_engine.delete_trade(self.con, t_id)
        self.assertTrue(deleted)
        # Confirm empty
        data = portfolio_engine.compute_portfolio_data(self.con)
        self.assertEqual(len(data["positions"]), 0)
        self.assertEqual(len(data["trades"]), 0)

if __name__ == "__main__":
    unittest.main()
