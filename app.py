"""Private, local-first BEKAP-inspired portfolio signal and chart workspace.

Daily OHLC bars are refreshed from Yahoo Finance's no-key chart endpoint, with
optional configured providers as a fallback. Signals use completed bars.
"""
from __future__ import annotations

import csv
import asyncio
import hashlib
import io
import json
import os
import sqlite3
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo
from urllib.request import Request as UrlRequest, urlopen

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
import bekap_indicators
import portfolio_engine

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("BEKAP_DB_PATH", ROOT / "bekap.sqlite3"))
LOGO_CACHE_DIR = ROOT / "cache" / "logos"
VERSION = "0.2.0"
WEEKLY_BRAKE_ENGINE_VERSION = "weekly-brake-v1"
INDICATOR_WARMUP_YEARS = 10
_INSTRUMENT_SEARCH_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_LOGO_CACHE_LOCK = threading.Lock()
_LOGO_FETCH_PENDING: set[str] = set()
app = FastAPI(title="BEKAP Esinli Sepet Sinyal Sistemi", version=VERSION)
app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")


def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con


def initialize() -> None:
    with db() as con:
        portfolio_engine.init_portfolio_db(con)
        con.executescript("""
        CREATE TABLE IF NOT EXISTS strategy_versions(
          id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, version TEXT NOT NULL UNIQUE,
          config_json TEXT NOT NULL, note TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS assets(
          id INTEGER PRIMARY KEY, symbol TEXT NOT NULL UNIQUE, market TEXT NOT NULL,
          role TEXT NOT NULL CHECK(role IN ('lifetime','trend','periodic')),
          currency TEXT NOT NULL, target_weight REAL NOT NULL DEFAULT 0,
          max_weight REAL NOT NULL DEFAULT 0, tv_symbol TEXT NOT NULL DEFAULT '',
          parameters_json TEXT NOT NULL DEFAULT '{}',
          enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS bars(
          asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
          timeframe TEXT NOT NULL CHECK(timeframe IN ('1D','1W')), date TEXT NOT NULL,
          open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
          volume REAL, PRIMARY KEY(asset_id,timeframe,date));
        CREATE TABLE IF NOT EXISTS intraday_bars(
          asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
          date TEXT NOT NULL, open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
          close REAL NOT NULL, volume REAL, updated_at TEXT NOT NULL,
          PRIMARY KEY(asset_id,date));
        CREATE TABLE IF NOT EXISTS signals(
          id INTEGER PRIMARY KEY, event_key TEXT NOT NULL UNIQUE,
          asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE, event TEXT NOT NULL,
          timeframe TEXT NOT NULL, signal_date TEXT NOT NULL, confirmed_at TEXT NOT NULL,
          close_price REAL NOT NULL, execution_date TEXT, execution_price REAL,
          rsi REAL, reason TEXT NOT NULL, strategy_version TEXT NOT NULL,
          payload_json TEXT NOT NULL, is_current INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS outbox(
          id INTEGER PRIMARY KEY, event_key TEXT NOT NULL UNIQUE,
          signal_id INTEGER NOT NULL REFERENCES signals(id) ON DELETE CASCADE, status TEXT NOT NULL DEFAULT 'pending',
          attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT, created_at TEXT NOT NULL,
          sent_at TEXT);
        CREATE TABLE IF NOT EXISTS portfolio_settings(
          key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS market_data_state(
          asset_id INTEGER PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
          provider TEXT NOT NULL, provider_symbol TEXT NOT NULL, provider_exchange TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'waiting', last_checked TEXT, last_bar_date TEXT, last_error TEXT);
        CREATE TABLE IF NOT EXISTS corporate_actions(
          asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
          action_date TEXT NOT NULL, kind TEXT NOT NULL, value REAL NOT NULL,
          PRIMARY KEY(asset_id,action_date,kind));
        CREATE TABLE IF NOT EXISTS universe_cache(
          ticker TEXT PRIMARY KEY,
          last_close REAL,
          day_change_pct REAL,
          price_date TEXT,
          last_signal_event TEXT,
          last_signal_date TEXT,
          updated_at TEXT NOT NULL);
        """)
        signal_columns = {row["name"] for row in con.execute("PRAGMA table_info(signals)")}
        if "is_current" not in signal_columns:
            con.execute("ALTER TABLE signals ADD COLUMN is_current INTEGER NOT NULL DEFAULT 1")
        con.executescript("""
        DROP TRIGGER IF EXISTS asset_market_data_autocreate;
        CREATE TRIGGER IF NOT EXISTS asset_market_data_autocreate
        AFTER INSERT ON assets
        BEGIN
          INSERT OR IGNORE INTO market_data_state(asset_id,provider,provider_symbol,provider_exchange,status)
          VALUES(NEW.id,'Yahoo Finance',
            CASE
              WHEN upper(trim(NEW.symbol))='BTC' THEN 'BTC-USD'
              WHEN upper(trim(NEW.market)) IN ('BIST','BORSA ISTANBUL','ISTANBUL') OR upper(trim(NEW.symbol)) IN ('TUPRS','THYAO')
                THEN CASE WHEN upper(trim(NEW.symbol)) LIKE '%.IS' THEN upper(trim(NEW.symbol)) ELSE upper(trim(NEW.symbol)) || '.IS' END
              WHEN upper(trim(NEW.market)) IN ('TSX','TORONTO','TOR')
                THEN CASE WHEN upper(trim(NEW.symbol)) LIKE '%.TO' THEN upper(trim(NEW.symbol)) ELSE upper(trim(NEW.symbol)) || '.TO' END
              ELSE upper(trim(NEW.symbol))
            END,
            CASE WHEN upper(trim(NEW.market)) IN ('BIST','BORSA ISTANBUL','ISTANBUL') OR upper(trim(NEW.symbol)) IN ('TUPRS','THYAO') THEN 'BIST' ELSE '' END,
            'waiting');
        END;
        """)
        con.execute("INSERT OR IGNORE INTO strategy_versions(created_at,version,config_json,note) VALUES(?,?,?,?)",
                    (now(), VERSION, json.dumps(DEFAULT_CONFIG), "Initial BEKAP-inspired configurable baseline"))
        for k, v in {"initial_capital_usd": 0, "monthly_contribution_usd": 0,
                     "fees_bps": 0, "slippage_bps": 0, "cutoff_date": "2027-01-02",
                     "forward_start": date.today().isoformat()}.items():
            con.execute("INSERT OR IGNORE INTO portfolio_settings(key,value_json) VALUES(?,?)", (k, json.dumps(v)))
        for symbol, market, role, currency, tv_symbol, provider_symbol, exchange in SEED_ASSETS:
            con.execute("""INSERT OR IGNORE INTO assets(symbol,market,role,currency,target_weight,max_weight,
                tv_symbol,parameters_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                (symbol, market, role, currency, 0, 0, tv_symbol,
                 json.dumps({"rsi_period": 14, "buy_zone_max": 35, "weekly_brake_drawdown_pct": 21}), now()))
            asset_id = con.execute("SELECT id FROM assets WHERE symbol=?", (symbol,)).fetchone()[0]
            con.execute("""INSERT OR IGNORE INTO market_data_state(asset_id,provider,provider_symbol,provider_exchange)
                VALUES(?,?,?,?)""", (asset_id, "Twelve Data", provider_symbol, exchange))
        # Backfill provider state for existing assets created manually before this migration.
        for asset in con.execute("SELECT id,symbol,market FROM assets").fetchall():
            provider_symbol = yahoo_ticker(asset["symbol"], asset["market"])
            con.execute("""INSERT OR IGNORE INTO market_data_state(asset_id,provider,provider_symbol,provider_exchange)
                VALUES(?,?,?,?)""", (asset["id"], "Yahoo Finance", provider_symbol, ""))
            con.execute("""UPDATE market_data_state SET provider_symbol=?,provider_exchange=''
                WHERE asset_id=? AND provider='Yahoo Finance'""", (provider_symbol, asset["id"]))


DEFAULT_CONFIG = {
    "rsi_period": 14, "buy_zone_max": 35, "turn_up_required": True,
    "indicator_engine": "BEKAPRSI AUTO user Pine v6 + BEKAPTREND RSI/ADX visual port",
    "weekly_brake_drawdown_pct": 21,
    "weekly_return_rule": "weekly_buys_daily_sells_until_daily_take_profit",
    "lifetime_sell_rule": "profit_only", "trend_periodic_sell_rule": "full_exit",
    "buy_fraction_of_cash": 0.1, "max_position_fraction": 0.2,
    "monthly_contribution_usd": 0, "initial_capital_usd": 0,
    "fees_bps": 0, "slippage_bps": 0,
    "buy_rule_note": "BEKAP alım teyidi/kademe matematiği, kaynaklar doğrulanınca burada sürümlenecek.",
    "profit_definition": "MVP: sinyal motoru satış boyutunu otomatik varsaymaz; sözleşme ayarı gerekir."
}

# User's initial universe. Weight caps are left unset until supplied by the user.
SEED_ASSETS = [
    ("AAPL", "NASDAQ", "lifetime", "USD", "NASDAQ:AAPL", "AAPL", ""),
    ("NVDA", "NASDAQ", "trend", "USD", "NASDAQ:NVDA", "NVDA", ""),
    ("GLD", "NYSE Arca", "lifetime", "USD", "AMEX:GLD", "GLD", ""),
    ("BTC", "CRYPTO", "lifetime", "USD", "COINBASE:BTCUSD", "BTC/USD", ""),
    ("LLY", "NYSE", "trend", "USD", "NYSE:LLY", "LLY", ""),
    ("TUPRS", "BIST", "lifetime", "TRY", "BIST:TUPRS", "TUPRS", "BIST"),
    ("CPER", "NYSE Arca", "periodic", "USD", "AMEX:CPER", "CPER", ""),
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fetch_twelve_data(symbol: str, exchange: str = "", full_history: bool = True) -> dict[str, Any]:
    api_key = os.environ.get("TWELVE_DATA_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("TWELVE_DATA_API_KEY tanımlı değil")
    query = {"symbol": symbol, "interval": "1day", "outputsize": 5000 if full_history else 30,
             "order": "ASC", "adjust": "all", "apikey": api_key}
    if exchange:
        query["exchange"] = exchange
    request = UrlRequest("https://api.twelvedata.com/time_series?" + urlencode(query),
                         headers={"User-Agent": "BEKAP-Sepet/0.1 (personal research)"})
    with urlopen(request, timeout=35) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("status") == "error" or "values" not in payload:
        raise RuntimeError(payload.get("message", "Twelve Data geçerli zaman serisi döndürmedi"))
    return payload


def fetch_alpha_vantage(symbol: str, full_history: bool = True) -> dict[str, Any]:
    api_key = os.environ.get("ALPHA_VANTAGE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Alpha Vantage yedek anahtarı tanımlı değil")
    query = urlencode({"function": "TIME_SERIES_DAILY", "symbol": symbol,
                       "outputsize": "full" if full_history else "compact", "apikey": api_key})
    req = UrlRequest("https://www.alphavantage.co/query?" + query,
                     headers={"User-Agent": "BEKAP-Sepet/0.1"})
    with urlopen(req, timeout=35) as response:
        raw = json.loads(response.read().decode("utf-8"))
    series = raw.get("Time Series (Daily)")
    if not series:
        raise RuntimeError(raw.get("Note") or raw.get("Information") or raw.get("Error Message") or "Alpha Vantage günlük verisi yok")
    values = [{"datetime": d, "open": v["1. open"], "high": v["2. high"],
               "low": v["3. low"], "close": v["4. close"], "volume": v.get("5. volume")}
              for d, v in sorted(series.items())]
    return {"values": values, "meta": {"currency": "USD"}}


def yahoo_ticker(symbol: str, market: str = "") -> str:
    """Map portfolio symbols to Yahoo's canonical quote symbols."""
    symbol = symbol.strip().upper()
    market = market.strip().upper()
    if symbol == "BTC":
        return "BTC-USD"
    if market in {"BIST", "BORSA ISTANBUL", "ISTANBUL"} or symbol in {"TUPRS", "THYAO"}:
        return symbol if symbol.endswith(".IS") else f"{symbol}.IS"
    if market in {"TSX", "TORONTO", "TOR"}:
        return symbol if symbol.endswith(".TO") else f"{symbol}.TO"
    return symbol


def completed_daily_through(meta: dict[str, Any], as_of: datetime | None = None) -> date:
    """Latest date eligible for a completed daily bar at the supplied local time."""
    as_of = as_of or datetime.now(timezone.utc)
    try:
        exchange_tz = ZoneInfo(meta.get("exchangeTimezoneName") or "UTC")
    except Exception:
        exchange_tz = timezone.utc
    local_now = as_of.astimezone(exchange_tz)
    through = local_now.date() - timedelta(days=1)
    tz_name = str(meta.get("exchangeTimezoneName") or "")
    if ("New_York" in tz_name or "Toronto" in tz_name) and local_now.hour >= 17:
        through = local_now.date()
    elif "Istanbul" in tz_name and (local_now.hour, local_now.minute) >= (18, 15):
        through = local_now.date()
    return through


def aggregate_intraday_daily(
    timestamps: list[int], quote: dict[str, list[Any]], meta: dict[str, Any],
    as_of: datetime | None = None, include_current: bool = False,
) -> list[dict[str, Any]]:
    """Aggregate intraday candles into daily bars, optionally including today's open session."""
    try:
        exchange_tz = ZoneInfo(meta.get("exchangeTimezoneName") or "UTC")
    except Exception:
        exchange_tz = timezone.utc
    through = completed_daily_through(meta, as_of)
    current_day = (as_of or datetime.now(timezone.utc)).astimezone(exchange_tz).date()
    grouped: dict[str, dict[str, Any]] = {}
    fields = {key: quote.get(key) or [] for key in ("open", "high", "low", "close", "volume")}
    for i, timestamp in enumerate(timestamps):
        day = datetime.fromtimestamp(int(timestamp), timezone.utc).astimezone(exchange_tz).date()
        if day > through and not (include_current and day == current_day):
            continue
        try:
            opn, high, low, close = (float(fields[k][i]) for k in ("open", "high", "low", "close"))
        except (IndexError, TypeError, ValueError):
            continue
        if min(opn, high, low, close) <= 0 or high < max(opn, low, close) or low > min(opn, high, close):
            continue
        volume_value = fields["volume"][i] if i < len(fields["volume"]) else None
        volume = float(volume_value) if volume_value is not None else 0.0
        key = day.isoformat()
        if key not in grouped:
            grouped[key] = {"datetime": key, "open": opn, "high": high, "low": low,
                            "close": close, "volume": volume}
        else:
            row = grouped[key]
            row["high"] = max(row["high"], high)
            row["low"] = min(row["low"], low)
            row["close"] = close
            row["volume"] += volume
    return [grouped[key] for key in sorted(grouped)]


def fetch_yahoo_recent_daily(ticker: str, meta: dict[str, Any]) -> list[dict[str, Any]]:
    """Fetch the newest completed session when Yahoo's daily endpoint lags."""
    query = urlencode({"range": "5d", "interval": "60m", "includePrePost": "false"})
    request = UrlRequest(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?{query}",
        headers={"User-Agent": "Mozilla/5.0 (compatible; BEKAP-Sepet/1.0)"},
    )
    with urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    chart = payload.get("chart", {})
    if chart.get("error") or not chart.get("result"):
        raise RuntimeError(str(chart.get("error") or "Yahoo Finance saatlik tamamlayıcı veri döndürmedi"))
    recent = chart["result"][0]
    timestamps = recent.get("timestamp") or []
    quote = (recent.get("indicators", {}).get("quote") or [{}])[0]
    return aggregate_intraday_daily(timestamps, quote, meta)


def fetch_yahoo_current_bar(ticker: str, meta: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Get the current regular-session partial candle for chart display, never signal confirmation."""
    query = urlencode({"range": "5d", "interval": "15m", "includePrePost": "false"})
    request = UrlRequest(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?{query}",
        headers={"User-Agent": "Mozilla/5.0 (compatible; BEKAP-Sepet/1.0)"},
    )
    with urlopen(request, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))
    chart = payload.get("chart", {})
    if chart.get("error") or not chart.get("result"):
        return None
    result = chart["result"][0]
    meta = result.get("meta") or meta or {}
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    bars = aggregate_intraday_daily(result.get("timestamp") or [], quote, meta, include_current=True)
    try:
        exchange_tz = ZoneInfo(meta.get("exchangeTimezoneName") or "UTC")
    except Exception:
        exchange_tz = timezone.utc
    today = datetime.now(timezone.utc).astimezone(exchange_tz).date()
    if today <= completed_daily_through(meta):
        return None
    return next((bar for bar in reversed(bars) if bar["datetime"] == today.isoformat()), None)


def fetch_yahoo_chart(symbol: str, market: str = "") -> dict[str, Any]:
    """Fetch ten years so the 200-week EMA has sufficient Pine-compatible warm-up history."""
    ticker = yahoo_ticker(symbol, market)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range={INDICATOR_WARMUP_YEARS}y&interval=1d&events=div%2Csplits"
    req = UrlRequest(url, headers={"User-Agent": "Mozilla/5.0 (compatible; BEKAP-Sepet/1.0)"})
    with urlopen(req, timeout=25) as response:
        payload = json.loads(response.read().decode("utf-8"))
    chart = payload.get("chart", {})
    if chart.get("error") or not chart.get("result"):
        raise RuntimeError(str(chart.get("error") or "Yahoo Finance mum verisi bulunamadı"))
    result = chart["result"][0]
    meta = result.get("meta", {})
    try:
        exchange_tz = ZoneInfo(meta.get("exchangeTimezoneName") or "UTC")
    except Exception:
        exchange_tz = timezone.utc
    timestamps = result.get("timestamp") or []
    quotes = (result.get("indicators", {}).get("quote") or [{}])[0]
    values = []
    for i, ts in enumerate(timestamps):
        local_day = datetime.fromtimestamp(int(ts), timezone.utc).astimezone(exchange_tz).date()
        fields = {key: (quotes.get(key) or [None] * len(timestamps))[i]
                  for key in ("open", "high", "low", "close", "volume")}
        if any(fields[k] is None for k in ("open", "high", "low", "close")):
            continue
        values.append({"datetime": local_day.isoformat(), **fields})
    # The daily endpoint has recently lagged. Fill the latest gap from regular-session hourly bars.
    completed_through = completed_daily_through(meta)
    values = [v for v in values if date.fromisoformat(v["datetime"]) <= completed_through]
    intraday_values: list[dict[str, Any]] = []
    try:
        intraday_values = fetch_yahoo_recent_daily(ticker, meta)
    except Exception:
        # Keep the historical daily series available if the recent feed is temporarily down.
        pass
    by_date = {v["datetime"]: v for v in values}
    intraday_filled_dates = []
    for bar in intraday_values:
        # A completed daily OHLC candle is authoritative. Hourly aggregation only fills
        # dates missing from the daily endpoint; its final hourly close can differ by cents.
        if bar["datetime"] not in by_date:
            by_date[bar["datetime"]] = bar
            intraday_filled_dates.append(bar["datetime"])
    values = [by_date[k] for k in sorted(by_date)]
    actions = []
    for event_type, kind in (("dividends", "dividend"), ("splits", "split")):
        for ts, event in (result.get("events", {}).get(event_type) or {}).items():
            event_date = datetime.fromtimestamp(int(ts), timezone.utc).astimezone(exchange_tz).date().isoformat()
            if kind == "dividend":
                amount = float(event.get("amount") or 0)
            else:
                numerator = float(event.get("numerator") or 0)
                denominator = float(event.get("denominator") or 0)
                amount = numerator / denominator if denominator else 0
            if amount > 0:
                actions.append({"date": event_date, "kind": kind, "value": amount})
    if not values:
        raise RuntimeError("Yahoo Finance geçerli, tamamlanmış günlük mum döndürmedi")
    current_bar = None
    try:
        current_bar = fetch_yahoo_current_bar(ticker, meta)
    except Exception:
        # Keep completed candles available when the optional intraday feed is unavailable.
        pass
    return {"values": values, "actions": actions, "meta": meta,
            "intraday_used": bool(intraday_filled_dates),
            "intraday_last_date": intraday_filled_dates[-1] if intraday_filled_dates else None,
            "current_bar": current_bar}


def fetch_coinbase_btc(full_history: bool = True) -> dict[str, Any]:
    # Public BTC-USD candles; page through bounded windows (Coinbase caps each response).
    end = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    if full_history:
        try:
            start = end.replace(year=end.year - INDICATOR_WARMUP_YEARS)
        except ValueError:  # Feb 29 in a leap year.
            start = end.replace(year=end.year - INDICATOR_WARMUP_YEARS, day=28)
    else:
        start = end - timedelta(days=2)
    rows: dict[str, dict[str, Any]] = {}
    cursor = start
    while cursor < end:
        chunk_end = min(cursor + timedelta(days=299), end)
        q = urlencode({"granularity": 86400, "start": cursor.isoformat(), "end": chunk_end.isoformat()})
        req = UrlRequest(f"https://api.exchange.coinbase.com/products/BTC-USD/candles?{q}",
                         headers={"Accept": "application/json", "User-Agent": "BEKAP-Sepet/0.1"})
        with urlopen(req, timeout=35) as response:
            page = json.loads(response.read().decode("utf-8"))
        if not isinstance(page, list):
            raise RuntimeError("Coinbase BTC-USD candle yanıtı geçersiz")
        for ts, low, high, opn, close, volume in page:
            d = datetime.fromtimestamp(int(ts), timezone.utc).date().isoformat()
            if date.fromisoformat(d) < date.today():
                rows[d] = {"datetime": d, "open": opn, "high": high, "low": low,
                           "close": close, "volume": volume}
        cursor = chunk_end
        time.sleep(0.25)
    if not rows:
        raise RuntimeError("Coinbase BTC-USD tamamlanmış mum döndürmedi")
    return {"values": [rows[k] for k in sorted(rows)], "meta": {"currency": "USD"}}


def sync_market_data(asset_ids: set[int] | None = None) -> dict[str, Any]:
    with db() as con:
        query = """SELECT a.id,a.symbol,a.market,s.provider_symbol,s.provider_exchange,
          s.last_bar_date prior_last_bar_date,
          (SELECT MAX(w.date) FROM bars w WHERE w.asset_id=a.id AND w.timeframe='1W') prior_week_date
          FROM assets a JOIN market_data_state s ON s.asset_id=a.id WHERE a.enabled=1"""
        parameters: tuple[int, ...] = ()
        if asset_ids is not None:
            if not asset_ids:
                return {"updated": 0, "results": []}
            placeholders = ",".join("?" for _ in asset_ids)
            query += f" AND a.id IN ({placeholders})"
            parameters = tuple(asset_ids)
        query += " ORDER BY a.symbol"
        instruments = [dict(r) for r in con.execute(query, parameters)]
    results = []
    for asset in instruments:
        checked = now()
        try:
            try:
                payload = fetch_yahoo_chart(asset["symbol"], asset["market"])
                if payload.get("current_bar"):
                    provider = "Yahoo Finance · kapanış verisi + 15 dk geçici mum"
                elif payload.get("intraday_used"):
                    provider = "Yahoo Finance · günlük tarihçe + saatlik tamamlayıcı mumlar"
                else:
                    provider = "Yahoo Finance chart · günlük kapanış"
            except Exception as yahoo_error:
                if asset["symbol"] == "BTC":
                    payload, provider = fetch_coinbase_btc(), "Coinbase public candles · yedek"
                elif asset["symbol"] == "TUPRS" and os.environ.get("TWELVE_DATA_API_KEY", "").strip():
                    payload, provider = fetch_twelve_data(asset["provider_symbol"], asset["provider_exchange"]), "Twelve Data · yedek"
                elif os.environ.get("ALPHA_VANTAGE_API_KEY", "").strip() or os.environ.get("TWELVE_DATA_API_KEY", "").strip():
                    try:
                        payload, provider = fetch_alpha_vantage(asset["symbol"]), "Alpha Vantage · yedek"
                    except Exception:
                        payload, provider = fetch_twelve_data(asset["provider_symbol"], asset["provider_exchange"]), "Twelve Data · yedek"
                else:
                    raise RuntimeError(f"Yahoo Finance: {yahoo_error}") from yahoo_error
            meta = payload.get("meta", {})
            values = payload["values"]
            if not provider.startswith("Yahoo Finance"):
                values = [v for v in values if date.fromisoformat(v["datetime"][:10]) < date.today()]
            if not values:
                raise RuntimeError("Henüz tamamlanmış günlük bar gelmedi")
            with db() as con:
                for bar in values:
                    d = date.fromisoformat(bar["datetime"][:10])
                    o, h, l, c = [float(bar[k]) for k in ("open", "high", "low", "close")]
                    if min(o, h, l, c) <= 0 or h < max(o, l, c) or l > min(o, h, c):
                        continue
                    volume = float(bar["volume"]) if bar.get("volume") not in (None, "") else None
                    con.execute("""INSERT OR REPLACE INTO bars(asset_id,timeframe,date,open,high,low,close,volume)
                        VALUES(?,?,?,?,?,?,?,?)""", (asset["id"], "1D", d.isoformat(), o, h, l, c, volume))
                current_bar = payload.get("current_bar") if provider.startswith("Yahoo Finance") else None
                con.execute("DELETE FROM intraday_bars WHERE asset_id=?", (asset["id"],))
                if current_bar:
                    con.execute("""INSERT OR REPLACE INTO intraday_bars
                        (asset_id,date,open,high,low,close,volume,updated_at) VALUES(?,?,?,?,?,?,?,?)""",
                        (asset["id"], current_bar["datetime"], float(current_bar["open"]),
                         float(current_bar["high"]), float(current_bar["low"]),
                         float(current_bar["close"]), float(current_bar.get("volume") or 0), checked))
                last_date = max(v["datetime"][:10] for v in values)
                if "actions" in payload:
                    con.execute("DELETE FROM corporate_actions WHERE asset_id=?", (asset["id"],))
                    for action in payload["actions"]:
                        con.execute("INSERT OR REPLACE INTO corporate_actions(asset_id,action_date,kind,value) VALUES(?,?,?,?)",
                                    (asset["id"], action["date"], action["kind"], action["value"]))
                con.execute("""UPDATE market_data_state SET status='ok',provider=?,last_checked=?,last_bar_date=?,last_error=NULL
                    WHERE asset_id=?""", (provider, checked, last_date, asset["id"]))
            rebuild_weekly_bars(asset["id"])
            signal_result = run_weekly_brake_signals(asset["id"],
                notify_after_daily=asset["prior_last_bar_date"],
                notify_after_weekly=asset["prior_week_date"])
            results.append({"symbol": asset["symbol"], "status": "ok", "provider": provider, "last_bar_date": last_date,
                            "currency": meta.get("currency", ""), "signals_created": signal_result["created"]})
        except Exception as exc:
            with db() as con:
                con.execute("UPDATE market_data_state SET status='error',last_checked=?,last_error=? WHERE asset_id=?",
                            (checked, str(exc)[:500], asset["id"]))
            results.append({"symbol": asset["symbol"], "status": "error", "error": str(exc)[:250]})
    return {"updated": sum(r["status"] == "ok" for r in results), "results": results}


async def auto_sync_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(sync_market_data)
        except Exception:
            pass
        await asyncio.sleep(6 * 60 * 60)


def sync_intraday_bars() -> int:
    """Refresh display-only live candles; signal events still use confirmed daily bars."""
    with db() as con:
        assets = [dict(row) for row in con.execute(
            "SELECT id,symbol,market FROM assets WHERE enabled=1 ORDER BY symbol")]
    updated = 0
    for asset in assets:
        try:
            bar = fetch_yahoo_current_bar(yahoo_ticker(asset["symbol"], asset["market"]))
        except Exception:
            continue
        with db() as con:
            if bar:
                con.execute("""INSERT OR REPLACE INTO intraday_bars
                    (asset_id,date,open,high,low,close,volume,updated_at) VALUES(?,?,?,?,?,?,?,?)""",
                    (asset["id"], bar["datetime"], float(bar["open"]), float(bar["high"]),
                     float(bar["low"]), float(bar["close"]), float(bar.get("volume") or 0), now()))
                updated += 1
            else:
                con.execute("DELETE FROM intraday_bars WHERE asset_id=?", (asset["id"],))
    return updated


async def auto_intraday_loop() -> None:
    while True:
        await asyncio.sleep(10 * 60)
        try:
            await asyncio.to_thread(sync_intraday_bars)
        except Exception:
            pass


@app.on_event("startup")
async def startup() -> None:
    initialize()
    asyncio.create_task(auto_sync_loop())
    asyncio.create_task(auto_intraday_loop())


@app.get("/")
def home() -> FileResponse:
    return FileResponse(ROOT / "static" / "index.html", headers={"Cache-Control": "no-cache"})


def _weekly_ohlc(daily: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for row in daily:
        d = date.fromisoformat(row["date"])
        iso = d.isocalendar()
        groups.setdefault((iso.year, iso.week), []).append(row)
    weeks = []
    current = date.today().isocalendar()[:2]
    for key in sorted(groups):
        rows = groups[key]
        last_day = date.fromisoformat(rows[-1]["date"])
        if key == current and last_day.weekday() < 4:
            continue
        weeks.append({"date": rows[-1]["date"], "open": rows[0]["open"],
                      "high": max(r["high"] for r in rows), "low": min(r["low"] for r in rows),
                      "close": rows[-1]["close"],
                      "volume": sum(r["volume"] or 0 for r in rows)})
    return weeks


def _weekly_chart_ohlc(daily: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """TradingView-style weekly candles, including the current partial week for display only."""
    groups: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for row in daily:
        d = date.fromisoformat(row["date"])
        iso = d.isocalendar()
        groups.setdefault((iso.year, iso.week), []).append(row)
    current = date.today().isocalendar()[:2]
    weeks = []
    for key in sorted(groups):
        rows = groups[key]
        first_day = date.fromisoformat(rows[0]["date"])
        last_day = date.fromisoformat(rows[-1]["date"])
        provisional = key == current and last_day.weekday() < 4
        monday = first_day - timedelta(days=first_day.weekday())
        weeks.append({
            "date": monday.isoformat(), "open": rows[0]["open"],
            "high": max(row["high"] for row in rows),
            "low": min(row["low"] for row in rows),
            "close": rows[-1]["close"],
            "volume": sum(row["volume"] or 0 for row in rows),
            "provisional": provisional,
        })
    return weeks


def rebuild_weekly_bars(asset_id: int) -> int:
    with db() as con:
        daily = [dict(r) for r in con.execute("SELECT date,open,high,low,close,volume FROM bars WHERE asset_id=? AND timeframe='1D' ORDER BY date",
                                               (asset_id,))]
        weeks = _weekly_ohlc(daily)
        con.execute("DELETE FROM bars WHERE asset_id=? AND timeframe='1W'", (asset_id,))
        con.executemany("INSERT OR REPLACE INTO bars(asset_id,timeframe,date,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?,?)",
                        [(asset_id, "1W", r["date"], r["open"], r["high"], r["low"], r["close"], r["volume"]) for r in weeks])
    return len(weeks)


def build_chart_payload(asset_id: int, timeframe: str = "D") -> dict[str, Any]:
    with db() as con:
        asset = con.execute("SELECT * FROM assets WHERE id=? AND enabled=1", (asset_id,)).fetchone()
        if not asset:
            raise HTTPException(404, "Varlık bulunamadı.")
        source = con.execute("SELECT provider,last_bar_date,last_checked,status,last_error FROM market_data_state WHERE asset_id=?",
                             (asset_id,)).fetchone()
        daily = [dict(r) for r in con.execute("SELECT date,open,high,low,close,volume FROM bars WHERE asset_id=? AND timeframe='1D' ORDER BY date",
                                              (asset_id,))]
        live_bar = con.execute("SELECT date,open,high,low,close,volume,updated_at FROM intraday_bars WHERE asset_id=? ORDER BY date DESC LIMIT 1",
                               (asset_id,)).fetchone()
        actions = [dict(r) for r in con.execute("SELECT action_date date,kind,value FROM corporate_actions WHERE asset_id=? ORDER BY action_date",
                                                (asset_id,))]
    daily_display = [dict(row) for row in daily]
    if live_bar and (not daily or str(live_bar["date"]) > str(daily[-1]["date"])):
        daily_display.append({"date": live_bar["date"], "open": live_bar["open"], "high": live_bar["high"],
                              "low": live_bar["low"], "close": live_bar["close"], "volume": live_bar["volume"],
                              "provisional": True})
    bars = daily_display if timeframe == "D" else _weekly_chart_ohlc(daily_display)
    if not bars:
        return {"symbol": asset["symbol"], "currency": asset["currency"], "timeframe": timeframe,
                "bars": [], "indicators": [], "actions": [], "strategy_signals": [], "chart_signals": [], "profile": {},
                "source": source["provider"] if source else None,
                "last_bar_date": None, "error": source["last_error"] if source else "Henüz fiyat verisi yok."}
    calc = bekap_indicators.calculate(bars, asset["symbol"], asset["market"])
    confirmed_daily_calc = bekap_indicators.calculate(daily, asset["symbol"], asset["market"])
    confirmed_weekly = _weekly_ohlc(daily)
    weekly_calc = (calc if timeframe == "W" and not bars[-1].get("provisional")
                   else bekap_indicators.calculate(confirmed_weekly, asset["symbol"], asset["market"]))
    parameters = json.loads(asset["parameters_json"] or "{}")
    brake_pct = float(parameters.get("weekly_brake_drawdown_pct", DEFAULT_CONFIG["weekly_brake_drawdown_pct"]))
    strategy_signals = weekly_brake_events(daily, confirmed_daily_calc["rows"], confirmed_weekly,
                                           weekly_calc["rows"], brake_pct)
    chart_dates = {str(bar["date"])[:10] for bar in bars}
    for signal in strategy_signals:
        signal_day = date.fromisoformat(signal["signal_date"])
        signal["chart_date"] = (signal_day.isoformat() if timeframe == "D"
                                else (signal_day - timedelta(days=signal_day.weekday())).isoformat())
    own_timeframe = "1D" if timeframe == "D" else "1W"
    strategy_signals = [signal for signal in strategy_signals
                        if signal["timeframe"] == own_timeframe and signal["chart_date"] in chart_dates]
    closes = [float(r["close"]) for r in bars]
    rows = calc["rows"]
    current_week_provisional = timeframe == "W" and bool(bars[-1].get("provisional"))
    current_daily_provisional = timeframe == "D" and bool(bars[-1].get("provisional"))
    if current_week_provisional or current_daily_provisional:
        # Display live indicator values, but do not expose an unfinished bar as a confirmed signal.
        rows[-1].update(buy=False, tp=False, cross_up=False, cross_down=False)
        if timeframe == "D" and confirmed_daily_calc["rows"]:
            confirmed = confirmed_daily_calc["rows"][-1]
            for key in ("last_buy", "last_tp"):
                rows[-1][key] = confirmed.get(key)
            rows[-1]["buy_diff"] = rows[-1]["tp_diff"] = None
    for i, row in enumerate(rows):
        row["sma200"] = sum(closes[i - 199:i + 1]) / 200 if i >= 199 else None
        if i < 19:
            row.update(bb_mid=None, bb_upper=None, bb_lower=None)
            continue
        window = closes[i - 19:i + 1]
        middle = sum(window) / 20
        deviation = statistics.pstdev(window)
        row.update(bb_mid=middle, bb_upper=middle + 2 * deviation, bb_lower=middle - 2 * deviation)
    # Chart markers are period-native indicator events. Keep these separate from
    # strategy_signals, which are the brake-aware events used by the trade journal.
    chart_timeframe = "1D" if timeframe == "D" else "1W"
    chart_signals = []
    for bar, indicator in zip(bars, rows):
        for event, active, change_key in (
            ("buy_confirmed", indicator.get("buy"), "buy_diff"),
            ("take_profit_confirmed", indicator.get("tp"), "tp_diff"),
        ):
            if active:
                chart_signals.append({
                    "event": event, "timeframe": chart_timeframe,
                    "signal_date": str(bar["date"])[:10], "chart_date": str(bar["date"])[:10],
                    "close_price": float(bar["close"]), "rsi": indicator.get("rsi"),
                    "change_pct": indicator.get(change_key),
                })
    return {"symbol": asset["symbol"], "market": asset["market"], "currency": asset["currency"],
            "timeframe": timeframe, "bars": bars, "indicators": rows, "actions": actions,
            "strategy_signals": strategy_signals, "chart_signals": chart_signals,
            "profile": calc["profile"], "indicator_version": calc["version"],
            "source": source["provider"] if source else "—", "last_bar_date": bars[-1]["date"],
            "last_confirmed_bar_date": (confirmed_weekly[-1]["date"] if confirmed_weekly else bars[-1]["date"]),
            "last_daily_bar_date": daily_display[-1]["date"] if daily_display else None,
            "last_confirmed_daily_bar_date": daily[-1]["date"] if daily else None,
            "current_bar_provisional": current_week_provisional or current_daily_provisional,
            "current_daily_provisional": current_daily_provisional,
            "intraday_updated_at": live_bar["updated_at"] if live_bar and daily_display[-1].get("provisional") else None,
            "last_checked": source["last_checked"] if source else None, "as_of": now(),
            "confirmed_only": not current_week_provisional}


@app.get("/api/assets/{asset_id}/chart")
def chart_data(asset_id: int, timeframe: str = "D") -> dict[str, Any]:
    if timeframe not in ("D", "W"):
        raise HTTPException(400, "Grafik periyodu D veya W olmalı.")
    return build_chart_payload(asset_id, timeframe)


@app.get("/api/overview")
def overview() -> dict[str, Any]:
    with db() as con:
        assets = [dict(r) for r in con.execute("""SELECT a.*,m.provider,m.provider_symbol,m.status data_status,
            m.last_checked,m.last_bar_date,m.last_error,
            (SELECT b.close FROM bars b WHERE b.asset_id=a.id AND b.timeframe='1D' ORDER BY b.date DESC LIMIT 1) last_close,
            (SELECT b.close FROM bars b WHERE b.asset_id=a.id AND b.timeframe='1D' ORDER BY b.date DESC LIMIT 1 OFFSET 1) previous_close,
            (SELECT b.date FROM bars b WHERE b.asset_id=a.id AND b.timeframe='1D' ORDER BY b.date DESC LIMIT 1) price_date
            FROM assets a LEFT JOIN market_data_state m ON m.asset_id=a.id ORDER BY a.symbol""")]
        for asset in assets:
            previous = asset.get("previous_close")
            latest = asset.get("last_close")
            asset["day_change_pct"] = ((float(latest) / float(previous) - 1) * 100
                                       if latest is not None and previous not in (None, 0) else None)
            latest_signal = con.execute("""SELECT event,signal_date FROM signals
                WHERE asset_id=? AND is_current=1 AND strategy_version LIKE ?
                ORDER BY signal_date DESC,id DESC LIMIT 1""",
                (asset["id"], f"{WEEKLY_BRAKE_ENGINE_VERSION}:%")).fetchone()
            asset["last_signal"] = dict(latest_signal) if latest_signal else None
        recent = [dict(r) for r in con.execute("""SELECT s.*,a.symbol,a.role,a.currency FROM signals s
          JOIN assets a ON a.id=s.asset_id WHERE s.is_current=1 AND s.strategy_version LIKE ?
          ORDER BY s.signal_date DESC,s.id DESC LIMIT 30""", (f"{WEEKLY_BRAKE_ENGINE_VERSION}:%",))]
        settings = {r["key"]: json.loads(r["value_json"]) for r in con.execute("SELECT * FROM portfolio_settings")}
        bars = con.execute("SELECT COUNT(*) n FROM bars").fetchone()["n"]
        strategy_version = con.execute("SELECT version FROM strategy_versions ORDER BY id DESC LIMIT 1").fetchone()[0]
    return {"assets": assets, "signals": recent, "settings": settings, "bar_count": bars,
            "strategy_version": strategy_version, "data_status": "uploaded" if bars else "missing",
            "provider_configured": True, "provider_name": "Yahoo Finance daily chart feed",
            "forward_cutoff": "2027-01-02", "as_of": now()}


def search_yahoo_instruments(query: str) -> list[dict[str, Any]]:
    """Search Yahoo Finance for stocks and ETFs; results are cached briefly by the API route."""
    query = query.strip()
    if not query:
        return []
    params = urlencode({"q": query, "quotesCount": 12, "newsCount": 0, "enableFuzzyQuery": "true"})
    request = UrlRequest(
        f"https://query1.finance.yahoo.com/v1/finance/search?{params}",
        headers={"User-Agent": "Mozilla/5.0 (compatible; BEKAP-Sepet/1.0)"},
    )
    with urlopen(request, timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))
    results = []
    for quote_item in payload.get("quotes", []):
        kind = str(quote_item.get("quoteType") or "").upper()
        symbol = str(quote_item.get("symbol") or "").strip().upper()
        if kind not in {"EQUITY", "ETF"} or not symbol:
            continue
        exchange = str(quote_item.get("exchDisp") or quote_item.get("exchange") or "")
        exchange_upper = exchange.upper()
        currency = ("TRY" if any(token in exchange_upper for token in ("BIST", "ISTANBUL", "TURKEY", "IST")) or symbol.endswith(".IS") else
                    "CAD" if any(token in exchange_upper for token in ("TSX", "TORONTO")) or symbol.endswith(".TO") else
                    "USD" if any(token in exchange_upper for token in ("NASDAQ", "NYSE", "AMEX", "NMS", "NGM", "NCM", "PCX")) else None)
        results.append({"symbol": symbol, "name": quote_item.get("longname") or quote_item.get("shortname") or symbol,
                        "market": exchange, "currency": currency, "kind": "ETF" if kind == "ETF" else "Hisse"})
    return results


@app.get("/api/instruments/search")
def instrument_search(q: str = "") -> dict[str, Any]:
    query = q.strip()[:64]
    if len(query) < 1:
        return {"results": []}
    key = query.casefold()
    cached = _INSTRUMENT_SEARCH_CACHE.get(key)
    if cached and cached[0] > time.monotonic():
        return {"results": cached[1]}
    try:
        results = search_yahoo_instruments(query)
    except Exception as error:
        raise HTTPException(502, "Hisse ve ETF eşleşmeleri şu anda alınamadı.") from error
    _INSTRUMENT_SEARCH_CACHE[key] = (time.monotonic() + 300, results)
    return {"results": results}


def compute_universe_item(ticker: str, symbol: str = "", market: str = "") -> dict[str, Any] | None:
    """Fetch completed daily history for a universe ticker and compute its latest price and BEKAPRSI signal."""
    ticker = ticker.strip().upper()
    if not ticker:
        return None
    try:
        payload = fetch_yahoo_chart(ticker, "")
        values = payload.get("values") or []
        if not values:
            return None
        daily = [{"date": str(v["datetime"])[:10], "open": float(v["open"]), "high": float(v["high"]),
                  "low": float(v["low"]), "close": float(v["close"]),
                  "volume": float(v["volume"]) if v.get("volume") not in (None, "") else 0.0}
                 for v in values]
        last_close = daily[-1]["close"]
        prev_close = daily[-2]["close"] if len(daily) >= 2 else None
        day_change_pct = ((last_close / prev_close - 1.0) * 100.0
                          if prev_close not in (None, 0) else None)
        price_date = daily[-1]["date"]
        last_signal_event = None
        last_signal_date = None
        if len(daily) >= 16:
            base_sym = symbol.strip().upper() or (ticker[:-3] if ticker.endswith(".IS") else ticker)
            resolved_mkt = "XIST" if ticker.endswith(".IS") or market.upper() in ("TR", "BIST") else market
            daily_calc = bekap_indicators.calculate(daily, base_sym, resolved_mkt)
            weekly = _weekly_ohlc(daily)
            weekly_calc = bekap_indicators.calculate(weekly, base_sym, resolved_mkt)
            events = weekly_brake_events(daily, daily_calc["rows"], weekly, weekly_calc["rows"],
                                         float(DEFAULT_CONFIG["weekly_brake_drawdown_pct"]))
            if events:
                last_signal_event = events[-1]["event"]
                last_signal_date = events[-1]["signal_date"]
        return {
            "ticker": ticker,
            "last_close": last_close,
            "day_change_pct": day_change_pct,
            "price_date": price_date,
            "last_signal_event": last_signal_event,
            "last_signal_date": last_signal_date,
            "updated_at": now(),
        }
    except Exception:
        return None


def sync_universe_batch(items: list[dict[str, str]], force: bool = False) -> dict[str, dict[str, Any]]:
    with db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS universe_cache(
          ticker TEXT PRIMARY KEY, last_close REAL, day_change_pct REAL, price_date TEXT,
          last_signal_event TEXT, last_signal_date TEXT, updated_at TEXT NOT NULL)""")
        tickers = [str(item.get("ticker") or item.get("symbol") or "").strip().upper() for item in items]
        tickers = [t for t in tickers if t]
        if not tickers:
            return {}
        placeholders = ",".join("?" for _ in tickers)
        existing = {row["ticker"]: dict(row) for row in con.execute(
            f"SELECT * FROM universe_cache WHERE ticker IN ({placeholders})", tuple(tickers))}
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat(timespec="seconds")
    to_fetch = []
    results: dict[str, dict[str, Any]] = {}
    for item in items:
        t = str(item.get("ticker") or item.get("symbol") or "").strip().upper()
        if not t:
            continue
        cached = existing.get(t)
        if cached and not force and str(cached.get("updated_at") or "") >= cutoff and cached.get("price_date"):
            results[t] = cached
        else:
            to_fetch.append(item)
    if to_fetch:
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(
                compute_universe_item,
                str(it.get("ticker") or it.get("symbol") or ""),
                str(it.get("symbol") or ""),
                str(it.get("market") or ""),
            ) for it in to_fetch[:35]]
            fetched = [f.result() for f in futures]
        valid = [r for r in fetched if r]
        if valid:
            with db() as con:
                for r in valid:
                    con.execute("""INSERT OR REPLACE INTO universe_cache
                        (ticker,last_close,day_change_pct,price_date,last_signal_event,last_signal_date,updated_at)
                        VALUES(?,?,?,?,?,?,?)""",
                        (r["ticker"], r["last_close"], r["day_change_pct"], r["price_date"],
                         r["last_signal_event"], r["last_signal_date"], r["updated_at"]))
                    results[r["ticker"]] = r
    return results


