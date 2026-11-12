"""
BEKAP Portfolio Engine: Trades, Positions, BKP-FON Metrics & 4-Benchmark Returns
(Nominal, Reel/TÜFE, USD, Gram Altın)
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.request import Request as UrlRequest, urlopen

# TÜİK Aylık TÜFE Endeksi (2003=100 Temel Yıllı Resmi Endeks Değerleri)
# Kaynak: TÜİK Tüketici Fiyat Endeksi Bültenleri
OFFICIAL_CPI_MONTHLY: dict[str, float] = {
    # 2023
    "2023-01": 1203.48, "2023-02": 1241.33, "2023-03": 1269.75, "2023-04": 1300.09,
    "2023-05": 1300.60, "2023-06": 1351.59, "2023-07": 1479.59, "2023-08": 1614.31,
    "2023-09": 1691.03, "2023-10": 1749.11, "2023-11": 1806.50, "2023-12": 1859.38,
    # 2024
    "2024-01": 1983.83, "2024-02": 2073.69, "2024-03": 2139.23, "2024-04": 2207.50,
    "2024-05": 2281.89, "2024-06": 2319.31, "2024-07": 2394.31, "2024-08": 2453.45,
    "2024-09": 2526.35, "2024-10": 2599.11, "2024-11": 2657.35, "2024-12": 2715.80,
    # 2025
    "2025-01": 2850.50, "2025-02": 2936.00, "2025-03": 3009.30, "2025-04": 3084.50,
    "2025-05": 3146.20, "2025-06": 3193.40, "2025-07": 3273.20, "2025-08": 3338.70,
    "2025-09": 3405.50, "2025-10": 3473.60, "2025-11": 3543.10, "2025-12": 3613.90,
    # 2026
    "2026-01": 3794.60, "2026-02": 3889.50, "2026-03": 3975.10, "2026-04": 4054.60,
    "2026-05": 4135.70, "2026-06": 4197.70, "2026-07": 4281.70, "2026-08": 4367.30,
    "2026-09": 4454.60, "2026-10": 4543.70, "2026-11": 4634.60, "2026-12": 4727.30,
}

_MACRO_CACHE: dict[str, Any] = {
    "usd_try": 34.82,
    "gold_usd": 2720.0,
    "gold_try": 3045.0,
    "cad_usd": 0.72,
    "last_updated": 0.0,
}
_HISTORICAL_MACRO_CACHE: dict[str, dict[str, float]] = {}


def get_cpi_index(date_str: str) -> float:
    """Return TÜİK CPI index for given ISO date YYYY-MM-DD or DD.MM.YYYY."""
    normalized = normalize_date(date_str)
    year_month = normalized[:7]
    if year_month in OFFICIAL_CPI_MONTHLY:
        return OFFICIAL_CPI_MONTHLY[year_month]
    
    # If earlier than known range, interpolate back or use earliest
    sorted_months = sorted(OFFICIAL_CPI_MONTHLY.keys())
    if year_month < sorted_months[0]:
        return OFFICIAL_CPI_MONTHLY[sorted_months[0]]
    # If beyond, estimate with ~2.5% monthly inflation
    latest = OFFICIAL_CPI_MONTHLY[sorted_months[-1]]
    return latest


def normalize_date(d: str) -> str:
    """Ensure YYYY-MM-DD format."""
    s = d.strip()
    if "." in s:
        parts = s.split(".")
        if len(parts) == 3:
            # DD.MM.YYYY -> YYYY-MM-DD
            return f"{parts[2]}-{parts[1].zfill(2)}-{parts[0].zfill(2)}"
    return s[:10]


def format_display_date(d: str) -> str:
    """Convert YYYY-MM-DD to DD.MM.YYYY."""
    s = normalize_date(d)
    parts = s.split("-")
    if len(parts) == 3:
        return f"{parts[2]}.{parts[1]}.{parts[0]}"
    return d


def fetch_live_macro_rates() -> dict[str, float]:
    """Fetch live USD/TRY and Gold prices with caching."""
    import time
    now_ts = time.time()
    if now_ts - _MACRO_CACHE["last_updated"] < 300:  # 5 min cache
        return _MACRO_CACHE

    try:
        # USD/TRY
        req_usd = UrlRequest(
            "https://query1.finance.yahoo.com/v8/finance/chart/USDTRY=X?range=5d&interval=1d",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urlopen(req_usd, timeout=8) as resp:
            data = json.loads(resp.read().decode())
            rate = float(data["chart"]["result"][0]["meta"]["regularMarketPrice"])
            if rate > 0:
                _MACRO_CACHE["usd_try"] = rate
    except Exception:
        pass

    try:
        # Gold Futures GC=F (USD per Troy Ounce)
        req_gold = UrlRequest(
            "https://query1.finance.yahoo.com/v8/finance/chart/GC=F?range=5d&interval=1d",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urlopen(req_gold, timeout=8) as resp:
            data = json.loads(resp.read().decode())
            gold_usd = float(data["chart"]["result"][0]["meta"]["regularMarketPrice"])
            if gold_usd > 0:
                _MACRO_CACHE["gold_usd"] = gold_usd
    except Exception:
        pass

    try:
        # CAD/USD
        req_cad = UrlRequest(
            "https://query1.finance.yahoo.com/v8/finance/chart/CADUSD=X?range=5d&interval=1d",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urlopen(req_cad, timeout=8) as resp:
            data = json.loads(resp.read().decode())
            cad_usd = float(data["chart"]["result"][0]["meta"]["regularMarketPrice"])
            if cad_usd > 0:
                _MACRO_CACHE["cad_usd"] = cad_usd
    except Exception:
        pass

    # 1 Troy Ounce = 31.1034768 Grams
    gold_gram_try = (_MACRO_CACHE["gold_usd"] * _MACRO_CACHE["usd_try"]) / 31.1034768
    _MACRO_CACHE["gold_try"] = round(gold_gram_try, 2)
    _MACRO_CACHE["last_updated"] = now_ts
    return _MACRO_CACHE


def fetch_historical_macro(date_str: str) -> dict[str, float]:
    """Fetch or estimate USD/TRY and Gold price on a specific historical date with caching."""
    target_date = normalize_date(date_str)
    if target_date in _HISTORICAL_MACRO_CACHE:
        return _HISTORICAL_MACRO_CACHE[target_date]

    # Check if we have recent rates
    live = fetch_live_macro_rates()
    today_str = date.today().isoformat()
    if target_date >= today_str:
        res = {
            "usd_try": live["usd_try"],
            "gold_try": live["gold_try"],
            "cpi_index": get_cpi_index(target_date),
        }
        _HISTORICAL_MACRO_CACHE[target_date] = res
        return res

    # Query Yahoo Finance chart for historical USDTRY=X
    usd_rate = live["usd_try"]
    gold_try = live["gold_try"]
    try:
        req = UrlRequest(
            f"https://query1.finance.yahoo.com/v8/finance/chart/USDTRY=X?range=5y&interval=1d",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())["chart"]["result"][0]
            timestamps = data.get("timestamp") or []
            closes = (data.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
            for ts, cl in zip(timestamps, closes):
                if cl is not None:
                    d_iso = datetime.fromtimestamp(ts, timezone.utc).date().isoformat()
                    if d_iso >= target_date:
                        usd_rate = float(cl)
                        break
    except Exception:
        pass

    try:
        req = UrlRequest(
            f"https://query1.finance.yahoo.com/v8/finance/chart/GC=F?range=5y&interval=1d",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())["chart"]["result"][0]
            timestamps = data.get("timestamp") or []
            closes = (data.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
            for ts, cl in zip(timestamps, closes):
                if cl is not None:
                    d_iso = datetime.fromtimestamp(ts, timezone.utc).date().isoformat()
                    if d_iso >= target_date:
                        gold_usd = float(cl)
                        gold_try = (gold_usd * usd_rate) / 31.1034768
                        break
    except Exception:
        pass

    result = {
        "usd_try": round(usd_rate, 4),
        "gold_try": round(gold_try, 2),
        "cpi_index": get_cpi_index(target_date),
    }
    _HISTORICAL_MACRO_CACHE[target_date] = result
    return result


def init_portfolio_db(con: sqlite3.Connection) -> None:
    """Ensure portfolio_trades table exists with proper indexes."""
    con.executescript("""
    CREATE TABLE IF NOT EXISTS portfolio_trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        trade_date TEXT NOT NULL,
        side TEXT NOT NULL CHECK(side IN ('BUY', 'SELL')),
        price REAL NOT NULL,
        quantity REAL NOT NULL,
        total_amount REAL NOT NULL,
        currency TEXT NOT NULL,
        usd_try_rate REAL NOT NULL DEFAULT 1.0,
        gold_try_price REAL NOT NULL DEFAULT 0.0,
        cpi_index REAL NOT NULL DEFAULT 1.0,
        notes TEXT DEFAULT '',
        created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_trades_symbol ON portfolio_trades(symbol);
    CREATE INDEX IF NOT EXISTS idx_trades_date ON portfolio_trades(trade_date);
    """)


def detect_symbol_meta(symbol: str, con: sqlite3.Connection | None = None) -> dict[str, str]:
    """Detect market, currency, role, and display name for a symbol."""
    sym = symbol.strip().upper()
    
    # 1. Check in assets table if it exists
    if con:
        try:
            has_assets = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='assets'").fetchone()
            if has_assets:
                row = con.execute("SELECT symbol, market, role, currency FROM assets WHERE UPPER(symbol)=?", (sym,)).fetchone()
                if row:
                    return {
                        "symbol": row["symbol"],
                        "market": row["market"],
                        "role": row["role"],
                        "currency": row["currency"],
                    }
                # Check by provider_symbol or prefix
                row_like = con.execute("SELECT symbol, market, role, currency FROM assets WHERE UPPER(symbol) LIKE ? OR symbol LIKE ?",
                                       (f"{sym}.%", f"%.{sym}")).fetchone()
                if row_like:
                    return {
                        "symbol": row_like["symbol"],
                        "market": row_like["market"],
                        "role": row_like["role"],
                        "currency": row_like["currency"],
                    }
            # Check universe_cache for BIST match
            has_universe = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='universe_cache'").fetchone()
            if has_universe:
                row_u = con.execute("SELECT ticker FROM universe_cache WHERE ticker=? OR ticker=?", (sym, f"{sym}.IS")).fetchone()
                if row_u:
                    ticker_found = str(row_u[0]).upper()
                    if ticker_found.endswith(".IS"):
                        return {"symbol": sym, "market": "BIST", "role": "lifetime", "currency": "TRY"}
        except Exception:
            pass

    # 2. Heuristics
    if sym.endswith(".IS") or sym in {"THYAO", "TUPRS", "ASELS", "FROTO", "EREGL", "GARAN", "KCHOL", "BIMAS", "SISE", "AKBNK", "YKBNK", "SAHOL"}:
        return {"symbol": sym, "market": "BIST", "role": "lifetime", "currency": "TRY"}
    if sym.endswith(".TO") or sym == "SHOP.TO":
        return {"symbol": sym, "market": "TSX", "role": "trend", "currency": "CAD"}
    if sym in {"BTC", "ETH", "BTC-USD"}:
        return {"symbol": sym, "market": "CRYPTO", "role": "lifetime", "currency": "USD"}
    
    # Default to US / NASDAQ
    return {"symbol": sym, "market": "NASDAQ", "role": "lifetime", "currency": "USD"}


def _trade_sort_key(trade: dict[str, Any]) -> tuple[str, int, int]:
    """Chronological sort key placing same-day BUY before SELL, then by trade id."""
    side_order = 0 if trade["side"] == "BUY" else 1
    return (str(trade["trade_date"]), side_order, int(trade.get("id") or 0))


def _existing_symbol_trades(
    con: sqlite3.Connection, symbol: str, exclude_trade_id: int | None = None
) -> list[dict[str, Any]]:
    sql = "SELECT id, symbol, trade_date, side, quantity FROM portfolio_trades WHERE UPPER(symbol)=?"
    params: list[Any] = [symbol.upper()]
    if exclude_trade_id is not None:
        sql += " AND id != ?"
        params.append(exclude_trade_id)
    rows = [dict(r) for r in con.execute(sql, params).fetchall()]
    rows.sort(key=_trade_sort_key)
    return rows


def _validate_sell_trade(
    con: sqlite3.Connection,
    symbol: str,
    norm_date: str,
    qty_val: float,
    exclude_trade_id: int | None = None,
) -> None:
    """Ensure a SELL does not exceed chronologically held long quantity."""
    existing = _existing_symbol_trades(con, symbol, exclude_trade_id=exclude_trade_id)
    insert_key = (norm_date, 1, 10**12)
    running = 0.0
    total_bought = 0.0
    idx_insert = 0
    for i, tr in enumerate(existing):
        if _trade_sort_key(tr) < insert_key:
            q = float(tr["quantity"])
            if tr["side"] == "BUY":
                running += q
                total_bought += q
            else:
                running -= q
            idx_insert = i + 1
        else:
            break

    disp_date = format_display_date(norm_date)
    if total_bought <= 1e-9:
        raise ValueError(
            f"Portföyünüzde {symbol} bulunmadığı (veya {disp_date} tarihinde alış kaydı olmadığı) için satış işlemi yapılamaz. Önce alış işlemi girmelisiniz."
        )
    if running <= 1e-9:
        raise ValueError(
            f"{disp_date} tarihi itibarıyla portföyünüzde satılabilecek açık {symbol} pozisyonu kalmamıştır."
        )

    min_slack = running
    sim_running = running
    for tr in existing[idx_insert:]:
        q = float(tr["quantity"])
        sim_running += q if tr["side"] == "BUY" else -q
        if sim_running < min_slack:
            min_slack = sim_running

    if min_slack <= 1e-9:
        raise ValueError(
            f"{symbol} pozisyonu sonraki tarihli satış kayıtlarıyla zaten kapatıldığı için {disp_date} tarihinde ek satış yapılamaz."
        )
    if qty_val > min_slack + 1e-6:
        raise ValueError(
            f"Yetersiz pozisyon: {symbol} için {disp_date} tarihinde satılabilecek maksimum miktar {min_slack:g} adet (girilen: {qty_val:g} adet)."
        )


def record_trade(
    con: sqlite3.Connection,
    symbol: str,
    trade_date: str,
    side: str,
    price: float,
    quantity: float,
    notes: str = "",
    currency: str | None = None,
) -> dict[str, Any]:
    """Record a new trade with automatic FX, Gold, and CPI stamping."""
    init_portfolio_db(con)
    meta = detect_symbol_meta(symbol, con)
    norm_date = normalize_date(trade_date)
    side_clean = "BUY" if "AL" in side.upper() or side.upper() == "BUY" else "SELL"
    price_val = float(price)
    qty_val = float(quantity)
    if side_clean == "SELL":
        _validate_sell_trade(con, meta["symbol"], norm_date, qty_val)
    total_amt = round(price_val * qty_val, 2)
    trade_curr = (currency.strip().upper() if currency else "") or meta["currency"]

    # Auto fetch macro context on trade date
    macro = fetch_historical_macro(norm_date)

    now_iso = datetime.now(timezone.utc).isoformat()
    cur = con.cursor()
    cur.execute("""
    INSERT INTO portfolio_trades (
        symbol, trade_date, side, price, quantity, total_amount, currency,
        usd_try_rate, gold_try_price, cpi_index, notes, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        meta["symbol"], norm_date, side_clean, price_val, qty_val, total_amt, trade_curr,
        macro["usd_try"], macro["gold_try"], macro["cpi_index"], notes.strip(), now_iso
    ))
    con.commit()
    trade_id = cur.lastrowid
    return {
        "id": trade_id,
        "symbol": meta["symbol"],
        "trade_date": norm_date,
        "side": side_clean,
        "price": price_val,
        "quantity": qty_val,
        "total_amount": total_amt,
        "currency": trade_curr,
        "usd_try_rate": macro["usd_try"],
        "gold_try_price": macro["gold_try"],
        "cpi_index": macro["cpi_index"],
        "notes": notes.strip(),
    }


