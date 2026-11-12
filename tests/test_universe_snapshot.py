import json
from pathlib import Path


SNAPSHOT = Path(__file__).parents[1] / "static" / "universe_snapshot.json"


def test_fixed_universe_snapshot_contains_1491_unique_instruments():
    payload = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    rows = payload["rows"]

    assert payload["total"] == 1491
    assert len(rows) == 1491
    assert len({row["id"] for row in rows}) == 1491
    assert len({row["symbol"].casefold() for row in rows}) == 1491
    assert len({row["yahoo_symbol"].casefold() for row in rows}) == 1491
    assert sum(1 for row in rows if row.get("instrument_type", "stock") == "stock") == 1179
    assert sum(1 for row in rows if row.get("instrument_type") == "etf") == 312


def test_universe_snapshot_preserves_source_table_fields_and_raw_records():
    payload = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    required = {
        "id", "symbol", "yahoo_symbol", "market", "currency", "source_class",
        "source_rate", "pdf_page", "pdf_index", "name", "sector", "quote",
        "quote_status", "expected_session", "published_class", "source_checked_at",
    }

    assert payload["version"] == "8765-2026-11-06+bekap-etf-312"
    assert all(required <= row.keys() for row in payload["rows"])
    assert {row["market"] for row in payload["rows"]} == {"US", "DE", "TR"}
    assert {row["source_class"] for row in payload["rows"] if row.get("instrument_type") == "etf"} == {"life", "trend", "seasonal"}


def test_universe_table_uses_the_previously_selected_headings_and_logo_cell():
    root = Path(__file__).parents[1]
    html = (root / "static" / "index.html").read_text(encoding="utf-8")
    app_js = (root / "static" / "app.js").read_text(encoding="utf-8")
    expected = (
        "Varlık", "Fiyat", "Günlük değişim", "Rol", "Hedef / üst ağırlık",
        "Son AL/SAT", "Sinyal tarihi", "Veri tarihi",
    )
    start = html.index('<table class="basket-table universe-table">')
    end = html.index("</thead>", start)
    heading_row = html[start:end]

    assert all(f"<th>{label}</th>" in heading_row for label in expected)
    assert tuple(heading_row.index(f"<th>{label}</th>") for label in expected) == tuple(
        sorted(heading_row.index(f"<th>{label}</th>") for label in expected)
    )
    assert "universeLogoHTML(row)" in app_js
    assert "loading=\"lazy\"" in app_js