@app.post("/api/universe/batch")
async def universe_batch(request: Request) -> dict[str, Any]:
    body = await request.json()
    items = body.get("items") or []
    force = bool(body.get("force", False))
    if not isinstance(items, list):
        raise HTTPException(400, "items listesi gerekli.")
    data = await asyncio.to_thread(sync_universe_batch, items[:35], force)
    return {"items": data}


def logo_response_fallback(symbol: str) -> Response:
    initials = "".join(part[0] for part in symbol.replace(".", " ").replace("-", " ").split()[:2]) or symbol[:2]
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
           f'<rect width="100" height="100" rx="22" fill="#172b40"/>'
           f'<text x="50" y="60" text-anchor="middle" font-family="Arial,sans-serif" '
           f'font-size="34" font-weight="700" fill="#9bd1ff">{initials}</text></svg>')
    return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "no-store"})


@app.get("/api/logos/{symbol}")
def instrument_logo(symbol: str, background_tasks: BackgroundTasks, market: str = "") -> Response:
    """Serve cached PNGs immediately and download missing logos after the response."""
    symbol = symbol.strip().upper()
    if not symbol or len(symbol) > 32 or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.^_-" for char in symbol):
        raise HTTPException(400, "Geçersiz sembol.")
    safe_key = symbol.replace("/", "_")
    LOGO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached_path = LOGO_CACHE_DIR / f"{safe_key}.png"
    miss_path = LOGO_CACHE_DIR / f"{safe_key}.missing"
    with _LOGO_CACHE_LOCK:
        if cached_path.is_file():
            return FileResponse(cached_path, media_type="image/png", headers={"Cache-Control": "public, max-age=2592000"})
        if miss_path.exists() and time.time() - miss_path.stat().st_mtime < 86400:
            return logo_response_fallback(symbol)
        if symbol not in _LOGO_FETCH_PENDING:
            _LOGO_FETCH_PENDING.add(symbol)
            background_tasks.add_task(fetch_and_cache_logo, symbol, market)
    return logo_response_fallback(symbol)