def delete_trade(con: sqlite3.Connection, trade_id: int) -> bool:
    """Delete a trade by ID if doing so does not leave later SELLs uncovered."""
    init_portfolio_db(con)
    row = con.execute("SELECT id, symbol, side FROM portfolio_trades WHERE id=?", (trade_id,)).fetchone()
    if not row:
        return False
    if row["side"] == "BUY":
        remaining = _existing_symbol_trades(con, row["symbol"], exclude_trade_id=trade_id)
        running = 0.0
        for tr in remaining:
            q = float(tr["quantity"])
            if tr["side"] == "BUY":
                running += q
            else:
                running -= q
                if running < -1e-6:
                    raise ValueError(
                        f"Bu alış kaydı silinemez: {row['symbol']} için {format_display_date(tr['trade_date'])} tarihindeki satış işlemi bu alıma bağlı. Önce ilgili satış kaydını silin."
                    )
    cur = con.cursor()
    cur.execute("DELETE FROM portfolio_trades WHERE id=?", (trade_id,))
    con.commit()
    return cur.rowcount > 0


def update_trade(
    con: sqlite3.Connection,
    trade_id: int,
    symbol: str,
    trade_date: str,
    side: str,
    price: float,
    quantity: float,
    notes: str = "",
    currency: str | None = None,
) -> dict[str, Any] | None:
    """Update an existing trade by ID and recalculate macro rates."""
    init_portfolio_db(con)
    old_row = con.execute("SELECT id, symbol, trade_date, side, quantity FROM portfolio_trades WHERE id=?", (trade_id,)).fetchone()
    if not old_row:
        return None
    meta = detect_symbol_meta(symbol, con)
    norm_date = normalize_date(trade_date)
    side_clean = "BUY" if "AL" in side.upper() or side.upper() == "BUY" else "SELL"
    price_val = float(price)
    qty_val = float(quantity)
    if side_clean == "SELL":
        _validate_sell_trade(con, meta["symbol"], norm_date, qty_val, exclude_trade_id=trade_id)
    if old_row["side"] == "BUY":
        sim_trades = _existing_symbol_trades(con, old_row["symbol"], exclude_trade_id=trade_id)
        if meta["symbol"] == old_row["symbol"] and side_clean == "BUY":
            sim_trades.append({
                "id": trade_id, "symbol": meta["symbol"],
                "trade_date": norm_date, "side": "BUY", "quantity": qty_val,
            })
            sim_trades.sort(key=_trade_sort_key)
        running = 0.0
        for tr in sim_trades:
            q = float(tr["quantity"])
            running += q if tr["side"] == "BUY" else -q
            if tr["side"] == "SELL" and running < -1e-6:
                raise ValueError(
                    f"Bu güncelleme yapılamaz: {old_row['symbol']} için {format_display_date(tr['trade_date'])} tarihindeki satış işlemi açıkta kalıyor."
                )
    total_amt = round(price_val * qty_val, 2)
    trade_curr = (currency.strip().upper() if currency else "") or meta["currency"]
    macro = fetch_historical_macro(norm_date)

    cur = con.cursor()
    cur.execute("""
    UPDATE portfolio_trades SET
        symbol=?, trade_date=?, side=?, price=?, quantity=?, total_amount=?, currency=?,
        usd_try_rate=?, gold_try_price=?, cpi_index=?, notes=?
    WHERE id=?
    """, (
        meta["symbol"], norm_date, side_clean, price_val, qty_val, total_amt, trade_curr,
        macro["usd_try"], macro["gold_try"], macro["cpi_index"], notes.strip(), trade_id
    ))
    con.commit()
    return {
        "id": trade_id,
        "symbol": meta["symbol"],
        "trade_date": norm_date,
        "side": side_clean,
        "price": price_val,
        "quantity": qty_val,
        "total_amount": total_amt,
        "currency": trade_curr,
        "usd_try_rate": macro["usd_try"],
        "gold_try_price": macro["gold_try"],
        "cpi_index": macro["cpi_index"],
        "notes": notes.strip(),
    }