def fetch_and_cache_logo(symbol: str, market: str) -> None:
    """Fetch a missing logo once into the local, persistent logo library."""
    safe_key = symbol.replace("/", "_")
    cached_path = LOGO_CACHE_DIR / f"{safe_key}.png"
    miss_path = LOGO_CACHE_DIR / f"{safe_key}.missing"
    with _LOGO_CACHE_LOCK:
        if cached_path.is_file() or (miss_path.exists() and time.time() - miss_path.stat().st_mtime < 86400):
            _LOGO_FETCH_PENDING.discard(symbol)
            return
    try:
        with db() as con:
            asset = con.execute("SELECT market FROM assets WHERE symbol=? LIMIT 1", (symbol,)).fetchone()
        resolved_market = market or (asset["market"] if asset else "")
        logo_symbol = yahoo_ticker(symbol, resolved_market)
        url = f"https://assets.parqet.com/logos/symbol/{quote(logo_symbol, safe='.^_-')}?format=png"
        request = UrlRequest(url, headers={"User-Agent": "Mozilla/5.0 (compatible; BEKAP-Sepet/1.0)"})
        with urlopen(request, timeout=2.5) as response:
            content = response.read(2_000_001)
            if (response.headers.get_content_type() != "image/png" or not content.startswith(b"\x89PNG\r\n\x1a\n")
                    or len(content) > 2_000_000):
                raise ValueError("Logo yanıtı geçerli PNG değil.")
        LOGO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        temp_path = cached_path.with_suffix(".tmp")
        temp_path.write_bytes(content)
        temp_path.replace(cached_path)
        miss_path.unlink(missing_ok=True)
    except Exception:
        LOGO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        miss_path.touch()
    finally:
        with _LOGO_CACHE_LOCK:
            _LOGO_FETCH_PENDING.discard(symbol)


@app.post("/api/data/refresh")
async def refresh_data() -> dict[str, Any]:
    return await asyncio.to_thread(sync_market_data)


@app.post("/api/assets")
async def add_asset(request: Request) -> dict[str, Any]:
    p = await request.json()
    symbol = str(p.get("symbol", "")).strip().upper()
    if not symbol or len(symbol) > 32:
        raise HTTPException(400, "Geçerli bir sembol gerekli.")
    role = p.get("role", "lifetime")
    if role not in ("lifetime", "trend", "periodic"):
        raise HTTPException(400, "Rol lifetime, trend veya periodic olmalı.")
    market, currency = str(p.get("market", "" )).strip(), str(p.get("currency", "USD")).strip().upper()
    try:
        target, maximum = float(p.get("target_weight", 0)), float(p.get("max_weight", 0))
    except (TypeError, ValueError):
        raise HTTPException(400, "Ağırlıklar sayısal olmalı.")
    if not market or not currency or not (0 <= target <= 1 and 0 <= maximum <= 1 and target <= maximum):
        raise HTTPException(400, "Piyasa/para birimi gerekli; ağırlıklar 0-1 aralığında ve hedef <= üst sınır olmalı.")
    try:
        with db() as con:
            params = p.get("parameters", {})
            if not isinstance(params, dict): raise HTTPException(400, "parameters nesne biçiminde olmalı.")
            params = {"rsi_period": int(params.get("rsi_period", 14)),
                      "buy_zone_max": float(params.get("buy_zone_max", 35)),
                      "weekly_brake_drawdown_pct": float(params.get("weekly_brake_drawdown_pct", 21))}
            if not 2 <= params["rsi_period"] <= 50 or not 1 <= params["buy_zone_max"] <= 60 or not 1 <= params["weekly_brake_drawdown_pct"] <= 80:
                raise HTTPException(400, "RSI 2-50, alım bölgesi 1-60 ve haftalık fren 1-80 aralığında olmalı.")
            cur = con.execute("INSERT INTO assets(symbol,market,role,currency,target_weight,max_weight,tv_symbol,parameters_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                              (symbol, market, role, currency, target, maximum,
                               str(p.get("tv_symbol", "")).strip(), json.dumps(params), now()))
            provider_symbol = yahoo_ticker(symbol, market)
            con.execute("""INSERT OR IGNORE INTO market_data_state(asset_id,provider,provider_symbol,provider_exchange)
                VALUES(?,?,?,?)""", (cur.lastrowid, "Yahoo Finance", provider_symbol, ""))
            return dict(con.execute("SELECT * FROM assets WHERE id=?", (cur.lastrowid,)).fetchone())
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Bu sembol sepette zaten var.")