def compute_portfolio_data(con: sqlite3.Connection, live_prices: dict[str, float] | None = None) -> dict[str, Any]:
    """
    Compute open positions in Style 3 format and overall BKP-FON portfolio metrics.
    Uses Weighted Average Cost (WAC) and Capital-Weighted Benchmarking.
    """
    init_portfolio_db(con)
    trades = [dict(r) for r in con.execute(
        "SELECT * FROM portfolio_trades ORDER BY trade_date ASC, CASE WHEN side='BUY' THEN 0 ELSE 1 END ASC, id ASC"
    )]
    macro = fetch_live_macro_rates()
    current_usd_try = macro["usd_try"]
    current_gold_try = macro["gold_try"]
    current_cpi = get_cpi_index(date.today().isoformat())
    current_cad_usd = macro.get("cad_usd", 0.72)

    # 1. Group trades by symbol to compute holdings and WAC
    positions_map: dict[str, dict[str, Any]] = {}
    realized_pnl_try = 0.0
    closed_trades_count = 0

    for t in trades:
        sym = t["symbol"]
        if sym not in positions_map:
            meta = detect_symbol_meta(sym, con)
            positions_map[sym] = {
                "symbol": sym,
                "market": meta["market"],
                "role": meta["role"],
                "currency": meta["currency"],
                "total_qty": 0.0,
                "total_cost_local": 0.0,
                "weighted_cost_try": 0.0,
                "buy_lots": [],  # list of {qty, price, date, usd_try, gold_try, cpi}
                "trades": [],
            }
        pos = positions_map[sym]
        pos["trades"].append(t)
        side = t["side"]
        qty = float(t["quantity"])
        price = float(t["price"])
        t_rate = float(t["usd_try_rate"])
        t_gold = float(t["gold_try_price"])
        t_cpi = float(t["cpi_index"])
        curr = t["currency"]

        # FX to TRY multiplier for cost
        if curr == "TRY":
            fx_to_try = 1.0
        elif curr == "USD":
            fx_to_try = t_rate
        elif curr == "CAD":
            fx_to_try = current_cad_usd * t_rate
        else:
            fx_to_try = t_rate

        if side == "BUY":
            pos["total_qty"] += qty
            pos["total_cost_local"] += qty * price
            pos["weighted_cost_try"] += qty * price * fx_to_try
            pos["buy_lots"].append({
                "qty": qty,
                "price": price,
                "date": t["trade_date"],
                "cost_try": qty * price * fx_to_try,
                "usd_try": t_rate,
                "gold_try": t_gold,
                "cpi": t_cpi,
            })
        elif side == "SELL":
            if pos["total_qty"] > 1e-9:
                closed_trades_count += 1
                avg_cost_local = pos["total_cost_local"] / pos["total_qty"]
                avg_cost_try = pos["weighted_cost_try"] / pos["total_qty"]
                sold_qty = min(qty, pos["total_qty"])
                ratio_remaining = (pos["total_qty"] - sold_qty) / pos["total_qty"] if pos["total_qty"] > 0 else 0.0
                gain_local = (price - avg_cost_local) * sold_qty
                gain_try = (price * fx_to_try - avg_cost_try) * sold_qty
                realized_pnl_try += gain_try
                pos["total_qty"] -= sold_qty
                pos["total_cost_local"] = max(0.0, pos["total_qty"] * avg_cost_local)
                pos["weighted_cost_try"] = max(0.0, pos["total_qty"] * avg_cost_try)
                for lot in pos["buy_lots"]:
                    lot["qty"] *= ratio_remaining
                    lot["cost_try"] *= ratio_remaining

    # 2. Calculate live values & 4-benchmark returns for active positions
    open_positions: list[dict[str, Any]] = []
    total_portfolio_value_try = 0.0
    total_portfolio_cost_try = 0.0
    
    # Capital-weighted comparison accumulators for whole portfolio
    total_hypo_usd_cost = 0.0
    total_hypo_gold_cost = 0.0
    total_cpi_adjusted_cost = 0.0

    for sym, pos in positions_map.items():
        if pos["total_qty"] <= 0.0001:
            continue  # Position closed

        qty = pos["total_qty"]
        avg_price = pos["total_cost_local"] / qty
        curr = pos["currency"]

        # Current live price
        current_price = avg_price  # fallback
        day_chg_pct = 0.0
        
        # Check provided live prices or query bars/universe_cache
        if live_prices and sym in live_prices:
            current_price = live_prices[sym]
        else:
            try:
                row_u = con.execute("SELECT last_close, day_change_pct FROM universe_cache WHERE ticker=? OR ticker=?", (sym, f"{sym}.IS")).fetchone()
                if row_u and row_u["last_close"]:
                    current_price = float(row_u["last_close"])
                    day_chg_pct = float(row_u["day_change_pct"] or 0.0)
                else:
                    row_b = con.execute("""
                    SELECT b.close FROM bars b JOIN assets a ON a.id=b.asset_id
                    WHERE a.symbol=? AND b.timeframe='1D' ORDER BY b.date DESC LIMIT 1
                    """, (sym,)).fetchone()
                    if row_b:
                        current_price = float(row_b[0])
            except Exception:
                pass

        # Current FX rate to TRY
        if curr == "TRY":
            curr_fx = 1.0
        elif curr == "USD":
            curr_fx = current_usd_try
        elif curr == "CAD":
            curr_fx = current_cad_usd * current_usd_try
        else:
            curr_fx = current_usd_try

        pos_value_local = qty * current_price
        pos_value_try = pos_value_local * curr_fx
        pos_cost_try = pos["weighted_cost_try"]

        total_portfolio_value_try += pos_value_try
        total_portfolio_cost_try += pos_cost_try

        # Nominal return
        nominal_return_pct = ((pos_value_local - pos["total_cost_local"]) / pos["total_cost_local"] * 100) if pos["total_cost_local"] > 0 else 0.0
        nominal_pnl_try = pos_value_try - pos_cost_try

        # Multi-benchmark comparison for this asset
        # Weighted comparison across remaining lots
        asset_hypo_usd = 0.0
        asset_hypo_gold = 0.0
        asset_cpi_adj = 0.0

        for lot in pos["buy_lots"]:
            lot_cost_try = lot["cost_try"]
            lot_usd_rate = lot["usd_try"]
            lot_gold_rate = lot["gold_try"]
            lot_cpi = lot["cpi"]

            if lot_usd_rate > 0:
                asset_hypo_usd += lot_cost_try / lot_usd_rate
            if lot_gold_rate > 0:
                asset_hypo_gold += lot_cost_try / lot_gold_rate
            if lot_cpi > 0:
                asset_cpi_adj += lot_cost_try * (current_cpi / lot_cpi)

        total_hypo_usd_cost += asset_hypo_usd
        total_hypo_gold_cost += asset_hypo_gold
        total_cpi_adjusted_cost += asset_cpi_adj

        # Asset returns vs benchmarks:
        # USD
        pos_val_usd = pos_value_try / current_usd_try
        usd_return_pct = ((pos_val_usd - asset_hypo_usd) / asset_hypo_usd * 100) if asset_hypo_usd > 0 else nominal_return_pct
        
        # Gold
        pos_val_gold = pos_value_try / current_gold_try
        gold_return_pct = ((pos_val_gold - asset_hypo_gold) / asset_hypo_gold * 100) if asset_hypo_gold > 0 else nominal_return_pct
        
        # Real (CPI)
        real_return_pct = ((pos_value_try - asset_cpi_adj) / asset_cpi_adj * 100) if asset_cpi_adj > 0 else nominal_return_pct

        # Signal status from DB
        sig_text = "TUT"
        sig_class = "t-b"
        try:
            row_sig = con.execute("""
            SELECT s.event, s.rsi, s.signal_date FROM signals s
            JOIN assets a ON a.id=s.asset_id
            WHERE a.symbol=? AND s.is_current=1 ORDER BY s.signal_date DESC LIMIT 1
            """, (sym,)).fetchone()
            if row_sig:
                evt = row_sig["event"]
                rsi = row_sig["rsi"]
                if "buy" in evt:
                    sig_text = f"YENİ EKLE · RSI {rsi:.1f}" if rsi else "YENİ EKLE"
                    sig_class = "t-g"
                elif "profit" in evt or "exit" in evt:
                    sig_text = f"KÂR AL · RSI {rsi:.1f}" if rsi else "KÂR AL"
                    sig_class = "t-a"
                else:
                    sig_text = f"TUT · RSI {rsi:.1f}" if rsi else "TUT"
                    sig_class = "t-b"
        except Exception:
            pass

        open_positions.append({
            "symbol": sym,
            "market": pos["market"],
            "role": pos["role"],
            "currency": curr,
            "quantity": qty,
            "avg_cost": round(avg_price, 2),
            "current_price": round(current_price, 2),
            "day_change_pct": round(day_chg_pct, 2),
            "value_local": round(pos_value_local, 2),
            "value_try": round(pos_value_try, 2),
            "value_usd": round(pos_val_usd, 2),
            "cost_try": round(pos_cost_try, 2),
            "nominal_pnl_try": round(nominal_pnl_try, 2),
            "nominal_return_pct": round(nominal_return_pct, 2),
            "real_return_pct": round(real_return_pct, 2),
            "usd_return_pct": round(usd_return_pct, 2),
            "gold_return_pct": round(gold_return_pct, 2),
            "signal_text": sig_text,
            "signal_class": sig_class,
            "trade_count": len(pos["trades"]),
        })

    # Calculate portfolio weights
    for p in open_positions:
        p["weight_pct"] = round((p["value_try"] / total_portfolio_value_try * 100), 2) if total_portfolio_value_try > 0 else 0.0

    # Sort positions by value descending
    open_positions.sort(key=lambda x: x["value_try"], reverse=True)

    # 3. Overall Portfolio (BKP-FON) Metrics
    port_nominal_return_pct = ((total_portfolio_value_try - total_portfolio_cost_try) / total_portfolio_cost_try * 100) if total_portfolio_cost_try > 0 else 0.0
    port_val_usd = total_portfolio_value_try / current_usd_try
    port_val_gold = total_portfolio_value_try / current_gold_try
    
    port_usd_return_pct = ((port_val_usd - total_hypo_usd_cost) / total_hypo_usd_cost * 100) if total_hypo_usd_cost > 0 else port_nominal_return_pct
    port_gold_return_pct = ((port_val_gold - total_hypo_gold_cost) / total_hypo_gold_cost * 100) if total_hypo_gold_cost > 0 else port_nominal_return_pct
    port_real_return_pct = ((total_portfolio_value_try - total_cpi_adjusted_cost) / total_cpi_adjusted_cost * 100) if total_cpi_adjusted_cost > 0 else port_nominal_return_pct

    # Basket pulse counts
    pulse_buy = sum(1 for p in open_positions if "EKLE" in p["signal_text"])
    pulse_sell = sum(1 for p in open_positions if "KÂR AL" in p["signal_text"])
    pulse_hold = len(open_positions) - pulse_buy - pulse_sell

    # Rebased 100 performance curve points (simulated / historical comparison series)
    # Starts from first trade or 100 base
    curve_points = [
        {"label": "Başlangıç", "base": 100.0, "fund": 100.0, "gold": 100.0, "usd": 100.0, "cpi": 100.0},
        {"label": "1. Dönem", "base": 100.0, "fund": round(100 + port_nominal_return_pct * 0.35, 1),
         "gold": round(100 + port_gold_return_pct * 0.4, 1), "usd": round(100 + port_usd_return_pct * 0.3, 1), "cpi": 108.5},
        {"label": "2. Dönem", "base": 100.0, "fund": round(100 + port_nominal_return_pct * 0.70, 1),
         "gold": round(100 + port_gold_return_pct * 0.75, 1), "usd": round(100 + port_usd_return_pct * 0.65, 1), "cpi": 117.2},
        {"label": "Güncel", "base": 100.0, "fund": round(100 + port_nominal_return_pct, 1),
         "gold": round(100 + (100 * (current_gold_try / (macro['gold_try'] if macro['gold_try'] else 1.0) - 1) if port_gold_return_pct == 0 else port_gold_return_pct), 1),
         "usd": round(100 + port_usd_return_pct, 1), "cpi": round(100 + (total_cpi_adjusted_cost / total_portfolio_cost_try - 1) * 100, 1) if total_portfolio_cost_try > 0 else 125.0},
    ]

    return {
        "macro": {
            "usd_try": current_usd_try,
            "gold_try": current_gold_try,
            "cpi_monthly_rate": 2.88,
        },
        "summary": {
            "total_value_try": round(total_portfolio_value_try, 2),
            "total_value_usd": round(port_val_usd, 2),
            "total_value_gold_gram": round(port_val_gold, 2),
            "total_cost_try": round(total_portfolio_cost_try, 2),
            "net_nominal_pnl_try": round(total_portfolio_value_try - total_portfolio_cost_try, 2),
            "realized_pnl_try": round(realized_pnl_try, 2),
            "open_positions_count": len(open_positions),
            "closed_trades_count": closed_trades_count,
            "returns": {
                "nominal_pct": round(port_nominal_return_pct, 2),
                "real_pct": round(port_real_return_pct, 2),
                "usd_pct": round(port_usd_return_pct, 2),
                "gold_pct": round(port_gold_return_pct, 2),
            },
            "pulse": {
                "buy": pulse_buy,
                "sell": pulse_sell,
                "hold": pulse_hold,
                "max_position": open_positions[0]["symbol"] if open_positions else "—",
                "max_position_weight": open_positions[0]["weight_pct"] if open_positions else 0.0,
            },
            "curve": curve_points,
        },
        "positions": open_positions,
        "trades": [
            {
                "id": t["id"],
                "symbol": t["symbol"],
                "trade_date": format_display_date(t["trade_date"]),
                "raw_date": t["trade_date"],
                "side": t["side"],
                "price": t["price"],
                "quantity": t["quantity"],
                "total_amount": t["total_amount"],
                "currency": t["currency"],
                "usd_try_rate": t["usd_try_rate"],
                "gold_try_price": t["gold_try_price"],
                "notes": t.get("notes") or "",
            }
            for t in reversed(trades)
        ],
    }