@app.delete("/api/assets/{asset_id}")
def remove_asset(asset_id: int) -> dict[str, bool]:
    with db() as con:
        cur = con.execute("DELETE FROM assets WHERE id=?", (asset_id,))
        if not cur.rowcount:
            raise HTTPException(404, "Varlık bulunamadı.")
    return {"deleted": True}


@app.put("/api/settings")
async def update_settings(request: Request) -> dict[str, Any]:
    p = await request.json()
    allowed = {"initial_capital_usd": float, "monthly_contribution_usd": float,
               "fees_bps": float, "slippage_bps": float, "forward_start": str}
    with db() as con:
        for key, typ in allowed.items():
            if key in p:
                try: value = typ(p[key])
                except (ValueError, TypeError): raise HTTPException(400, f"{key} geçersiz")
                con.execute("INSERT OR REPLACE INTO portfolio_settings(key,value_json) VALUES(?,?)", (key, json.dumps(value)))
        ver = f"0.1.{con.execute('SELECT COUNT(*) FROM strategy_versions').fetchone()[0]}"
        previous = dict(con.execute("SELECT key,value_json FROM portfolio_settings"))
        snapshot = {**DEFAULT_CONFIG, **{k: json.loads(v) for k,v in previous.items()}}
        con.execute("INSERT OR IGNORE INTO strategy_versions(created_at,version,config_json,note) VALUES(?,?,?,?)",
                    (now(), ver, json.dumps(snapshot), "Portfolio and strategy settings snapshot"))
    return {"saved": True, "strategy_version": ver}


def rsi_wilder(closes: list[float], period: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= period: return out
    changes = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [max(c, 0) for c in changes]; losses = [max(-c, 0) for c in changes]
    ag, al = sum(gains[:period])/period, sum(losses[:period])/period
    def value(g: float, l: float) -> float:
        return 100.0 if l == 0 else 100 - 100/(1+g/l)
    out[period] = value(ag, al)
    for i in range(period+1, len(closes)):
        ag = (ag*(period-1)+gains[i-1])/period
        al = (al*(period-1)+losses[i-1])/period
        out[i] = value(ag, al)
    return out


def weekly_brake_events(daily_bars: list[dict[str, Any]], daily_rows: list[dict[str, Any]],
                        weekly_bars: list[dict[str, Any]], weekly_rows: list[dict[str, Any]],
                        drawdown_pct: float = 21.0,
                        include_suppressed: bool = False) -> list[dict[str, Any]]:
    """Apply the requested mode switch: weekly AL during brake, daily SAT always.

    The brake is confirmed on a completed daily close at or below the configured
    drawdown from the latest daily SAT close. It ends on a subsequent daily SAT.
    """
    if drawdown_pct <= 0 or drawdown_pct >= 100:
        raise ValueError("weekly brake drawdown must be between 0 and 100 percent")
    daily_by_date = {str(row["date"]): row for row in daily_rows}
    weekly_by_week: dict[tuple[int, int], dict[str, Any]] = {}
    for bar, row in zip(weekly_bars, weekly_rows):
        day = date.fromisoformat(str(bar["date"])[:10])
        weekly_by_week[day.isocalendar()[:2]] = {**row, "source_date": str(bar["date"])[:10],
                                                "close": float(bar["close"])}
    daily_week_ends: dict[tuple[int, int], str] = {}
    for bar in daily_bars:
        day = date.fromisoformat(str(bar["date"])[:10])
        daily_week_ends[day.isocalendar()[:2]] = str(bar["date"])[:10]

    events: list[dict[str, Any]] = []
    brake_active = False
    last_daily_sat_price: float | None = None
    for bar in daily_bars:
        signal_date = str(bar["date"])[:10]
        day = date.fromisoformat(signal_date)
        key = day.isocalendar()[:2]
        indicator = daily_by_date.get(signal_date, {})
        close = float(bar["close"])
        if (not brake_active and last_daily_sat_price is not None
                and close <= last_daily_sat_price * (1.0 - drawdown_pct / 100.0)):
            brake_active = True

        if indicator.get("buy"):
            if not brake_active:
                events.append({"event": "buy_confirmed", "timeframe": "1D", "signal_date": signal_date,
                               "close_price": close, "rsi": indicator.get("rsi"),
                               "change_pct": indicator.get("buy_diff"), "brake_active": False})
            elif include_suppressed:
                events.append({"event": "buy_confirmed", "timeframe": "1D", "signal_date": signal_date,
                               "close_price": close, "rsi": indicator.get("rsi"),
                               "change_pct": indicator.get("buy_diff"), "brake_active": True,
                               "brake_suppressed": True})

        daily_sat = bool(indicator.get("tp"))
        if daily_sat:
            events.append({"event": "take_profit_confirmed", "timeframe": "1D", "signal_date": signal_date,
                           "close_price": close, "rsi": indicator.get("rsi"),
                           "change_pct": indicator.get("tp_diff"), "brake_active": brake_active})
            last_daily_sat_price = close
            brake_active = False

        weekly = weekly_by_week.get(key)
        if (weekly and daily_week_ends.get(key) == signal_date and weekly.get("buy") and brake_active):
            events.append({"event": "buy_confirmed", "timeframe": "1W",
                           "signal_date": weekly["source_date"], "close_price": float(weekly.get("close", close)),
                           "rsi": weekly.get("rsi"), "change_pct": weekly.get("buy_diff"),
                           "brake_active": True})
    return events


def run_signals(asset_id: int, timeframe: str = "1D", notify_after: str | None = None) -> dict[str, Any]:
    with db() as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM bars WHERE asset_id=? AND timeframe=? ORDER BY date", (asset_id, timeframe)).fetchall()]
        asset = con.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
        if not asset: raise HTTPException(404, "Varlık bulunamadı.")
        if len(rows) < 16: return {"created": 0, "message": "BEKAPRSI AUTO için en az 16 kapanmış mum gerekli."}
        calc = bekap_indicators.calculate(rows, asset["symbol"], asset["market"])
        profile = calc["profile"]
        strategy_version = f"{calc['version']}:{profile['ticker']}:{hashlib.sha256(json.dumps(profile,sort_keys=True).encode()).hexdigest()[:8]}"
        n = 0
        for i, indicator in enumerate(calc["rows"]):
            events = []
            if indicator["buy"]: events.append(("buy_confirmed", "EKLE"))
            if indicator["tp"]: events.append(("take_profit_confirmed", "KAR AL"))
            for event, label in events:
                close = float(rows[i]["close"])
                fmt = lambda value: f"{float(value):.2f}".replace(".", ",")
                if label == "EKLE":
                    reason = (f"BEKAPRSI alım teyidi: RSI dönüşü; alım eşiği {fmt(profile['buy'])}, "
                              f"son alımdan düşüş koşulu %{fmt(profile['drop']*100)}, filtre {profile['filter']}.")
                else:
                    reason = (f"BEKAPRSI kâr-al teyidi: RSI tetikleyicisi {fmt(profile['tp'])}; "
                              f"yüksek RSI eşiği {fmt(profile['overbought'])}, yükseliş koşulu %{fmt(profile['rise']*100)}.")
                execution = rows[i+1] if i+1 < len(rows) else None
                key = hashlib.sha256(f"{asset['symbol']}|{timeframe}|{rows[i]['date']}|{event}|{strategy_version}".encode()).hexdigest()
                payload = {"symbol": asset["symbol"], "event": event, "timeframe": timeframe,
                           "signal_date": rows[i]["date"], "close": close, "rsi": indicator["rsi"],
                           "reason": reason, "strategy_version": strategy_version,
                           "profile": profile, "indicator_version": calc["version"]}
                cur = con.execute("""INSERT OR IGNORE INTO signals(event_key,asset_id,event,timeframe,signal_date,confirmed_at,
                   close_price,execution_date,execution_price,rsi,reason,strategy_version,payload_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (key, asset_id, event, timeframe, rows[i]["date"], now(), close,
                    execution["date"] if execution else None, float(execution["open"]) if execution else None,
                    indicator["rsi"], reason, strategy_version, json.dumps(payload, ensure_ascii=False)))
                if cur.rowcount:
                    sid = cur.lastrowid
                    # Historical backfill belongs in the journal, never in the live Telegram queue.
                    if notify_after and rows[i]["date"] > notify_after:
                        con.execute("INSERT OR IGNORE INTO outbox(event_key,signal_id,created_at) VALUES(?,?,?)", (key, sid, now()))
                    n += 1
    return {"created": n, "message": "Kapanmış mumlar BEKAPRSI AUTO kurallarıyla işlendi; aynı olay yinelenmez."}


def run_weekly_brake_signals(asset_id: int, notify_after_daily: str | None = None,
                             notify_after_weekly: str | None = None) -> dict[str, Any]:
    with db() as con:
        asset = con.execute("SELECT * FROM assets WHERE id=? AND enabled=1", (asset_id,)).fetchone()
        if not asset:
            raise HTTPException(404, "Varlık bulunamadı.")
        daily_bars = [dict(row) for row in con.execute(
            "SELECT date,open,high,low,close,volume FROM bars WHERE asset_id=? AND timeframe='1D' ORDER BY date", (asset_id,))]
        weekly_bars = [dict(row) for row in con.execute(
            "SELECT date,open,high,low,close,volume FROM bars WHERE asset_id=? AND timeframe='1W' ORDER BY date", (asset_id,))]

    daily_calc = bekap_indicators.calculate(daily_bars, asset["symbol"], asset["market"])
    weekly_calc = bekap_indicators.calculate(weekly_bars, asset["symbol"], asset["market"])
    profile = daily_calc["profile"]
    parameters = json.loads(asset["parameters_json"] or "{}")
    drawdown_pct = float(parameters.get("weekly_brake_drawdown_pct", DEFAULT_CONFIG["weekly_brake_drawdown_pct"]))
    events = weekly_brake_events(daily_bars, daily_calc["rows"], weekly_bars, weekly_calc["rows"], drawdown_pct)
    fingerprint = hashlib.sha256(json.dumps(
        {"profile": profile, "weekly_brake_drawdown_pct": drawdown_pct,
         "engine": WEEKLY_BRAKE_ENGINE_VERSION}, sort_keys=True).encode()).hexdigest()[:10]
    strategy_version = f"{WEEKLY_BRAKE_ENGINE_VERSION}:{daily_calc['version']}:{profile['ticker']}:{fingerprint}"
    bars_by_timeframe = {"1D": daily_bars, "1W": weekly_bars}
    created = 0

    with db() as con:
        existing = {row["event_key"] for row in con.execute(
            "SELECT event_key FROM signals WHERE asset_id=? AND strategy_version LIKE ?",
            (asset_id, f"{WEEKLY_BRAKE_ENGINE_VERSION}:%"))}
        con.execute("UPDATE signals SET is_current=0 WHERE asset_id=? AND strategy_version LIKE ?",
                    (asset_id, f"{WEEKLY_BRAKE_ENGINE_VERSION}:%"))
        desired_keys = set()
        for event in events:
            timeframe = event["timeframe"]
            series = bars_by_timeframe[timeframe]
            signal_date = event["signal_date"]
            index = next((i for i, bar in enumerate(series) if str(bar["date"])[:10] == signal_date), None)
            execution = series[index + 1] if index is not None and index + 1 < len(series) else None
            event_key = hashlib.sha256(
                f"{asset['symbol']}|{timeframe}|{signal_date}|{event['event']}|{strategy_version}".encode()).hexdigest()
            desired_keys.add(event_key)
            fmt = lambda value: f"{float(value):.2f}".replace(".", ",") if value is not None else "—"
            if event["event"] == "buy_confirmed":
                reason = (f"BEKAPRSI alım teyidi: RSI dönüşü; "
                          f"{'haftalık fren modu' if timeframe == '1W' else 'günlük mod'}; "
                          f"fren eşiği %{fmt(drawdown_pct)} günlük kapanış düşüşü.")
            else:
                reason = ("BEKAPRSI kâr-al teyidi: günlük SAT; haftalık fren sona erer."
                          if event["brake_active"] else "BEKAPRSI kâr-al teyidi: günlük SAT.")
            payload = {"symbol": asset["symbol"], **event, "reason": reason,
                       "strategy_version": strategy_version, "profile": profile,
                       "weekly_brake_drawdown_pct": drawdown_pct,
                       "indicator_version": daily_calc["version"]}
            con.execute("""INSERT INTO signals(event_key,asset_id,event,timeframe,signal_date,confirmed_at,
                close_price,execution_date,execution_price,rsi,reason,strategy_version,payload_json,is_current)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,1)
                ON CONFLICT(event_key) DO UPDATE SET close_price=excluded.close_price,
                execution_date=excluded.execution_date,execution_price=excluded.execution_price,
                rsi=excluded.rsi,reason=excluded.reason,payload_json=excluded.payload_json,is_current=1""",
                (event_key, asset_id, event["event"], timeframe, signal_date, now(), event["close_price"],
                 execution["date"] if execution else None, float(execution["open"]) if execution else None,
                 event["rsi"], reason, strategy_version, json.dumps(payload, ensure_ascii=False)))
            if event_key not in existing:
                created += 1
            notify_after = notify_after_daily if timeframe == "1D" else notify_after_weekly
            if notify_after and signal_date > notify_after:
                signal_id = con.execute("SELECT id FROM signals WHERE event_key=?", (event_key,)).fetchone()[0]
                con.execute("INSERT OR IGNORE INTO outbox(event_key,signal_id,created_at) VALUES(?,?,?)",
                            (event_key, signal_id, now()))

        stale_keys = existing - desired_keys
        for key in stale_keys:
            con.execute("DELETE FROM outbox WHERE event_key=? AND status='pending'", (key,))

    return {"created": created, "message": "Haftalık frenli AL / günlük SAT kuralları uygulandı.",
            "weekly_brake_drawdown_pct": drawdown_pct, "signals": len(events)}


@app.post("/api/assets/{asset_id}/bars")
async def upload_bars(asset_id: int, request: Request, timeframe: str = "1D") -> dict[str, Any]:
    if timeframe not in ("1D", "1W"): raise HTTPException(400, "timeframe 1D veya 1W olmalı")
    body = await request.body()
    if len(body) > 12_000_000: raise HTTPException(413, "CSV en fazla 12 MB olabilir.")
    try:
        reader = csv.DictReader(io.StringIO(body.decode("utf-8-sig")))
        required = {"date", "open", "high", "low", "close"}
        if not required.issubset({x.strip().lower() for x in (reader.fieldnames or [])}):
            raise ValueError("CSV başlıkları: date,open,high,low,close[,volume]")
        count = 0
        with db() as con:
            if not con.execute("SELECT 1 FROM assets WHERE id=?", (asset_id,)).fetchone(): raise HTTPException(404, "Varlık bulunamadı.")
            prior_date = con.execute("SELECT MAX(date) FROM bars WHERE asset_id=? AND timeframe=?", (asset_id, timeframe)).fetchone()[0]
            prior_week_date = con.execute("SELECT MAX(date) FROM bars WHERE asset_id=? AND timeframe='1W'", (asset_id,)).fetchone()[0]
            for raw in reader:
                r = {k.strip().lower(): v for k,v in raw.items() if k}
                d = date.fromisoformat(r["date"].strip())
                o,h,l,c = [float(r[k]) for k in ("open","high","low","close")]
                if min(o,h,l,c) <= 0 or h < max(o,l,c) or l > min(o,h,c): raise ValueError(f"OHLC tutarsız: {d}")
                v = float(r["volume"]) if r.get("volume", "").strip() else None
                con.execute("INSERT OR REPLACE INTO bars(asset_id,timeframe,date,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?,?)",
                            (asset_id,timeframe,d.isoformat(),o,h,l,c,v)); count += 1
        if timeframe == "1D":
            rebuild_weekly_bars(asset_id)
        result = run_weekly_brake_signals(
            asset_id,
            notify_after_daily=prior_date if timeframe == "1D" else None,
            notify_after_weekly=prior_week_date)
        return {"rows_imported": count, **result}
    except HTTPException: raise
    except Exception as e: raise HTTPException(400, f"CSV okunamadı: {e}")


@app.get("/api/assets/{asset_id}/signals")
def asset_signals(asset_id: int, include_suppressed: bool = False) -> list[dict[str, Any]]:
    with db() as con:
        asset = con.execute("SELECT symbol,market,role,currency,parameters_json FROM assets WHERE id=?", (asset_id,)).fetchone()
        if not asset:
            raise HTTPException(404, "Varlık bulunamadı.")
        signals = [dict(r) for r in con.execute(
            "SELECT * FROM signals WHERE asset_id=? AND is_current=1 AND strategy_version LIKE ? ORDER BY signal_date DESC,id DESC",
            (asset_id, f"{WEEKLY_BRAKE_ENGINE_VERSION}:%"))]
        bars = {timeframe: [dict(r) for r in con.execute(
            "SELECT date,open,high,low,close,volume FROM bars WHERE asset_id=? AND timeframe=? ORDER BY date",
            (asset_id, timeframe))] for timeframe in ("1D", "1W")}
    adx_by_timeframe: dict[str, dict[str, float | None]] = {}
    calculated_by_timeframe: dict[str, list[dict[str, Any]]] = {}
    for timeframe, values in bars.items():
        if len(values) >= 16:
            calculated = bekap_indicators.calculate(values, asset["symbol"], asset["market"])["rows"]
            calculated_by_timeframe[timeframe] = calculated
            adx_by_timeframe[timeframe] = {row["date"]: row["adx"] for row in calculated}
    for signal in signals:
        signal["symbol"] = asset["symbol"]
        signal["role"] = asset["role"]
        signal["currency"] = asset["currency"]
        signal["adx"] = adx_by_timeframe.get(signal["timeframe"], {}).get(signal["signal_date"])
        signal["brake_suppressed"] = False
    if include_suppressed and len(bars["1D"]) >= 16:
        parameters = json.loads(asset["parameters_json"] or "{}")
        drawdown_pct = float(parameters.get("weekly_brake_drawdown_pct", DEFAULT_CONFIG["weekly_brake_drawdown_pct"]))
        seen = {(s["signal_date"], s["timeframe"], s["event"]) for s in signals}
        brake_events = weekly_brake_events(
            bars["1D"], calculated_by_timeframe["1D"],
            bars["1W"], calculated_by_timeframe.get("1W", []),
            drawdown_pct, include_suppressed=True,
        )
        for ev in brake_events:
            if not ev.get("brake_suppressed"):
                continue
            key = (ev["signal_date"], ev["timeframe"], ev["event"])
            if key in seen:
                continue
            seen.add(key)
            signals.append({
                "id": 0,
                "asset_id": asset_id,
                "event": ev["event"],
                "timeframe": ev["timeframe"],
                "signal_date": ev["signal_date"],
                "close_price": ev["close_price"],
                "rsi": ev["rsi"],
                "symbol": asset["symbol"],
                "role": asset["role"],
                "currency": asset["currency"],
                "adx": adx_by_timeframe.get(ev["timeframe"], {}).get(ev["signal_date"]),
                "brake_active": True,
                "brake_suppressed": True,
            })
        signals.sort(key=lambda s: (str(s["signal_date"]), int(s.get("id") or 0)), reverse=True)
    return signals


@app.get("/api/telegram/preview")
def telegram_preview() -> dict[str, Any]:
    with db() as con:
        row = con.execute("SELECT s.*,a.symbol,a.currency FROM signals s JOIN assets a ON a.id=s.asset_id "
                           "WHERE s.is_current=1 AND s.strategy_version LIKE ? ORDER BY s.id DESC LIMIT 1",
                           (f"{WEEKLY_BRAKE_ENGINE_VERSION}:%",)).fetchone()
    if not row: return {"configured": False, "message": "Henüz sinyal yok. Telegram gönderimi bot token/chat id ortam değişkenleri ayarlanana kadar kapalı.", "preview": None}
    r = dict(row)
    number = lambda value: f"{float(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if value is not None else "—"
    date_parts = str(r['signal_date']).split("-")
    signal_date = f"{date_parts[2]}.{date_parts[1]}.{date_parts[0]}" if len(date_parts) == 3 else str(r['signal_date'])
    event = {"buy_confirmed": "ALIM teyidi", "take_profit_confirmed": "KÂR AL teyidi", "full_exit": "Tam çıkış"}.get(r['event'], r['event'])
    timeframe = {"1D": "Günlük", "D": "Günlük", "1W": "Haftalık", "W": "Haftalık"}.get(r['timeframe'], r['timeframe'])
    execution = number(r['execution_price']) if r['execution_price'] is not None else "Sonraki seans bekleniyor"
    msg = (f"{r['symbol']} · {event}\nZaman aralığı: {timeframe}\n"
           f"Sinyal fiyatı: {number(r['close_price'])} {r['currency']} · Tahmini işlem fiyatı: {execution}\n"
           f"Göreli güç endeksi (RSI): {number(r['rsi'])}\nSinyal nedeni: {r['reason']}\n"
           f"Veri tarihi: {signal_date} · Kural sürümü: {r['strategy_version']}\n"
           f"Grafik: {os.environ.get('BEKAP_PUBLIC_BASE_URL','http://127.0.0.1:8767')}/?asset={r['asset_id']}")
    return {"configured": bool(os.environ.get("BEKAP_TELEGRAM_BOT_TOKEN") and os.environ.get("BEKAP_TELEGRAM_CHAT_ID")), "preview": msg,
            "delivery": "disabled until explicit deployment configuration"}


@app.get("/api/backtest/status")
def backtest_status() -> dict[str, Any]:
    with db() as con:
        n = con.execute("SELECT COUNT(*) FROM bars").fetchone()[0]
        start = con.execute("SELECT MIN(date) FROM bars").fetchone()[0]
        end = con.execute("SELECT MAX(date) FROM bars").fetchone()[0]
    return {"implemented": False, "bars_uploaded": n, "data_range": [start,end],
            "message": "Muhasebe doğru sonuç üretmek için sepet, kur, temettü/bölünme ve maliyet sözleşmesi bekliyor. Henüz getiri hesaplanmıyor.",
            "history_window_years": 5, "forward_cutoff": "2027-01-02", "as_of": date.today().isoformat()}


@app.get("/api/portfolio")
def api_portfolio_get() -> dict[str, Any]:
    with db() as con:
        return portfolio_engine.compute_portfolio_data(con)


@app.post("/api/portfolio/trade")
async def api_portfolio_trade_create(request: Request) -> dict[str, Any]:
    body = await request.json()
    symbol = body.get("symbol", "").strip()
    trade_date = body.get("date") or body.get("trade_date") or date.today().isoformat()
    side = body.get("side", "BUY").strip()
    try:
        price = float(body.get("price", 0))
        quantity = float(body.get("quantity", 0))
    except (ValueError, TypeError):
        raise HTTPException(400, "Geçersiz fiyat veya adet.")
    if not symbol or price <= 0 or quantity <= 0:
        raise HTTPException(400, "Sembol, pozitif fiyat ve adet zorunludur.")
    notes = str(body.get("notes") or "").strip()
    currency = body.get("currency")
    with db() as con:
        try:
            trade = portfolio_engine.record_trade(con, symbol, trade_date, side, price, quantity, notes, currency=currency)
        except ValueError as err:
            raise HTTPException(400, str(err)) from err
    return {"status": "ok", "trade": trade}


@app.put("/api/portfolio/trade/{trade_id}")
async def api_portfolio_trade_update(trade_id: int, request: Request) -> dict[str, Any]:
    body = await request.json()
    symbol = body.get("symbol", "").strip()
    trade_date = body.get("date") or body.get("trade_date") or date.today().isoformat()
    side = body.get("side", "BUY").strip()
    try:
        price = float(body.get("price", 0))
        quantity = float(body.get("quantity", 0))
    except (ValueError, TypeError):
        raise HTTPException(400, "Geçersiz fiyat veya adet.")
    if not symbol or price <= 0 or quantity <= 0:
        raise HTTPException(400, "Sembol, pozitif fiyat ve adet zorunludur.")
    notes = str(body.get("notes") or "").strip()
    currency = body.get("currency")
    with db() as con:
        try:
            updated = portfolio_engine.update_trade(con, trade_id, symbol, trade_date, side, price, quantity, notes, currency=currency)
        except ValueError as err:
            raise HTTPException(400, str(err)) from err
        if not updated:
            raise HTTPException(404, "İşlem kaydı bulunamadı.")
    return {"status": "ok", "trade": updated}


@app.delete("/api/portfolio/trade/{trade_id}")
def api_portfolio_trade_delete(trade_id: int) -> dict[str, Any]:
    with db() as con:
        try:
            deleted = portfolio_engine.delete_trade(con, trade_id)
        except ValueError as err:
            raise HTTPException(400, str(err)) from err
        if not deleted:
            raise HTTPException(404, "İşlem kaydı bulunamadı.")
    return {"status": "ok", "deleted": True}

