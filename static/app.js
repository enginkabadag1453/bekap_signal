const $ = s => document.querySelector(s);
const roleTR = { lifetime: 'Ömürlük', trend: 'Trend', periodic: 'Dönemsel' };
const universeClassTR = { life: 'Ömürlük', trend: 'Trend', seasonal: 'Dönemsel' };
const universeClassToRole = { life: 'lifetime', trend: 'trend', seasonal: 'periodic' };
const universeMarketDefault = { TR: 'BIST', DE: 'XETRA', US: 'NASDAQ' };
let signalTableRows = [];
let portfolioAssets = [];
let basketSort = { key: 'symbol', direction: 1 };
let searchTimer = null;
let searchRequestId = 0;
let universeSourceRows = [];
let universePage = 1;
let universeType = '';
let universeCategory = '';
const universePageSize = 30;
const universeLiveCache = new Map();
let universeBatchRequestId = 0;

async function api(url, opts = {}) {
  const response = await fetch(url, opts);
  const data = await response.json();
  if (!response.ok) throw Error(data.detail || 'İşlem tamamlanamadı.');
  return data;
}

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}
const numberTR = value => value == null || !Number.isFinite(Number(value)) ? '—' : Number(value).toLocaleString('tr-TR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const parseTR = value => {
  if (typeof value === 'number') return value;
  const text = String(value ?? '').trim().replace(/\s/g, '');
  if (!text) return 0;
  const normalized = text.includes(',') ? text.replace(/\./g, '').replace(',', '.') : text;
  const result = Number(normalized);
  if (!Number.isFinite(result)) throw Error(`Geçersiz sayı biçimi: ${value}`);
  return result;
};
const dateTR = value => {
  const match = String(value ?? '').match(/^(\d{4})-(\d{2})-(\d{2})/);
  return match ? `${match[3]}.${match[2]}.${match[1]}` : (value || '—');
};
const dateTimeTR = value => {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? '—' : new Intl.DateTimeFormat('tr-TR', {
    timeZone: 'Europe/Istanbul', day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23'
  }).format(parsed);
};
const eventTR = value => ({ buy_confirmed: 'ALIM teyidi', take_profit_confirmed: 'KÂR AL teyidi', full_exit: 'Tam çıkış' }[value] || value || '—');
const reasonTR = value => String(value || '—').replace('BEKAPRSI AUTO EKLE:', 'BEKAPRSI alım teyidi:').replace('BEKAPRSI AUTO KAR AL:', 'BEKAPRSI kâr-al teyidi:').replace('RSI(14)', 'göreli güç endeksi (RSI)').replace(/(?<![\w.])([+-]?\d+(?:\.\d+)?)(?![\w.])/g, token => numberTR(Number(token)));
const timeframeTR = value => value === '1D' || value === 'D' ? 'Günlük' : value === '1W' || value === 'W' ? 'Haftalık' : value;
const signalActionTR = value => value === 'buy_confirmed' ? 'AL' : ['take_profit_confirmed', 'full_exit'].includes(value) ? 'SAT' : null;
const logoURL = (asset, retry = false) => `/api/logos/${encodeURIComponent(asset.symbol)}?market=${encodeURIComponent(asset.market || '')}${retry ? '&retry=1' : ''}`;
const signalTR = value => value === 'buy_confirmed' ? 'AL' : ['take_profit_confirmed', 'full_exit'].includes(value) ? 'SAT' : '—';
const imageHTML = (asset, css = 'asset-logo') => `<img class="${css}" src="${logoURL(asset)}" alt="${esc(asset.symbol)} logosu" data-logo-symbol="${esc(asset.symbol)}" data-logo-market="${esc(asset.market || '')}" loading="lazy" decoding="async" onload="window.scheduleLogoRefresh(this)" onerror="window.fallbackLogo(this)">`;
const universeLogoHTML = row => {
  const ticker = row.yahoo_symbol || row.symbol;
  const src = `/api/logos/${encodeURIComponent(ticker)}?market=${encodeURIComponent(row.market || '')}`;
  return `<img class="universe-logo" src="${src}" alt="${esc(row.symbol)} logosu" data-logo-symbol="${esc(row.symbol)}" data-logo-url="${src}" loading="lazy" decoding="async" onload="window.refreshUniverseLogo(this)" onerror="window.fallbackLogo(this)">`;
};

const EXCHANGE_MARKET_MAP = {
  TR: 'BIST', IST: 'BIST', BIST: 'BIST', ISTANBUL: 'BIST', 'BORSA ISTANBUL': 'BIST',
  US: 'NASDAQ', NMS: 'NASDAQ', NGM: 'NASDAQ', NCM: 'NASDAQ', NAS: 'NASDAQ', NASDAQ: 'NASDAQ', NASDAQGS: 'NASDAQ', NASDAQGM: 'NASDAQ', NASDAQCM: 'NASDAQ',
  NYQ: 'NYSE', NYSE: 'NYSE',
  PCX: 'NYSE Arca', ASE: 'AMEX', AMEX: 'AMEX', ARCA: 'NYSE Arca', NYSEARCA: 'NYSE Arca', 'NYSE ARCA': 'NYSE Arca',
  BTS: 'CBOE', CBOE: 'CBOE', 'CBOE US': 'CBOE', 'CBOE BZX': 'CBOE',
  DE: 'XETRA', GER: 'XETRA', XETRA: 'XETRA', FRANKFURT: 'XETRA', FRA: 'XETRA',
  TOR: 'TSX', TSX: 'TSX', TORONTO: 'TSX',
  LSE: 'LSE', LON: 'LSE', LONDON: 'LSE'
};

function normalizeInstrumentMeta(raw) {
  if (!raw) return null;
  const rawSym = String(raw.symbol || '').trim().toUpperCase();
  const yahooSym = String(raw.yahoo_symbol || raw.symbol || '').trim().toUpperCase();
  const rawMkt = String(raw.exchange || raw.market || '').trim();
  const rawMktUpper = rawMkt.toUpperCase();

  let market = EXCHANGE_MARKET_MAP[rawMktUpper] || (rawMktUpper.includes('ISTANBUL') ? 'BIST' : rawMkt);
  if (!market) {
    if (yahooSym.endsWith('.IS')) market = 'BIST';
    else if (yahooSym.endsWith('.DE')) market = 'XETRA';
    else if (yahooSym.endsWith('.TO')) market = 'TSX';
    else if (yahooSym.endsWith('.L')) market = 'LSE';
    else if (rawSym.endsWith('-USD')) market = 'CRYPTO';
  }
  const mktUpper = String(market || '').toUpperCase();

  let symbol = rawSym;
  if (symbol.endsWith('.IS') && (!mktUpper || mktUpper === 'BIST')) symbol = symbol.slice(0, -3);

  let currency = String(raw.currency || '').trim().toUpperCase();
  if (currency === 'TL') currency = 'TRY';
  if (!currency) {
    if (mktUpper === 'BIST' || yahooSym.endsWith('.IS')) currency = 'TRY';
    else if (mktUpper === 'TSX' || yahooSym.endsWith('.TO')) currency = 'CAD';
    else if (mktUpper === 'XETRA' || yahooSym.endsWith('.DE')) currency = 'EUR';
    else if (mktUpper === 'LSE' || yahooSym.endsWith('.L')) currency = 'GBP';
    else currency = 'USD';
  }

  const universeMatch = (!raw.source_class && Array.isArray(universeSourceRows))
    ? universeSourceRows.find(u => {
        const us = String(u.symbol || '').toUpperCase();
        const uy = String(u.yahoo_symbol || '').toUpperCase();
        return us === symbol || uy === symbol || uy === yahooSym;
      })
    : null;
  const role = raw.role || (raw.source_class ? (universeClassToRole[raw.source_class] || '') : (universeMatch ? (universeClassToRole[universeMatch.source_class] || '') : ''));
  const resolvedMarket = market || universeMarketDefault[rawMktUpper] || rawMkt || 'NASDAQ';
  const resolvedMktUpper = resolvedMarket.toUpperCase();
  const tvPrefix = resolvedMktUpper === 'NYSE ARCA' ? 'AMEX' : resolvedMarket.replace(/\s+/g, '');
  const tvCode = symbol.replace(/\.(IS|DE|TO)$/, '');
  const tv_symbol = String(raw.tv_symbol || '').trim() || (tvPrefix && tvCode ? `${tvPrefix}:${tvCode}` : (yahooSym || symbol));

  return {
    symbol,
    yahoo_symbol: yahooSym || symbol,
    name: raw.name || universeMatch?.name || symbol,
    market: resolvedMarket,
    currency,
    price: raw.price != null ? raw.price : (universeMatch?.quote?.close ?? null),
    kind: raw.kind || (universeMatch ? (universeClassTR[universeMatch.source_class] || 'Hisse') : (resolvedMktUpper === 'BIST' ? 'BIST' : 'Hisse')),
    role,
    tv_symbol,
    tracked: Boolean(raw.tracked),
    genericMarket: !raw.exchange && String(raw.market || '').trim().toUpperCase() === 'US'
  };
}

function fillAssetFormFromMatch(form, rawItem, { preserveSymbol = false } = {}) {
  const meta = normalizeInstrumentMeta(rawItem);
  if (!form || !meta) return;
  if (!preserveSymbol) form.elements.symbol.value = meta.symbol;
  if (meta.market) form.elements.market.value = meta.market;
  if (meta.role && form.elements.role.querySelector(`option[value="${meta.role}"]`)) {
    form.elements.role.value = meta.role;
  }
  if (meta.currency) form.elements.currency.value = meta.currency;
  if (meta.tv_symbol) form.elements.tv_symbol.value = meta.tv_symbol;
}

function collectLocalInstrumentMatches(q) {
  const query = String(q || '').trim().toLocaleUpperCase('tr-TR');
  const queryAscii = query.replace(/İ/g, 'I').replace(/Ş/g, 'S').replace(/Ğ/g, 'G').replace(/Ü/g, 'U').replace(/Ö/g, 'O').replace(/Ç/g, 'C');
  if (!query) return [];

  const seen = new Set();
  const candidates = [];

  const pushCandidate = (raw, basePriority) => {
    const norm = normalizeInstrumentMeta(raw);
    if (!norm || !norm.symbol || seen.has(norm.symbol)) return;
    seen.add(norm.symbol);
    const sym = norm.symbol.toUpperCase();
    const ySym = norm.yahoo_symbol.toUpperCase();
    const nameUp = String(norm.name || '').toLocaleUpperCase('tr-TR');
    let score = 20;
    if (sym === query || sym === queryAscii || ySym === query) score = 100;
    else if (sym.startsWith(query) || sym.startsWith(queryAscii) || ySym.startsWith(query)) score = 70;
    else if (sym.includes(query) || sym.includes(queryAscii)) score = 45;
    else if (nameUp.startsWith(query)) score = 35;
    candidates.push({ item: norm, rank: score + basePriority });
  };

  if (Array.isArray(portfolioAssets)) {
    for (const a of portfolioAssets) {
      const s = String(a.symbol || '').toUpperCase();
      const m = String(a.market || '').toUpperCase();
      const uMatch = Array.isArray(universeSourceRows)
        ? universeSourceRows.find(u => String(u.symbol || '').toUpperCase() === s || String(u.yahoo_symbol || '').toUpperCase() === s)
        : null;
      const compName = uMatch?.name || (a.symbol === 'THYAO' ? 'Türk Hava Yolları A.O.' : a.symbol === 'TUPRS' ? 'Tüpraş Türkiye Petrol Rafinerileri' : a.symbol === 'ASELS' ? 'Aselsan Elektronik Sanayi' : a.symbol === 'EREGL' ? 'Ereğli Demir ve Çelik Fabrikaları' : a.symbol === 'FROTO' ? 'Ford Otomotiv Sanayi' : a.symbol);
      if (s.includes(query) || m.includes(query) || compName.toLocaleUpperCase('tr-TR').includes(query)) {
        pushCandidate({
          symbol: a.symbol,
          yahoo_symbol: uMatch?.yahoo_symbol || a.symbol,
          name: compName,
          market: a.market,
          currency: a.currency === 'TL' ? 'TRY' : a.currency,
          price: a.last_close,
          role: a.role,
          tv_symbol: a.tv_symbol,
          kind: `${roleTR[a.role] || 'İzlemede'} · İzlemede`,
          tracked: true
        }, 5);
      }
    }
  }

  if (Array.isArray(universeSourceRows)) {
    for (const r of universeSourceRows) {
      const s = String(r.symbol || '').toUpperCase();
      const ys = String(r.yahoo_symbol || '').toUpperCase();
      const n = String(r.name || '').toLocaleUpperCase('tr-TR');
      if (s.includes(query) || ys.includes(query) || n.includes(query)) {
        pushCandidate({
          symbol: r.symbol,
          yahoo_symbol: r.yahoo_symbol,
          name: r.name,
          market: r.market,
          exchange: r.exchange,
          source_class: r.source_class,
          currency: r.currency,
          price: r.quote?.close ?? null,
          kind: r.instrument_type === 'etf'
            ? `ETF · ${universeClassTR[r.source_class] || 'ETF'}`
            : (universeClassTR[r.source_class] || r.source_class || 'Hisse')
        }, 0);
      }
    }
  }

  candidates.sort((a, b) => b.rank - a.rank || a.item.symbol.localeCompare(b.item.symbol));
  return candidates.slice(0, 10).map(entry => entry.item);
}

function attachSymbolAutocomplete({ input, dropdown, onInputChange = null, onExactMatch = null, onSelect }) {
  if (!input || !dropdown) return { close: () => {} };

  let timer = null;
  let reqId = 0;
  let currentMatches = [];
  let highlightedIdx = -1;
  let lastExactKey = '';

  const close = () => {
    clearTimeout(timer);
    reqId++;
    dropdown.hidden = true;
    dropdown.innerHTML = '';
    currentMatches = [];
    highlightedIdx = -1;
    lastExactKey = '';
  };

  const updateHighlight = () => {
    dropdown.querySelectorAll('.trade-dropdown-item').forEach((el, idx) => {
      const active = idx === highlightedIdx;
      el.classList.toggle('active', active);
      if (active) el.scrollIntoView({ block: 'nearest' });
    });
  };

  const pick = (item) => {
    if (!item) return;
    close();
    onSelect?.(item);
  };

  const maybeNotifyExact = (items, query) => {
    if (!onExactMatch || !items?.length || query.length < 2) return;
    const up = query.toUpperCase();
    const exact = items.find(m => m.symbol.toUpperCase() === up || m.yahoo_symbol?.toUpperCase() === up);
    if (!exact) return;
    const exactKey = `${exact.symbol}|${exact.market}|${exact.currency}|${exact.tv_symbol}`;
    if (exactKey === lastExactKey) return;
    lastExactKey = exactKey;
    onExactMatch(exact);
  };

  const render = (items, query, searching = false) => {
    currentMatches = items || [];
    highlightedIdx = -1;
    if (!currentMatches.length) {
      dropdown.innerHTML = `<div class="trade-dropdown-empty">${searching
        ? `"${esc(query)}" aranıyor...`
        : `"${esc(query)}" ile eşleşen hisse bulunamadı. Kodu doğrudan yazabilirsiniz.`}</div>`;
      dropdown.hidden = false;
      return;
    }

    dropdown.innerHTML = currentMatches.slice(0, 8).map((it, idx) => {
      const c = it.currency || (it.market === 'BIST' ? 'TRY' : 'USD');
      const cSym = c === 'TRY' ? '₺' : c === 'USD' ? '$' : c === 'CAD' ? 'C$' : c === 'EUR' ? '€' : c;
      const priceText = it.price != null ? `${numberTR(it.price)} ${cSym}` : '';
      return `
        <div class="trade-dropdown-item" data-drop-idx="${idx}">
          <div class="trade-item-left">
            ${imageHTML({ symbol: it.symbol, market: it.market }, 'trade-drop-logo')}
            <div class="trade-item-info">
              <div class="trade-item-symbol">
                <b>${esc(it.symbol)}</b>
                <span class="trade-item-badge">${esc(it.kind || (it.market === 'BIST' ? 'BIST' : 'HİSSE'))}</span>
              </div>
              <div class="trade-item-name" title="${esc(it.name || it.symbol)}">${esc(it.name || it.symbol)}</div>
            </div>
          </div>
          <div class="trade-item-right">
            <div class="trade-item-price mono">${priceText}</div>
            <div class="trade-item-market">${esc(it.market || '')}</div>
          </div>
        </div>
      `;
    }).join('');
    dropdown.hidden = false;

    dropdown.querySelectorAll('[data-drop-idx]').forEach(el => {
      el.addEventListener('mousedown', (e) => {
        e.preventDefault();
        const item = currentMatches[Number(el.dataset.dropIdx)];
        if (item) pick(item);
      });
    });
  };

  const triggerSearch = () => {
    onInputChange?.(input.value || '');
    const q = (input.value || '').trim();
    if (!q) {
      lastExactKey = '';
      close();
      return;
    }

    const local = collectLocalInstrumentMatches(q);
    render(local, q, q.length >= 2 && local.length === 0);
    maybeNotifyExact(local, q);

    clearTimeout(timer);
    if (q.length >= 2) {
      timer = setTimeout(async () => {
        const myReqId = ++reqId;
        try {
          const res = await api(`/api/instruments/search?q=${encodeURIComponent(q)}`);
          if (myReqId !== reqId || (input.value || '').trim() !== q) return;
          const combined = local.map(item => ({ ...item }));
          const bySymbol = new Map(combined.map(item => [item.symbol.toUpperCase(), item]));
          for (const rawRemote of (res.results || [])) {
            const norm = normalizeInstrumentMeta(rawRemote);
            if (!norm || !norm.symbol) continue;
            const key = norm.symbol.toUpperCase();
            const existing = bySymbol.get(key);
            if (existing) {
              if (!existing.tracked && existing.genericMarket && norm.currency === existing.currency && rawRemote.market && !/^US$/i.test(rawRemote.market)) {
                existing.market = norm.market;
                existing.genericMarket = false;
                const tvPrefix = norm.market === 'NYSE Arca' ? 'AMEX' : norm.market.replace(/\s+/g, '');
                const tvCode = existing.symbol.replace(/\.(IS|DE|TO)$/, '');
                if (tvPrefix && tvCode) existing.tv_symbol = `${tvPrefix}:${tvCode}`;
              }
              if ((!existing.name || existing.name === existing.symbol) && norm.name) existing.name = norm.name;
              continue;
            }
            bySymbol.set(key, norm);
            combined.push(norm);
          }
          maybeNotifyExact(combined, q);
          if (document.activeElement === input) render(combined, q, false);
        } catch (_) {
          if (myReqId === reqId && document.activeElement === input && local.length === 0) render([], q, false);
        }
      }, 180);
    }
  };

  input.addEventListener('input', triggerSearch);
  input.addEventListener('focus', () => {
    if ((input.value || '').trim()) triggerSearch();
  });
  input.addEventListener('keydown', (e) => {
    if (dropdown.hidden) return;
    if (e.key === 'ArrowDown' && currentMatches.length > 0) {
      e.preventDefault();
      highlightedIdx = (highlightedIdx + 1) % currentMatches.length;
      updateHighlight();
    } else if (e.key === 'ArrowUp' && currentMatches.length > 0) {
      e.preventDefault();
      highlightedIdx = (highlightedIdx - 1 + currentMatches.length) % currentMatches.length;
      updateHighlight();
    } else if (e.key === 'Enter' && highlightedIdx >= 0 && currentMatches[highlightedIdx]) {
      e.preventDefault();
      pick(currentMatches[highlightedIdx]);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      close();
    }
  });

  document.addEventListener('click', (e) => {
    if (!dropdown.hidden && !input.contains(e.target) && !dropdown.contains(e.target)) {
      close();
    }
  });

  return { close };
}
window.fallbackLogo = image => {
  const symbol = image.dataset.logoSymbol || image.getAttribute('alt').replace(/ logosu$/, '');
  const initials = symbol.replace(/[.\-]/g, ' ').split(/\s+/).filter(Boolean).slice(0, 2).map(part => part[0]).join('') || symbol.slice(0, 2);
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><rect width="100" height="100" rx="22" fill="#172b40"/><text x="50" y="60" text-anchor="middle" font-family="Arial,sans-serif" font-size="34" font-weight="700" fill="#9bd1ff">${esc(initials)}</text></svg>`;
  image.src = `data:image/svg+xml,${encodeURIComponent(svg)}`;
};
window.scheduleLogoRefresh = image => {
  if (image.dataset.logoRetry) return;
  image.dataset.logoRetry = 'pending';
  setTimeout(() => {
    if (!image.isConnected) return;
    image.dataset.logoRetry = 'done';
    const symbol = image.dataset.logoSymbol || image.getAttribute('alt').replace(/ logosu$/, '');
    const market = image.dataset.logoMarket || new URL(image.src).searchParams.get('market') || '';
    image.src = `/api/logos/${encodeURIComponent(symbol)}?market=${encodeURIComponent(market)}&retry=1`;
  }, 3200);
};
window.refreshUniverseLogo = image => {
  if (image.dataset.logoRetry) return;
  image.dataset.logoRetry = 'pending';
  setTimeout(() => {
    if (!image.isConnected) return;
    image.dataset.logoRetry = 'done';
    image.src = `${image.dataset.logoUrl}&refresh=${Date.now()}`;
  }, 3200);
};

function setView(view, updateHash = true) {
  const known = ['overview', 'universe', 'basket', 'watchlist', 'portfolio'].includes(view) ? view : 'overview';
  document.querySelectorAll('[data-view-page]').forEach(page => { page.hidden = page.dataset.viewPage !== known; });
  document.querySelectorAll('.side-link').forEach(link => {
    const active = link.dataset.view === known;
    link.classList.toggle('active', active);
    if (active) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
  });
  if (updateHash && location.hash !== `#${known}`) history.replaceState(null, '', `#${known}`);
  if (known === 'portfolio' && window.loadPortfolio) window.loadPortfolio();
}

function renderWatchlist(assets) {
  const host = $('#assetPicker');
  const removeBtn = $('#removeOpen');
  if (removeBtn) removeBtn.disabled = !assets.length;
  host.innerHTML = assets.length ? assets.map(asset => `<div class="watchlist-card-item${String(asset.id) === $('#chartAsset').value ? ' selected' : ''}">
    <button type="button" class="watchlist-card" data-asset-id="${asset.id}">
      ${imageHTML(asset)}<span class="watchlist-symbol">${esc(asset.symbol)}</span><span class="watchlist-price">${asset.last_close == null ? '—' : `${numberTR(asset.last_close)} ${esc(asset.currency)}`}</span>
      <span class="watchlist-detail">${esc(asset.market)} · ${roleTR[asset.role] || esc(asset.role)}</span><span class="watchlist-date">${asset.price_date ? dateTR(asset.price_date) : asset.data_status === 'error' ? 'Veri sorunu' : 'Veri bekleniyor'}</span>
    </button>
    <button type="button" class="watchlist-remove-btn" data-remove-id="${asset.id}" aria-label="${esc(asset.symbol)} varlığını listeden çıkar" title="Listeden çıkar">Çıkar</button>
  </div>`).join('') : '<div class="rail-empty">İzleme listeniz boş. İlk hisse veya ETF’nizi ekleyin.</div>';
  host.querySelectorAll('[data-asset-id]').forEach(button => button.addEventListener('click', () => window.openAssetChart(Number(button.dataset.assetId))));
  host.querySelectorAll('[data-remove-id]').forEach(button => button.addEventListener('click', () => window.removeAsset(Number(button.dataset.removeId))));
}

function renderBasket(assets) {
  const query = ($('#basketSearch')?.value || '').trim().toLocaleLowerCase('tr-TR');
  const visible = assets.filter(asset => `${asset.symbol} ${asset.market}`.toLocaleLowerCase('tr-TR').includes(query));
  const { key, direction } = basketSort;
  visible.sort((a, b) => {
    let av = key === 'last_signal' ? signalTR(a.last_signal?.event) : key === 'signal_date' ? a.last_signal?.signal_date : a[key];
    let bv = key === 'last_signal' ? signalTR(b.last_signal?.event) : key === 'signal_date' ? b.last_signal?.signal_date : b[key];
    if (key === 'target_weight') { av = Number(a.target_weight); bv = Number(b.target_weight); }
    if (av == null) return 1;
    if (bv == null) return -1;
    if (typeof av === 'number' || typeof bv === 'number') return (Number(av) - Number(bv)) * direction;
    return String(av).localeCompare(String(bv), 'tr', { numeric: true }) * direction;
  });
  $('#basketResultCount').textContent = `${numberTR(visible.length)} varlık`;
  $('#assets').innerHTML = visible.length ? visible.map(asset => {
    const signal = asset.last_signal;
    const change = asset.day_change_pct;
    const date = signal?.signal_date ? dateTR(signal.signal_date) : '—';
    const priceDate = asset.price_date || asset.last_bar_date;
    const price = asset.last_close == null ? '—' : `${numberTR(asset.last_close)} ${esc(asset.currency)}`;
    const actionClass = signalTR(signal?.event) === 'AL' ? 'buy' : signalTR(signal?.event) === 'SAT' ? 'sell' : '';
    return `<tr data-row-asset="${asset.id}">
      <td><button class="table-asset" data-chart-id="${asset.id}">${imageHTML(asset, 'table-logo')}<span><b>${esc(asset.symbol)}</b><small>${esc(asset.currency)}</small></span></button></td>
      <td>${esc(asset.market)}</td><td><span class="role-tag">${roleTR[asset.role] || esc(asset.role)}</span></td>
      <td class="table-price">${price}</td>
      <td class="table-change ${change == null ? '' : change >= 0 ? 'positive' : 'negative'}">${change == null ? '—' : `${change >= 0 ? '+' : ''}${numberTR(change)}%`}</td>
      <td>${numberTR(asset.target_weight * 100)}% <span class="weight-divider">/</span> ${numberTR(asset.max_weight * 100)}%</td>
      <td>${signal ? `<span class="table-signal ${actionClass}">${signalTR(signal.event)}</span>` : '—'}</td>
      <td class="table-date">${date}</td><td class="table-date">${priceDate ? dateTR(priceDate) : asset.data_status === 'error' ? esc(dataErrorTR(asset.last_error)) : 'Bekleniyor'}</td>
      <td class="row-actions"><button class="secondary" data-chart-id="${asset.id}">Grafik</button><button class="secondary" data-remove-id="${asset.id}">Sil</button></td>
    </tr>`;
  }).join('') : `<tr><td colspan="10" class="empty">${assets.length ? 'Arama ölçütüyle eşleşen varlık bulunamadı.' : 'Sepetiniz henüz tanımlı değil.'}</td></tr>`;
  $('#assets').querySelectorAll('[data-chart-id]').forEach(button => button.addEventListener('click', () => window.openAssetChart(Number(button.dataset.chartId))));
  $('#assets').querySelectorAll('[data-remove-id]').forEach(button => button.addEventListener('click', () => window.removeAsset(Number(button.dataset.removeId))));
  document.querySelectorAll('.sort-button').forEach(button => {
    const active = button.dataset.sort === key;
    button.classList.toggle('sorted', active);
    button.querySelector('span').textContent = active ? (direction === 1 ? '↑' : '↓') : '↕';
  });
}

function renderUniverse(triggerBatch = true) {
  const host = $('#universeRows');
  if (!host) return;
  const query = ($('#universeSearch')?.value || '').trim().toLocaleLowerCase('tr-TR');
  const market = $('#universeMarket')?.value || '';
  const sort = $('#universeSort')?.value || 'rate';
  const tracked = new Map();
  for (const asset of portfolioAssets) {
    const sym = String(asset.symbol).toLocaleUpperCase('tr-TR');
    tracked.set(sym, asset);
    if (sym.endsWith('.IS') || sym.endsWith('.TO')) tracked.set(sym.slice(0, -3), asset);
  }
  const visible = universeSourceRows.filter(row => {
    const rowType = row.instrument_type || 'stock';
    const matchesQuery = !query || `${row.name || ''} ${row.symbol || ''} ${row.sector || ''}`.toLocaleLowerCase('tr-TR').includes(query);
    return matchesQuery && (!market || row.market === market) && (!universeType || rowType === universeType) && (!universeCategory || row.source_class === universeCategory);
  });
  if (sort === 'name') visible.sort((a, b) => String(a.name).localeCompare(String(b.name), 'tr'));
  else if (sort === 'symbol') visible.sort((a, b) => String(a.symbol).localeCompare(String(b.symbol), 'tr', { numeric: true }));
  else if (sort === 'source') visible.sort((a, b) => Number(a.pdf_page ?? 999999) - Number(b.pdf_page ?? 999999) || Number(a.pdf_index ?? a.etf_rank ?? 999999) - Number(b.pdf_index ?? b.etf_rank ?? 999999));
  else visible.sort((a, b) => Number(b.source_rate || 0) - Number(a.source_rate || 0));
  const pages = Math.max(1, Math.ceil(visible.length / universePageSize));
  universePage = Math.min(Math.max(1, universePage), pages);
  const pageRows = visible.slice((universePage - 1) * universePageSize, universePage * universePageSize);
  $('#universeResultCount').textContent = `${numberTR(visible.length)} enstrüman`;
  $('#universePageInfo').textContent = visible.length ? `${numberTR((universePage - 1) * universePageSize + 1)}–${numberTR(Math.min(universePage * universePageSize, visible.length))} / ${numberTR(visible.length)}` : 'Sonuç yok';
  $('#universePageNumber').textContent = `${numberTR(universePage)} / ${numberTR(pages)}`;
  $('#universePrevious').disabled = universePage <= 1;
  $('#universeNext').disabled = universePage >= pages;
  host.innerHTML = pageRows.length ? pageRows.map((row, rowIdx) => {
    const quote = row.quote;
    const tickerKey = String(row.yahoo_symbol || row.symbol).toUpperCase();
    const live = universeLiveCache.get(tickerKey);
    const asset = tracked.get(String(row.symbol).toLocaleUpperCase('tr-TR')) || tracked.get(tickerKey);
    const price = asset?.last_close ?? live?.last_close ?? quote?.close;
    const currency = asset?.currency || quote?.currency || row.currency;
    const priceDate = asset?.price_date || asset?.last_bar_date || live?.price_date || quote?.date;
    const signalEvent = asset?.last_signal?.event || live?.last_signal_event;
    const signalDate = asset?.last_signal?.signal_date || live?.last_signal_date;
    const signalType = signalEvent ? signalTR(signalEvent) : '—';
    const signalClass = signalType === 'AL' ? 'buy' : signalType === 'SAT' ? 'sell' : '';
    const weights = asset
      ? `${numberTR(Number(asset.target_weight) * 100)}% / ${numberTR(Number(asset.max_weight) * 100)}%`
      : (row.source_rate != null ? `<span class="universe-rate-note" title="Kaynak yıllık eğim oranı">Eğim %${numberTR(row.source_rate)}</span>` : '—');
    const role = asset ? (roleTR[asset.role] || asset.role) : (universeClassTR[row.source_class] || row.source_class || '—');
    const change = asset?.day_change_pct ?? live?.day_change_pct;
    const priceText = price == null ? '—' : `${numberTR(price)} ${esc(currency)}`;
    const actionBtn = asset
      ? `<button type="button" class="secondary" data-universe-chart="${asset.id}">Grafik</button>`
      : `<button type="button" class="secondary universe-add-btn" data-universe-add="${rowIdx}">＋ İzle</button>`;
    return `<tr title="${esc(row.name || row.symbol)} · ${esc(row.id)}">
      <td><span class="universe-company">${universeLogoHTML(row)}<span><b>${esc(row.symbol)}${row.instrument_type === 'etf' ? '<span class="universe-type-pill">ETF</span>' : ''}</b><small>${esc(row.name || 'Şirket adı yok')}</small></span></span></td>
      <td class="table-price">${priceText}</td>
      <td class="table-change ${change == null ? '' : change >= 0 ? 'positive' : 'negative'}">${change == null ? '—' : `${change >= 0 ? '+' : ''}${numberTR(change)}%`}</td>
      <td><span class="role-tag">${esc(role)}</span></td>
      <td>${weights}</td>
      <td>${signalType !== '—' ? `<span class="table-signal ${signalClass}">${signalType}</span>` : '—'}</td>
      <td class="table-date">${signalDate ? dateTR(signalDate) : '—'}</td>
      <td class="table-date">${priceDate ? dateTR(priceDate) : '—'}</td>
      <td class="row-actions">${actionBtn}</td>
    </tr>`;
  }).join('') : '<tr><td colspan="9" class="empty">Arama ölçütleriyle eşleşen enstrüman bulunamadı.</td></tr>';
  host.querySelectorAll('[data-universe-chart]').forEach(btn => btn.addEventListener('click', () => window.openAssetChart(Number(btn.dataset.universeChart))));
  host.querySelectorAll('[data-universe-add]').forEach(btn => btn.addEventListener('click', () => {
    const row = pageRows[Number(btn.dataset.universeAdd)];
    if (!row) return;
    fillAssetFormFromMatch($('#assetForm'), row);
    $('#assetDialog').showModal();
  }));
  if (triggerBatch && pageRows.length) fetchUniverseBatchForRows(pageRows);
}

async function fetchUniverseBatchForRows(pageRows, force = false) {
  const missing = pageRows.filter(row => {
    const tickerKey = String(row.yahoo_symbol || row.symbol).toUpperCase();
    return force || !universeLiveCache.has(tickerKey);
  });
  if (!missing.length) return;
  const reqId = ++universeBatchRequestId;
  try {
    const payload = {
      force,
      items: missing.map(row => ({
        ticker: row.yahoo_symbol || row.symbol,
        symbol: row.symbol,
        market: row.market || ''
      }))
    };
    const response = await api('/api/universe/batch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    for (const [ticker, item] of Object.entries(response.items || {})) {
      universeLiveCache.set(String(ticker).toUpperCase(), item);
    }
    if (reqId === universeBatchRequestId) renderUniverse(false);
  } catch (error) {
    console.error(error);
  }
}

async function loadUniverseSnapshot() {
  try {
    const response = await fetch('/static/universe_snapshot.json?v=1491', { cache: 'force-cache' });
    if (!response.ok) throw Error('Sabit evren dosyası yüklenemedi.');
    const data = await response.json();
    if (!Array.isArray(data.rows) || !data.rows.length || (data.total != null && Number(data.total) !== data.rows.length)) {
      throw Error('Sabit evren kaydı doğrulanamadı.');
    }
    universeSourceRows = data.rows;
    renderUniverse();
  } catch (error) {
    $('#universeRows').innerHTML = `<tr><td colspan="9" class="empty">${esc(error.message)}</td></tr>`;
    $('#universeResultCount').textContent = 'Evren yüklenemedi';
  }
}

function closeInstrumentSearch() {
  const results = $('#assetSearchResults');
  results.hidden = true;
  $('#assetSearch').setAttribute('aria-expanded', 'false');
}

function selectTrackedInstrument(asset) {
  $('#chartAsset').value = String(asset.id);
  $('#assetSearch').value = asset.symbol;
  closeInstrumentSearch();
  setView('overview');
  load();
}

function prepareNewInstrument(result) {
  fillAssetFormFromMatch($('#assetForm'), result);
  closeInstrumentSearch();
  $('#assetDialog').showModal();
}

function displayInstrumentMatches(query, remote = []) {
  const host = $('#assetSearchResults');
  const local = portfolioAssets.filter(asset => `${asset.symbol} ${asset.market}`
    .toLocaleLowerCase('tr-TR').includes(query.toLocaleLowerCase('tr-TR')));
  const trackedSymbols = new Set(local.map(asset => asset.symbol.toLocaleUpperCase('tr-TR')));
  const external = remote.filter(result => !trackedSymbols.has(result.symbol.toLocaleUpperCase('tr-TR')));
  const trackedMarkup = local.map(asset => `<button type="button" class="instrument-result" role="option" data-tracked-id="${asset.id}">
    ${imageHTML(asset, 'result-logo')}<span class="instrument-result-copy"><b>${esc(asset.symbol)}</b><small>${esc(asset.market)} · ${roleTR[asset.role] || esc(asset.role)}</small></span><span class="result-kind">İZLEMEDE</span>
  </button>`).join('');
  const externalMarkup = external.slice(0, 8).map((result, index) => `<button type="button" class="instrument-result" role="option" data-remote-index="${index}">
    ${imageHTML({ symbol: result.symbol, market: result.market }, 'result-logo')}<span class="instrument-result-copy"><b>${esc(result.symbol)}</b><small>${esc(result.name)} · ${esc(result.market || 'Piyasa bilgisi yok')}</small></span><span class="result-kind">${esc(result.kind)}</span>
  </button>`).join('');
  host.innerHTML = trackedMarkup + externalMarkup || '<div class="instrument-result-empty">Eşleşme bulunamadı. Hisse veya ETF kodunu kontrol edin.</div>';
  host.hidden = false;
  $('#assetSearch').setAttribute('aria-expanded', 'true');
  host.querySelectorAll('[data-tracked-id]').forEach(button => button.addEventListener('click', () => {
    const asset = portfolioAssets.find(item => String(item.id) === button.dataset.trackedId);
    if (asset) selectTrackedInstrument(asset);
  }));
  host.querySelectorAll('[data-remote-index]').forEach(button => button.addEventListener('click', () => {
    const result = external[Number(button.dataset.remoteIndex)];
    if (result) prepareNewInstrument(result);
  }));
}

async function searchInstruments(query) {
  const requestId = ++searchRequestId;
  const localMatches = portfolioAssets.some(asset => `${asset.symbol} ${asset.market}`
    .toLocaleLowerCase('tr-TR').includes(query.toLocaleLowerCase('tr-TR')));
  if (!query) { closeInstrumentSearch(); return; }
  const host = $('#assetSearchResults');
  host.innerHTML = '<div class="instrument-result-empty">Hisse ve ETF eşleşmeleri aranıyor…</div>';
  host.hidden = false;
  $('#assetSearch').setAttribute('aria-expanded', 'true');
  try {
    const response = await api(`/api/instruments/search?q=${encodeURIComponent(query)}`);
    if (requestId !== searchRequestId || $('#assetSearch').value.trim() !== query) return;
    displayInstrumentMatches(query, response.results || []);
  } catch (error) {
    if (requestId !== searchRequestId) return;
    displayInstrumentMatches(query, []);
    if (!localMatches) host.insertAdjacentHTML('beforeend', '<div class="instrument-search-note">Piyasa araması şu anda kullanılamıyor.</div>');
  }
}
const dataErrorTR = value => ({
  "TWELVE_DATA_API_KEY tanımlı değil": 'Fiyat sağlayıcısına şu anda erişilemiyor.',
  'Henüz tamamlanmış günlük bar gelmedi': 'Tamamlanmış günlük kapanış henüz yayımlanmadı.',
  'Yahoo Finance geçerli, tamamlanmış günlük mum döndürmedi': 'Günlük kapanış verisi henüz yayımlanmadı.'
}[value] || value || 'Veri alınamadı');
const telegramTR = raw => String(raw).split('\n').map((line, index) => {
  line = line.replace(/(\d{4})-(\d{2})-(\d{2})/g, (_, y, m, d) => `${d}.${m}.${y}`);
  line = line.replace('buy_confirmed', 'ALIM teyidi').replace('take_profit_confirmed', 'KÂR AL teyidi').replace('full_exit', 'Tam çıkış');
  line = line.replace('Zaman dilimi:', 'Zaman aralığı:').replace('1D', 'Günlük').replace('1W', 'Haftalık');
  line = line.replace('Sinyal:', 'Sinyal fiyatı:').replace('İşlem:', 'Tahmini işlem fiyatı:');
  line = line.replace('RSI(14):', 'Göreli güç endeksi (RSI):').replace('Neden:', 'Sinyal nedeni:').replace('Veri zamanı:', 'Veri tarihi:');
  line = line.replace('BEKAPRSI AUTO EKLE:', 'BEKAPRSI alım teyidi:').replace('BEKAPRSI AUTO KAR AL:', 'BEKAPRSI kâr-al teyidi:');
  if (index < 5) line = line.replace(/(?<![\w.])([+-]?\d+(?:\.\d+)?)(?=\s*(?:USD|TRY|$|,|;|\s))/g, token => {
    const value = Number(token);
    return Number.isFinite(value) ? numberTR(value) : token;
  });
  return line;
}).join('\n');

async function load() {
  try {
    const d = await api('/api/overview');
    portfolioAssets = d.assets;
    $('#assetCount').textContent = numberTR(d.assets.length);
    const signalLists = await Promise.all(d.assets.map(a => api(`/api/assets/${a.id}/signals?include_suppressed=1`).catch(() => [])));
    signalTableRows = d.assets.flatMap((asset, index) => signalLists[index]
      .map(signal => {
        const action = signalActionTR(signal.event);
        return action ? { ...signal, action, asset } : null;
      })
      .filter(Boolean))
      .sort((a, b) => String(b.signal_date).localeCompare(String(a.signal_date))
        || Number(Boolean(a.brake_suppressed)) - Number(Boolean(b.brake_suppressed))
        || String(a.asset.symbol).localeCompare(String(b.asset.symbol), 'tr') || Number(b.id) - Number(a.id));
    renderSignalTable(d.as_of);
    $('#version').textContent = String(d.strategy_version).replace(/(\d+)\.(\d+)\.(\d+)/, '$1,$2,$3');
    const ready = d.assets.filter(a => a.data_status === 'ok').length;
    $('#dataState').textContent = `Otomatik kapanış verisi: ${numberTR(ready)} / ${numberTR(d.assets.length)} varlık`;
    $('#dataInfo').textContent = 'Grafikler seans açıkken Yahoo Finance 15 dakikalık geçici mumu da gösterir. AL/SAT teyitleri yalnızca kapanmış mumlarla hesaplanır.';
    $('#progress').style.width = d.assets.length ? `${Math.round(100 * ready / d.assets.length)}%` : '0%';
    const previousAsset = $('#chartAsset').value;
    const opts = d.assets.map(a => `<option value="${a.id}">${esc(a.symbol)}</option>`).join('');
    $('#chartAsset').innerHTML = '<option value="">Varlık seç</option>' + opts;
    $('#uploadAsset').innerHTML = '<option value="">Varlık seç</option>' + opts;
    const removeOpts = d.assets.map(a => `<option value="${a.id}">${esc(a.symbol)} · ${esc(a.market)}</option>`).join('');
    if ($('#removeAssetSelect')) $('#removeAssetSelect').innerHTML = '<option value="">Varlık seçin</option>' + removeOpts;
    const selected = d.assets.some(a => String(a.id) === previousAsset) ? previousAsset : String(d.assets[0]?.id || '');
    $('#chartAsset').value = selected;
    if (!$('#uploadAsset').value) $('#uploadAsset').value = selected;
    if ($('#removeAssetSelect')) $('#removeAssetSelect').value = selected;
    renderWatchlist(d.assets);
    renderBasket(d.assets);
    renderUniverse();
    $('#assetSearch').value = d.assets.find(a => String(a.id) === selected)?.symbol || '';
    if (selected) await window.loadSignalChart(selected);
    const selectedAsset = d.assets.find(a => String(a.id) === selected);
    const instrumentLogo = $('#instrumentLogo');
    if (instrumentLogo) {
      instrumentLogo.hidden = !selectedAsset;
      if (selectedAsset) {
        instrumentLogo.src = logoURL(selectedAsset);
        instrumentLogo.dataset.logoSymbol = selectedAsset.symbol;
        instrumentLogo.dataset.logoMarket = selectedAsset.market || '';
        instrumentLogo.dataset.logoFallback = 'pending';
        instrumentLogo.onload = () => window.scheduleLogoRefresh(instrumentLogo);
        instrumentLogo.onerror = () => window.fallbackLogo(instrumentLogo);
      }
    }
    const st = d.settings;
    for (const key of ['initial_capital_usd', 'monthly_contribution_usd', 'fees_bps', 'slippage_bps', 'forward_start']) {
      const input = $(`[name="${key}"]`);
      if (input && st[key] != null) input.value = key === 'forward_start' ? dateTR(st[key]) : numberTR(st[key]);
    }
    const bt = await api('/api/backtest/status');
    $('#backtest').textContent = `${bt.message} Veri aralığı: ${dateTR(bt.data_range[0])} – ${dateTR(bt.data_range[1])}.`;
    const tg = await api('/api/telegram/preview');
    if (tg.preview) $('#telegramPreview').textContent = telegramTR(tg.preview);
    const currentHash = location.hash;
    setView(currentHash === '#universe' ? 'universe' : currentHash === '#basket' ? 'basket' : currentHash === '#watchlist' ? 'watchlist' : currentHash === '#portfolio' ? 'portfolio' : 'overview', false);
  } catch (error) {
    console.error(error);
  }
}

function renderSignalTable(asOf = new Date().toISOString()) {
  const filter = $('#signalFilter')?.value || 'ALL';
  const newestDate = signalTableRows[0]?.signal_date || null;
  const newestRows = newestDate ? signalTableRows.filter(row => row.signal_date === newestDate) : [];
  const rows = newestRows.filter(row => filter === 'ALL' || row.action === filter);
  $('#signalCount').textContent = numberTR(rows.length);
  $('#signalLatestDate').textContent = newestDate ? dateTR(newestDate) : '—';
  $('#signalUpdated').textContent = dateTimeTR(asOf);
  $('#signals').innerHTML = rows.length ? rows.map(row => {
    const asset = row.asset;
    const periodClass = row.timeframe === '1W' || row.timeframe === 'W' ? 'signal-period-weekly' : 'signal-period-daily';
    const actionClass = row.action === 'AL' ? 'signal-action-buy' : 'signal-action-sell';
    const brakeTag = row.brake_suppressed
      ? '<span class="signal-brake-badge" title="Son KÂR AL kapanışından -%21 veya daha fazla düşüş nedeniyle haftalık teyit bekleniyor">Fren / Haftalık Bekliyor</span>'
      : '';
    return `<tr>
      <td><span class="signal-asset-cell">${imageHTML(asset, 'signal-logo')}<span><b class="signal-asset">${esc(asset.symbol)}</b><small class="signal-market">${esc(roleTR[asset.role] || asset.role)}</small></span></span></td>
      <td class="${periodClass}">${timeframeTR(row.timeframe)}</td>
      <td><div class="signal-action-wrap"><span class="signal-action ${actionClass}">${row.action}</span>${brakeTag}</div></td>
      <td class="signal-date">${dateTR(row.signal_date)}</td>
    </tr>`;
  }).join('') : `<tr><td colspan="4" class="empty">${newestDate ? `En yeni tarih olan ${dateTR(newestDate)} için teyit edilmiş ${filter === 'ALL' ? 'AL veya SAT' : filter} sinyali yok.` : 'Sepette teyit edilmiş AL veya SAT sinyali yok.'}</td></tr>`;
}

window.removeAsset = async id => {
  if (confirm('Varlık ve bağlı yerel mum/sinyal kayıtları silinsin mi?')) {
    await api(`/api/assets/${id}`, { method: 'DELETE' });
    load();
  }
};
window.openAssetChart = id => {
  $('#chartAsset').value = String(id);
  setView('overview');
  load();
  $('.chart-column').scrollIntoView({ behavior: 'smooth', block: 'start' });
};

async function watchFirstPriceLoad(assetId) {
  $('#dataInfo').textContent = 'Yeni varlığın ilk günlük fiyat geçmişi sağlayıcıdan alınıyor…';
  await window.refreshMarketData();
  const overview = await api('/api/overview');
  const asset = overview.assets.find(item => Number(item.id) === Number(assetId));
  if (asset?.data_status === 'ok') $('#dataInfo').textContent = `${asset.symbol} için fiyat geçmişi yüklendi. Son tamamlanmış mum: ${dateTR(asset.price_date || asset.last_bar_date)}.`;
  else if (asset?.data_status === 'error') $('#dataInfo').textContent = `${asset.symbol} verisi alınamadı: ${dataErrorTR(asset.last_error)}`;
  else $('#dataInfo').textContent = 'Veri eşitlemesi yanıt vermedi. Yeniden denemek için üst çubuktaki düğmeyi kullan.';
}

const assetFormEl = $('#assetForm');
const assetModalAutocomplete = attachSymbolAutocomplete({
  input: assetFormEl?.elements.symbol,
  dropdown: $('#assetModalDropdown'),
  onExactMatch: match => fillAssetFormFromMatch(assetFormEl, match, { preserveSymbol: true }),
  onSelect: item => {
    fillAssetFormFromMatch(assetFormEl, item);
    assetFormEl?.elements.target_weight?.focus();
  }
});
const openBlankAssetDialog = () => {
  assetModalAutocomplete.close();
  assetFormEl?.reset();
  $('#assetDialog').showModal();
};
$('#addOpen').onclick = openBlankAssetDialog;
$('#basketAdd').onclick = openBlankAssetDialog;
$('#closeDialog').onclick = $('#cancelDialog').onclick = () => {
  assetModalAutocomplete.close();
  $('#assetDialog').close();
};
$('#assetDialog')?.addEventListener('close', () => assetModalAutocomplete.close());
if ($('#removeOpen')) {
  $('#removeOpen').onclick = () => {
    if ($('#removeAssetSelect') && $('#chartAsset').value) $('#removeAssetSelect').value = $('#chartAsset').value;
    $('#removeAssetDialog').showModal();
  };
}
if ($('#closeRemoveDialog')) $('#closeRemoveDialog').onclick = () => $('#removeAssetDialog').close();
if ($('#cancelRemoveDialog')) $('#cancelRemoveDialog').onclick = () => $('#removeAssetDialog').close();
if ($('#removeAssetForm')) {
  $('#removeAssetForm').onsubmit = async event => {
    event.preventDefault();
    const id = Number($('#removeAssetSelect').value);
    if (!id) return;
    $('#removeAssetDialog').close();
    await window.removeAsset(id);
  };
}
$('#assetSearch').addEventListener('input', event => {
  const query = event.target.value.trim();
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => searchInstruments(query), 250);
});
$('#assetSearch').addEventListener('keydown', event => {
  if (event.key === 'Escape') closeInstrumentSearch();
  if (event.key === 'Enter') {
    const first = $('#assetSearchResults').querySelector('.instrument-result');
    if (first) { event.preventDefault(); first.click(); }
  }
});
document.addEventListener('click', event => {
  if (!event.target.closest('.instrument-search')) closeInstrumentSearch();
});
document.querySelectorAll('.side-link').forEach(link => link.addEventListener('click', event => {
  event.preventDefault();
  setView(link.dataset.view);
}));
document.querySelector('.strategy-link').addEventListener('click', event => {
  event.preventDefault();
  setView('overview');
  $('#settings').scrollIntoView({ behavior: 'smooth', block: 'start' });
});
window.addEventListener('hashchange', () => {
  const hash = location.hash;
  setView(hash === '#universe' ? 'universe' : hash === '#basket' ? 'basket' : hash === '#watchlist' ? 'watchlist' : hash === '#portfolio' ? 'portfolio' : 'overview', false);
});
loadUniverseSnapshot();
$('#universeSearch').addEventListener('input', () => { universePage = 1; renderUniverse(); });
$('#universeMarket').addEventListener('change', () => { universePage = 1; renderUniverse(); });
$('#universeSort').addEventListener('change', () => { universePage = 1; renderUniverse(); });
document.querySelectorAll('[data-universe-type]').forEach(button => button.addEventListener('click', () => {
  universeType = button.dataset.universeType;
  universePage = 1;
  document.querySelectorAll('[data-universe-type]').forEach(other => other.classList.toggle('selected', other === button));
  renderUniverse();
}));
document.querySelectorAll('[data-universe-category]').forEach(button => button.addEventListener('click', () => {
  universeCategory = button.dataset.universeCategory;
  universePage = 1;
  document.querySelectorAll('[data-universe-category]').forEach(other => other.classList.toggle('selected', other === button));
  renderUniverse();
}));
$('#universePrevious').addEventListener('click', () => { universePage--; renderUniverse(); });
$('#universeNext').addEventListener('click', () => { universePage++; renderUniverse(); });
$('#basketSearch').addEventListener('input', () => renderBasket(portfolioAssets));
document.querySelectorAll('.sort-button').forEach(button => button.addEventListener('click', () => {
  if (basketSort.key === button.dataset.sort) basketSort.direction *= -1;
  else basketSort = { key: button.dataset.sort, direction: 1 };
  renderBasket(portfolioAssets);
}));
$('#assetForm').onsubmit = async event => {
  event.preventDefault();
  const payload = Object.fromEntries(new FormData(event.target));
  payload.target_weight = parseTR(payload.target_weight) / 100;
  payload.max_weight = parseTR(payload.max_weight) / 100;
  payload.parameters = {
    rsi_period: parseTR(payload.rsi_period), buy_zone_max: parseTR(payload.buy_zone_max),
    weekly_brake_drawdown_pct: parseTR(payload.weekly_brake_drawdown_pct)
  };
  delete payload.rsi_period; delete payload.buy_zone_max; delete payload.weekly_brake_drawdown_pct;
  try {
    const added = await api('/api/assets', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    event.target.reset(); assetModalAutocomplete.close(); $('#assetDialog').close(); $('#chartAsset').value = String(added.id); setView('overview'); load(); watchFirstPriceLoad(added.id);
  } catch (error) { alert(error.message); }
};

$('#chartAsset').addEventListener('change', () => load());
$('#chartInterval').addEventListener('change', () => window.loadSignalChart($('#chartAsset').value));
$('#chartStyle').addEventListener('change', () => window.loadSignalChart($('#chartAsset').value));
$('#chartRange').addEventListener('change', () => window.setChartRange?.($('#chartRange').value));
$('#chartBB').addEventListener('change', () => window.loadSignalChart($('#chartAsset').value));
$('#chartEma').addEventListener('change', () => window.loadSignalChart($('#chartAsset').value));
$('#chartLevels').addEventListener('change', () => window.loadSignalChart($('#chartAsset').value));
$('#chartSignals').addEventListener('change', () => window.loadSignalChart($('#chartAsset').value));

$('#settingsForm').onsubmit = async event => {
  event.preventDefault();
  const payload = Object.fromEntries(new FormData(event.target));
  for (const key of ['initial_capital_usd', 'monthly_contribution_usd', 'fees_bps', 'slippage_bps']) payload[key] = parseTR(payload[key]);
  if (payload.forward_start) {
    const match = payload.forward_start.match(/^(\d{2})\.(\d{2})\.(\d{4})$/);
    if (!match) { alert('Tarihi gg.aa.yyyy biçiminde gir.'); return; }
    payload.forward_start = `${match[3]}-${match[2]}-${match[1]}`;
  }
  try {
    const data = await api('/api/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    alert(`Ayarlar kaydedildi. Yeni strateji sürümü: ${data.strategy_version}`);
    load();
  } catch (error) { alert(error.message); }
};

$('#uploadForm').onsubmit = async event => {
  event.preventDefault();
  const id = $('#uploadAsset').value, file = $('#csvFile').files[0], timeframe = $('#timeframe').value;
  if (!id || !file) return;
  $('#uploadMsg').textContent = 'Dosya işleniyor…';
  try {
    const data = await api(`/api/assets/${id}/bars?timeframe=${timeframe}`, {
      method: 'POST', headers: { 'Content-Type': 'text/csv' }, body: await file.text()
    });
    $('#uploadMsg').textContent = `${numberTR(data.rows_imported)} tamamlanmış fiyat mumu yüklendi. ${numberTR(data.created)} yeni sinyal kaydedildi. ${data.message}`;
    load();
  } catch (error) { $('#uploadMsg').textContent = error.message; }
};

window.refreshMarketData = async () => {
  const button = $('#refreshData'); button.disabled = true; button.innerHTML = '⟳ <span>Güncelleniyor…</span>';
  try {
    universeLiveCache.clear();
    const data = await api('/api/data/refresh', { method: 'POST' });
    $('#dataInfo').textContent = `Güncelleme tamamlandı: ${numberTR(data.updated)} / ${numberTR(data.results.length)} varlık. Başarısız varlıkların durumu tabloda gösterilir.`;
    await load();
    return data;
  } catch (error) { $('#dataInfo').textContent = error.message; return null; }
  finally { button.disabled = false; button.innerHTML = '⟳ <span>Veriyi güncelle</span>'; }
};
$('#refreshData').onclick = window.refreshMarketData;
$('#signalFilter').onchange = () => renderSignalTable();
$('#signalRefresh').onclick = () => window.refreshMarketData();

$('#clock').textContent = new Intl.DateTimeFormat('tr-TR', { day: '2-digit', month: '2-digit', year: 'numeric' }).format(new Date());
const formatZonedClock = (timeZone, options) => new Intl.DateTimeFormat('tr-TR', { timeZone, ...options }).format(new Date());
function updateSidebarClocks() {
  const date = $('#sidebarDate'), turkey = $('#turkeyClock'), us = $('#usClock');
  if (!date || !turkey || !us) return;
  const datePart = formatZonedClock('Europe/Istanbul', { day: '2-digit', month: '2-digit', year: 'numeric' });
  const dayName = formatZonedClock('Europe/Istanbul', { weekday: 'long' }).toLocaleUpperCase('tr-TR');
  date.textContent = `${datePart} - ${dayName}`;
  turkey.textContent = formatZonedClock('Europe/Istanbul', { hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' });
  us.textContent = formatZonedClock('America/New_York', { hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' });
}
updateSidebarClocks();
setInterval(updateSidebarClocks, 1000);
const sidebarToggle = $('#sidebarToggle');
function setSidebarCollapsed(collapsed) {
  document.body.classList.toggle('sidebar-collapsed', collapsed);
  sidebarToggle.setAttribute('aria-expanded', String(!collapsed));
  sidebarToggle.setAttribute('aria-label', collapsed ? 'Kenar çubuğunu göster' : 'Kenar çubuğunu gizle');
  $('.sidebar-toggle-label').textContent = collapsed ? 'GÖSTER' : 'GİZLE';
  document.querySelectorAll('.side-link').forEach(link => {
    if (collapsed) link.title = link.querySelector('span:not(.side-icon)')?.textContent.trim() || link.getAttribute('aria-label') || '';
    else link.removeAttribute('title');
  });
}
setSidebarCollapsed(localStorage.getItem('bekap-sidebar-collapsed') === '1');
sidebarToggle.onclick = () => {
  const collapsed = !document.body.classList.contains('sidebar-collapsed');
  localStorage.setItem('bekap-sidebar-collapsed', collapsed ? '1' : '0');
  setSidebarCollapsed(collapsed);
};
setInterval(load, 5 * 60 * 1000);

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
  localStorage.setItem('bekap-theme', theme);
  $('#themeIcon').textContent = theme === 'dark' ? '☀' : '☾';
  $('#themeLabel').textContent = theme === 'dark' ? 'Açık tema' : 'Koyu tema';
  if ($('#chartAsset')?.value) window.loadSignalChart($('#chartAsset').value);
}
applyTheme(localStorage.getItem('bekap-theme') || 'dark');
$('#themeToggle').onclick = () => applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
load();

/* =====================================================================
   İŞLEMLERİM & PORTFÖY MODÜLÜ (BKP-FON & STİL 3)
   ===================================================================== */
let currentPortfolioData = null;

const pctFormat = (val) => {
  if (val == null || !Number.isFinite(Number(val))) return '—';
  const num = Number(val);
  const sign = num > 0 ? '+' : num < 0 ? '−' : '';
  return `${sign}%${Math.abs(num).toLocaleString('tr-TR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
};

const BAR_THEMES = {
  NOM: {
    shortName: 'NOM',
    name: 'NOMİNAL',
    posGrad: 'linear-gradient(90deg, #00d2ff 0%, #3a7bd5 100%)',
    negGrad: 'linear-gradient(90deg, #ff416c 0%, #ff4b2b 100%)',
    glow: 'rgba(0, 210, 255, 0.45)',
    textColor: '#38bdf8'
  },
  REEL: {
    shortName: 'REEL',
    name: 'REEL (TÜFE)',
    posGrad: 'linear-gradient(90deg, #f7971e 0%, #ffd200 100%)',
    negGrad: 'linear-gradient(90deg, #eb3349 0%, #f45c43 100%)',
    glow: 'rgba(247, 151, 30, 0.45)',
    textColor: '#fbbf24'
  },
  USD: {
    shortName: 'USD',
    name: 'DOLAR ($)',
    posGrad: 'linear-gradient(90deg, #10b981 0%, #06b6d4 100%)',
    negGrad: 'linear-gradient(90deg, #d31027 0%, #ea384d 100%)',
    glow: 'rgba(16, 185, 129, 0.45)',
    textColor: '#34d399'
  },
  ALTIN: {
    shortName: 'ALTIN',
    name: 'GR ALTIN',
    posGrad: 'linear-gradient(90deg, #f12711 0%, #f5af19 100%)',
    negGrad: 'linear-gradient(90deg, #cb2d3e 0%, #ef473a 100%)',
    glow: 'rgba(245, 175, 25, 0.45)',
    textColor: '#fbbf24'
  }
};

const barPos = (val) => {
  const num = Number(val) || 0;
  if (num >= 0) {
    const width = Math.min(75, num * (75 / 80));
    const minW = num > 0 ? 3.5 : (num === 0 ? 1.5 : 0);
    return { left: '25%', width: `${Math.max(minW, width)}%`, neg: false, num };
  } else {
    const width = Math.min(25, Math.abs(num) * (25 / 30));
    const minW = 3.5;
    return { left: `${Math.max(0, 25 - width)}%`, width: `${Math.max(minW, width)}%`, neg: true, num };
  }
};

const renderBarHtml = (type, val) => {
  const theme = BAR_THEMES[type] || BAR_THEMES.NOM;
  const p = barPos(val);
  const grad = p.neg ? theme.negGrad : theme.posGrad;
  const glow = p.neg ? 'rgba(255, 65, 108, 0.4)' : theme.glow;
  const num = Number(val) || 0;
  const valColor = num > 0 ? theme.textColor : num < 0 ? '#ff5c6c' : '#7d93a8';
  return `
    <span class="bn" style="color:${theme.textColor}">${theme.shortName}</span>
    <div class="btrack">
      <div class="bfill" style="left:${p.left};width:${p.width};background:${grad};box-shadow:0 0 6px ${glow}"></div>
    </div>
    <span class="bv" style="color:${valColor}">${pctFormat(num)}</span>
  `;
};

const renderDonutSvg = (weightPct) => {
  const r = 13;
  const c = 2 * Math.PI * r;
  const pct = Math.min(100, Math.max(0, Number(weightPct) || 0));
  const dash = (c * pct) / 100;
  const gradId = 'donutGrad_' + Math.floor(Math.random() * 100000);
  return `
    <svg width="34" height="34" viewBox="0 0 34 34" style="filter:drop-shadow(0 0 4px rgba(0, 229, 255, 0.35))">
      <defs>
        <linearGradient id="${gradId}" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stop-color="#00e5ff"/>
          <stop offset="100%" stop-color="#3b82f6"/>
        </linearGradient>
      </defs>
      <circle cx="17" cy="17" r="${r}" fill="none" stroke="#0e1724" stroke-width="4.5"/>
      <circle cx="17" cy="17" r="${r}" fill="none" stroke="url(#${gradId})" stroke-width="4.5"
        stroke-dasharray="${dash.toFixed(1)} ${c.toFixed(1)}" transform="rotate(-90 17 17)" stroke-linecap="round"/>
    </svg>
  `;
};

function renderFundCurveSvg(curvePoints) {
  const svg = $('#fundCurveSvg');
  if (!svg || !curvePoints || curvePoints.length === 0) return;

  const allVals = [];
  curvePoints.forEach(p => {
    allVals.push(p.fund ?? 100, p.gold ?? 100, p.usd ?? 100, p.cpi ?? 100);
  });
  let minV = Math.min(...allVals, 99.0);
  let maxV = Math.max(...allVals, 101.0);
  const diff = maxV - minV;
  if (diff < 3.5) {
    const pad = (3.5 - diff) / 2;
    minV -= pad;
    maxV += pad;
  } else {
    minV -= diff * 0.12;
    maxV += diff * 0.12;
  }

  const w = 540;
  const h = 88;
  const padL = 42;
  const padR = 64;
  const padT = 14;
  const padB = 22;
  const chartW = w - padL - padR;
  const chartH = h - padT - padB;

  const xs = curvePoints.map((_, i) => padL + (i / Math.max(1, curvePoints.length - 1)) * chartW);
  const scaleY = (val) => padT + chartH - ((val - minV) / (maxV - minV)) * chartH;
  const baseY = scaleY(100.0);

  const fundPts = curvePoints.map((p, i) => `${xs[i].toFixed(1)},${scaleY(p.fund).toFixed(1)}`).join(' ');
  const goldPts = curvePoints.map((p, i) => `${xs[i].toFixed(1)},${scaleY(p.gold).toFixed(1)}`).join(' ');
  const usdPts = curvePoints.map((p, i) => `${xs[i].toFixed(1)},${scaleY(p.usd).toFixed(1)}`).join(' ');
  const cpiPts = curvePoints.map((p, i) => `${xs[i].toFixed(1)},${scaleY(p.cpi).toFixed(1)}`).join(' ');

  const firstX = xs[0].toFixed(1);
  const lastIdx = curvePoints.length - 1;
  const lastX = xs[lastIdx].toFixed(1);
  const fundAreaPts = `${firstX},${baseY.toFixed(1)} ${fundPts} ${lastX},${baseY.toFixed(1)}`;

  const lastFund = curvePoints[lastIdx]?.fund || 100;
  const lastGold = curvePoints[lastIdx]?.gold || 100;
  const lastUsd = curvePoints[lastIdx]?.usd || 100;
  const lastCpi = curvePoints[lastIdx]?.cpi || 100;

  const lastFundY = scaleY(lastFund).toFixed(1);
  const lastGoldY = scaleY(lastGold).toFixed(1);
  const lastUsdY = scaleY(lastUsd).toFixed(1);
  const lastCpiY = scaleY(lastCpi).toFixed(1);

  const labelsHtml = curvePoints.map((p, i) => `
    <text x="${xs[i].toFixed(1)}" y="${h - 4}" text-anchor="middle" fill="#6a7d91" font-size="8.5" font-family="monospace">${p.label || ''}</text>
    <line x1="${xs[i].toFixed(1)}" y1="${padT}" x2="${xs[i].toFixed(1)}" y2="${h - padB}" stroke="rgba(140,165,190,0.08)" stroke-dasharray="2 2"/>
  `).join('');

  svg.innerHTML = `
    <defs>
      <linearGradient id="fundAreaGrad" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="#00e5ff" stop-opacity="0.32"/>
        <stop offset="100%" stop-color="#00e5ff" stop-opacity="0.0"/>
      </linearGradient>
      <filter id="glowFund" x="-20%" y="-20%" width="140%" height="140%">
        <feDropShadow dx="0" dy="0" stdDeviation="2.5" flood-color="#00e5ff" flood-opacity="0.75"/>
      </filter>
      <filter id="glowGold" x="-20%" y="-20%" width="140%" height="140%">
        <feDropShadow dx="0" dy="0" stdDeviation="2" flood-color="#fbbf24" flood-opacity="0.65"/>
      </filter>
    </defs>

    <!-- 100 Baseline -->
    <line x1="${padL}" y1="${baseY.toFixed(1)}" x2="${w - padR + 10}" y2="${baseY.toFixed(1)}" stroke="rgba(90, 169, 255, 0.4)" stroke-dasharray="3 3"/>
    <text x="6" y="${(baseY + 3).toFixed(1)}" fill="#5aa9ff" font-size="8.5" font-family="monospace" font-weight="700">100 Baz</text>

    <!-- Grid lines & labels -->
    ${labelsHtml}

    <!-- Area fill under BKP-FON -->
    <polygon points="${fundAreaPts}" fill="url(#fundAreaGrad)"/>

    <!-- Series Polylines -->
    <polyline fill="none" stroke="#f97316" stroke-width="1.8" stroke-dasharray="3 3" opacity="0.85" points="${cpiPts}"/>
    <polyline fill="none" stroke="#10b981" stroke-width="2" opacity="0.9" points="${usdPts}"/>
    <polyline fill="none" stroke="#fbbf24" stroke-width="2.2" filter="url(#glowGold)" points="${goldPts}"/>
    <polyline fill="none" stroke="#00e5ff" stroke-width="3" filter="url(#glowFund)" points="${fundPts}"/>

    <!-- End Dots & Badges -->
    <circle cx="${lastX}" cy="${lastCpiY}" r="3" fill="#f97316"/>
    <circle cx="${lastX}" cy="${lastUsdY}" r="3" fill="#10b981"/>
    <circle cx="${lastX}" cy="${lastGoldY}" r="3.5" fill="#fbbf24" stroke="#78350f" stroke-width="1"/>
    <circle cx="${lastX}" cy="${lastFundY}" r="4.5" fill="#00e5ff" stroke="#ffffff" stroke-width="1.5"/>

    <text x="${Number(lastX) + 8}" y="${Number(lastFundY) + 3}" fill="#00e5ff" font-size="9" font-family="monospace" font-weight="800">FON</text>
    <text x="${Number(lastX) + 8}" y="${Number(lastGoldY) + 3}" fill="#fbbf24" font-size="8.5" font-family="monospace">ALTIN</text>
  `;
}

window.loadPortfolio = async function() {
  try {
    const data = await api('/api/portfolio');
    currentPortfolioData = data;

    if ($('#portUsdTry')) $('#portUsdTry').textContent = `${numberTR(data.macro.usd_try)} ₺`;
    if ($('#portGoldTry')) $('#portGoldTry').textContent = `${numberTR(data.macro.gold_try)} ₺`;
    if ($('#portCpi')) $('#portCpi').textContent = `%${numberTR(data.macro.cpi_monthly_rate || 2.88)}`;

    const s = data.summary;
    if ($('#fundOpenCount')) $('#fundOpenCount').textContent = `${s.open_positions_count} Açık Pozisyon`;
    if ($('#fundTotalVal')) $('#fundTotalVal').textContent = `${numberTR(s.total_value_try)} ₺`;
    if ($('#fundValUsd')) $('#fundValUsd').textContent = `$${numberTR(s.total_value_usd)} USD`;
    if ($('#fundValGold')) $('#fundValGold').textContent = `${numberTR(s.total_value_gold_gram)} Gram`;
    if ($('#fundTotalCost')) $('#fundTotalCost').textContent = `${numberTR(s.total_cost_try)} ₺`;

    const gainEl = $('#fundTotalGain');
    if (gainEl) {
      const ret = s.returns.nominal_pct;
      gainEl.textContent = `${ret >= 0 ? '+' : ''}${numberTR(s.net_nominal_pnl_try)} ₺ (${ret >= 0 ? '+' : ''}%${numberTR(ret)})`;
      gainEl.className = 'mono ' + (ret >= 0 ? 'up' : 'dn');
    }

    renderFundCurveSvg(s.curve);

    const fundBars = $('#fundBarsContainer');
    if (fundBars) {
      fundBars.innerHTML = `
        ${renderBarHtml('NOM', s.returns.nominal_pct)}
        ${renderBarHtml('REEL', s.returns.real_pct)}
        ${renderBarHtml('USD', s.returns.usd_pct)}
        ${renderBarHtml('ALTIN', s.returns.gold_pct)}
      `;
    }

    if ($('#pulseDetailText')) $('#pulseDetailText').textContent = `${s.pulse.sell} KÂR AL · ${s.pulse.buy} YENİ EKLE · ${s.pulse.hold} TUT`;
    const reelAlfaEl = $('#fundReelAlfa');
    if (reelAlfaEl) {
      reelAlfaEl.textContent = `${s.returns.real_pct >= 0 ? '+' : ''}%${numberTR(s.returns.real_pct)} Net`;
      reelAlfaEl.className = 'pulse-val mono ' + (s.returns.real_pct >= 0 ? 'up' : 'dn');
    }
    if ($('#fundMaxPos')) {
      $('#fundMaxPos').textContent = s.pulse.max_position !== '—' ? `%${numberTR(s.pulse.max_position_weight)} (${s.pulse.max_position})` : '—';
    }
    if ($('#fundRealizedPnl')) {
      const rPnl = s.realized_pnl_try;
      $('#fundRealizedPnl').textContent = `${rPnl >= 0 ? '+' : ''}${numberTR(rPnl)} ₺`;
      $('#fundRealizedPnl').className = rPnl >= 0 ? 'up' : 'dn';
    }

    const posBody = $('#portfolioPositionsBody');
    if (posBody) {
      if (!data.positions || data.positions.length === 0) {
        posBody.innerHTML = '<tr><td colspan="7" class="empty">Portföyde açık işlem kaydı bulunmuyor. Yukarıdaki formdan ilk işleminizi girin.</td></tr>';
      } else {
        const bgColors = ['#1f6feb', '#4c9a2a', '#5b6573', '#c8102e', '#5e8e3e', '#0b4ea2', '#8c3eb8'];
        posBody.innerHTML = data.positions.map((pos, idx) => {
          const bgCol = bgColors[idx % bgColors.length];
          const initials = pos.symbol.replace(/[.\-]/g, ' ').split(/\s+/).filter(Boolean).slice(0, 2).map(p => p[0]).join('') || pos.symbol.slice(0, 2);
          const nomReturn = pos.nominal_return_pct;
          const nomCls = nomReturn >= 0 ? 'up' : 'dn';
          const pnlNomTry = pos.nominal_pnl_try;
          const currSymbol = pos.currency === 'TRY' ? '₺' : pos.currency === 'USD' ? '$' : pos.currency === 'CAD' ? 'C$' : pos.currency;

          return `
            <tr class="r">
              <td>
                <div class="port-pos-cell">
                  ${imageHTML({ symbol: pos.symbol, market: pos.market }, 'pos-logo')}
                  <div class="pos-meta">
                    <b>${esc(pos.symbol)}</b>
                    <div class="pos-sub">${esc(pos.market)} · ${esc(pos.currency)} · ${esc(pos.role)}</div>
                  </div>
                </div>
              </td>
              <td class="mono">
                <div style="font-weight:700;font-size:13.5px">${numberTR(pos.current_price)} ${currSymbol}</div>
                <div class="mu" style="font-size:11px">Ort. ${numberTR(pos.avg_cost)} ${currSymbol} · ${numberTR(pos.quantity)} adet</div>
                <div class="${pos.day_change_pct >= 0 ? 'up' : 'dn'}" style="font-size:10px">
                  ${pos.day_change_pct >= 0 ? '+' : ''}%${numberTR(pos.day_change_pct)} bugün
                </div>
              </td>
              <td>
                <div class="port-bars-grid" style="font-size:9.5px;row-gap:3px;">
                  ${renderBarHtml('NOM', pos.nominal_return_pct)}
                  ${renderBarHtml('REEL', pos.real_return_pct)}
                  ${renderBarHtml('USD', pos.usd_return_pct)}
                  ${renderBarHtml('ALTIN', pos.gold_return_pct)}
                </div>
              </td>
              <td>
                <div class="pos-pnl-chip ${nomCls}">
                  <span class="pnl-amount">${pnlNomTry >= 0 ? '+' : ''}${numberTR(pnlNomTry)} ₺</span>
                  <span class="pnl-pct">${nomReturn >= 0 ? '+' : ''}%${numberTR(nomReturn)}</span>
                </div>
              </td>
              <td>
                <div class="port-ring-wrap">
                  ${renderDonutSvg(pos.weight_pct)}
                  <div class="mono">
                    <b style="font-size:13.5px;color:#fff">${numberTR(pos.value_try)} ₺</b>
                    <div class="mu" style="font-size:10.5px;color:#7d93a8">%${numberTR(pos.weight_pct)} · $${numberTR(pos.value_usd)}</div>
                  </div>
                </div>
              </td>
              <td>
                <span class="tag ${pos.signal_class}">${esc(pos.signal_text)}</span>
              </td>
              <td style="text-align:right">
                <div class="port-action-btns">
                  <button type="button" class="btn-action-act buy" data-quick-action="BUY" data-quick-symbol="${esc(pos.symbol)}" data-quick-price="${pos.current_price}" data-quick-currency="${esc(pos.currency)}">+ Ekle</button>
                  <button type="button" class="btn-action-act sell" data-quick-action="SELL" data-quick-symbol="${esc(pos.symbol)}" data-quick-price="${pos.current_price}" data-quick-qty="${pos.quantity}" data-quick-currency="${esc(pos.currency)}">Sat</button>
                </div>
              </td>
            </tr>
          `;
        }).join('');
      }
    }

    const tradesBody = $('#portfolioTradesBody');
    if (tradesBody) {
      if (!data.trades || data.trades.length === 0) {
        tradesBody.innerHTML = '<tr><td colspan="10" class="empty">Henüz kaydedilmiş işlem geçmişi yok.</td></tr>';
      } else {
        tradesBody.innerHTML = data.trades.map(t => {
          const isBuy = t.side === 'BUY';
          const sideTag = isBuy
            ? '<span class="tag t-g">ALIŞ</span>'
            : '<span class="tag" style="color:var(--red);background:rgba(255,92,108,0.1)">SATIŞ</span>';
          const curr = t.currency === 'TRY' ? '₺' : t.currency === 'USD' ? '$' : t.currency === 'CAD' ? 'C$' : t.currency;

          return `
            <tr>
              <td class="mono">${esc(t.trade_date)}</td>
              <td>${sideTag}</td>
              <td class="mono">
                <div style="display:flex;align-items:center;gap:7px;">
                  ${imageHTML({ symbol: t.symbol, market: '' }, 'ledger-logo')}
                  <b>${esc(t.symbol)}</b>
                </div>
              </td>
              <td class="mono">${numberTR(t.price)} ${curr}</td>
              <td class="mono">${numberTR(t.quantity)}</td>
              <td class="mono" style="font-weight:700">${numberTR(t.total_amount)} ${curr}</td>
              <td class="mono mu">${numberTR(t.usd_try_rate)} ₺</td>
              <td class="mono mu">${numberTR(t.gold_try_price)} ₺</td>
              <td style="font-size:11px;color:var(--muted)">${esc(t.notes || '—')}</td>
              <td style="text-align:right">
                <button type="button" class="port-ctrl-btn edit" data-edit-trade="${t.id}">✏️ Düzenle</button>
                <button type="button" class="port-ctrl-btn del" data-del-trade="${t.id}">🗑️ Sil</button>
              </td>
            </tr>
          `;
        }).join('');
      }
    }
    if (window.syncPortfolioTradeGuard) window.syncPortfolioTradeGuard();
  } catch (err) {
    console.error('Portfolio load error:', err);
  }
};

function initPortfolioUI() {
  const form = $('#portfolioTradeForm');
  if (!form) return;

  const symInput = $('#tradeSymbol');
  const symDropdown = $('#tradeSymbolDropdown');
  const dateInput = $('#tradeDate');
  const datePicker = $('#tradeDatePicker');
  const calBtn = $('#tradeCalBtn');
  const sideBuy = $('.side-btn.buy');
  const sideSell = $('.side-btn.sell');
  const sideInput = $('#tradeSide');
  const priceInput = $('#tradePrice');
  const qtyInput = $('#tradeQuantity');
  const totalPreview = $('#tradeTotalPreview');
  const currBadge = $('#tradeCurrencyBadge');
  const valBanner = $('#tradeValidationBanner');
  const toggleNotesBtn = $('#toggleNotesBtn');
  const notesWrap = $('#tradeNotesWrap');
  const toggleLedgerBtn = $('#toggleLedgerBtn');
  const ledgerPanel = $('#portfolioLedgerPanel');

  const showBanner = (type, text) => {
    if (!valBanner) return;
    valBanner.className = `trade-validation-banner ${type}`;
    valBanner.textContent = text;
    valBanner.hidden = false;
  };
  const hideBanner = () => {
    if (!valBanner) return;
    valBanner.hidden = true;
    valBanner.textContent = '';
  };

  const toIsoDate = (val) => {
    const s = String(val || '').trim();
    if (s.includes('.')) {
      const parts = s.split('.');
      if (parts.length === 3) return `${parts[2]}-${parts[1].padStart(2, '0')}-${parts[0].padStart(2, '0')}`;
    }
    return s.slice(0, 10);
  };

  const getSellableInfo = (symbol, dateStr) => {
    const sym = String(symbol || '').trim().toUpperCase();
    const isoDate = toIsoDate(dateStr) || new Date().toISOString().slice(0, 10);
    const trades = (currentPortfolioData && Array.isArray(currentPortfolioData.trades)) ? currentPortfolioData.trades : [];
    const symTrades = trades
      .filter(t => String(t.symbol || '').toUpperCase() === sym)
      .map(t => ({ ...t, iso: t.raw_date || toIsoDate(t.trade_date) }))
      .sort((a, b) => a.iso.localeCompare(b.iso) || (a.side === b.side ? a.id - b.id : a.side === 'BUY' ? -1 : 1));

    let totalBought = 0;
    let running = 0;
    let minFuture = null;
    for (const t of symTrades) {
      const q = Number(t.quantity) || 0;
      if (t.iso <= isoDate) {
        if (t.side === 'BUY') {
          totalBought += q;
          running += q;
        } else {
          running -= q;
        }
        minFuture = running;
      } else {
        if (minFuture === null) minFuture = running;
        running += (t.side === 'BUY' ? q : -q);
        if (running < minFuture) minFuture = running;
      }
    }
    const maxSellable = minFuture === null ? 0 : Math.max(0, minFuture);
    return { sym, isoDate, totalBought, maxSellable };
  };

  const setSideToBuy = () => {
    if (sideBuy) sideBuy.classList.add('selected');
    if (sideSell) sideSell.classList.remove('selected');
    if (sideInput) sideInput.value = 'BUY';
  };

  const refreshSellAvailability = (explicitAttempt = false) => {
    const sym = (symInput?.value || '').trim().toUpperCase();
    const openPositions = currentPortfolioData?.positions || [];
    const info = sym ? getSellableInfo(sym, dateInput?.value) : { totalBought: 0, maxSellable: 0 };
    const canSell = sym ? info.maxSellable > 1e-6 : openPositions.length > 0;

    if (sideSell) {
      sideSell.classList.toggle('is-unavailable', !canSell);
      sideSell.title = !canSell
        ? (sym ? `${sym} için satılabilecek açık pozisyon yok` : 'Portföyde açık pozisyon yok')
        : (sym ? `Satılabilir maksimum: ${numberTR(info.maxSellable)} adet` : 'Satış işlemi');
    }

    if (sideInput?.value === 'SELL') {
      if (!canSell) {
        setSideToBuy();
        const msg = sym
          ? `⚠️ Portföyünüzde seçili tarih (${dateInput?.value || ''}) itibarıyla satılabilecek açık ${sym} pozisyonu bulunmuyor. Önce alış işlemi girmelisiniz.`
          : '⚠️ Portföyünüzde henüz açık pozisyon bulunmadığı için satış yapılamaz. Önce alış işlemi girmelisiniz.';
        showBanner('error', msg);
      } else if (sym) {
        const enteredQty = parseFloat(qtyInput?.value) || 0;
        if (enteredQty > info.maxSellable + 1e-6) {
          showBanner('error', `⚠️ Yetersiz bakiye: ${sym} için bu tarihte en fazla ${numberTR(info.maxSellable)} adet satılabilir.`);
        } else {
          showBanner('info', `ℹ️ ${sym} için bu tarihte satılabilir maksimum miktar: ${numberTR(info.maxSellable)} adet`);
        }
      } else if (openPositions.length > 0) {
        const list = openPositions.map(p => `${p.symbol} (${numberTR(p.quantity)})`).slice(0, 4).join(', ');
        showBanner('info', `ℹ️ Satış için açık bir hisse kodu seçin: ${list}`);
      }
    } else if (!explicitAttempt && valBanner && !valBanner.classList.contains('error')) {
      hideBanner();
    }
  };
  window.syncPortfolioTradeGuard = () => refreshSellAvailability(false);

  let tradeAutocomplete = null;
  const closeTradeDropdown = () => tradeAutocomplete?.close();

  const selectTradeItem = (item) => {
    if (!item) return;
    symInput.value = item.symbol;
    const detectedCurr = item.currency || (item.market === 'BIST' || item.market === 'TR' || item.symbol.endsWith('.IS') ? 'TRY' : item.market === 'TSX' || item.symbol.endsWith('.TO') ? 'CAD' : 'USD');
    currBadge.textContent = detectedCurr;
    if (item.price && (!priceInput.value || parseFloat(priceInput.value) === 0)) {
      priceInput.value = Number(item.price).toFixed(2);
    }
    closeTradeDropdown();
    updateLiveTotal();
    refreshSellAvailability(false);
    if (!priceInput.value || parseFloat(priceInput.value) === 0) {
      priceInput.focus();
    } else {
      qtyInput.focus();
    }
  };

  const today = new Date();
  const dd = String(today.getDate()).padStart(2, '0');
  const mm = String(today.getMonth() + 1).padStart(2, '0');
  const yyyy = today.getFullYear();
  if (dateInput && !dateInput.value) {
    dateInput.value = `${dd}.${mm}.${yyyy}`;
  }

  if (calBtn && datePicker) {
    calBtn.onclick = () => {
      try {
        if (datePicker.showPicker) datePicker.showPicker();
        else datePicker.click();
      } catch (e) {
        datePicker.click();
      }
    };
    datePicker.onchange = () => {
      if (datePicker.value) {
        const parts = datePicker.value.split('-');
        if (parts.length === 3) {
          dateInput.value = `${parts[2]}.${parts[1]}.${parts[0]}`;
        }
        refreshSellAvailability(false);
      }
    };
  }

  if (dateInput) {
    dateInput.addEventListener('input', (e) => {
      let v = e.target.value.replace(/\D/g, '').slice(0, 8);
      if (v.length >= 5) {
        e.target.value = `${v.slice(0, 2)}.${v.slice(2, 4)}.${v.slice(4)}`;
      } else if (v.length >= 3) {
        e.target.value = `${v.slice(0, 2)}.${v.slice(2)}`;
      } else {
        e.target.value = v;
      }
      refreshSellAvailability(false);
    });
  }

  if (sideBuy && sideSell && sideInput) {
    sideBuy.onclick = () => {
      closeTradeDropdown();
      setSideToBuy();
      hideBanner();
      refreshSellAvailability(false);
    };
    sideSell.onclick = () => {
      closeTradeDropdown();
      const sym = (symInput?.value || '').trim().toUpperCase();
      const openCount = (currentPortfolioData?.positions || []).length;
      const info = sym ? getSellableInfo(sym, dateInput?.value) : { maxSellable: 0 };
      if ((sym && info.maxSellable <= 1e-6) || (!sym && openCount === 0)) {
        setSideToBuy();
        const msg = sym
          ? `⚠️ Portföyünüzde seçili tarih (${dateInput?.value || ''}) itibarıyla satılabilecek açık ${sym} pozisyonu bulunmuyor. Önce alış işlemi girmelisiniz.`
          : '⚠️ Portföyünüzde henüz açık pozisyon bulunmadığı için satış yapılamaz. Önce alış işlemi girmelisiniz.';
        showBanner('error', msg);
        refreshSellAvailability(true);
        return;
      }
      sideSell.classList.add('selected');
      sideBuy.classList.remove('selected');
      sideInput.value = 'SELL';
      refreshSellAvailability(true);
    };
  }

  const updateCurrencyBadge = () => {
    const s = (symInput.value || '').trim().toUpperCase();
    if (s.endsWith('.IS') || ['THYAO', 'TUPRS', 'ASELS', 'FROTO', 'EREGL', 'GARAN'].includes(s)) {
      currBadge.textContent = 'TRY';
    } else if (s.endsWith('.TO') || s === 'SHOP.TO') {
      currBadge.textContent = 'CAD';
    } else {
      currBadge.textContent = 'USD';
    }
    updateLiveTotal();
    refreshSellAvailability(false);
  };

  const updateLiveTotal = () => {
    const p = parseFloat(priceInput.value) || 0;
    const q = parseFloat(qtyInput.value) || 0;
    const tot = p * q;
    const curr = currBadge.textContent;
    const currSym = curr === 'TRY' ? '₺' : curr === 'USD' ? '$' : curr === 'CAD' ? 'CAD' : curr;
    totalPreview.textContent = `${numberTR(tot)} ${currSym}`;
  };

  tradeAutocomplete = attachSymbolAutocomplete({
    input: symInput,
    dropdown: symDropdown,
    onInputChange: updateCurrencyBadge,
    onSelect: selectTradeItem
  });

  if (priceInput) priceInput.addEventListener('input', updateLiveTotal);
  if (qtyInput) {
    qtyInput.addEventListener('input', () => {
      updateLiveTotal();
      if (sideInput?.value === 'SELL') refreshSellAvailability(false);
    });
  }

  if (toggleNotesBtn && notesWrap) {
    toggleNotesBtn.onclick = () => {
      notesWrap.hidden = !notesWrap.hidden;
      toggleNotesBtn.textContent = notesWrap.hidden ? '+ Opsiyonel: Not / Komisyon Ekle' : '− Not Kutusunu Gizle';
    };
  }

  if (toggleLedgerBtn && ledgerPanel) {
    toggleLedgerBtn.onclick = () => {
      ledgerPanel.hidden = !ledgerPanel.hidden;
      toggleLedgerBtn.textContent = ledgerPanel.hidden ? '📜 İşlem Geçmişi Defteri Aç ▾' : '📜 Defteri Kapat ▴';
    };
  }

  document.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-quick-action]');
    if (!btn) return;
    const action = btn.dataset.quickAction;
    const sym = btn.dataset.quickSymbol;
    const price = btn.dataset.quickPrice;
    const qty = btn.dataset.quickQty;
    const qCurr = btn.dataset.quickCurrency;

    if (symInput) symInput.value = sym;
    if (priceInput && price) priceInput.value = price;
    updateCurrencyBadge();
    if (qCurr && currBadge) {
      currBadge.textContent = qCurr;
      updateLiveTotal();
    }
    if (action === 'BUY' && sideBuy) sideBuy.click();
    if (action === 'SELL' && sideSell) {
      if (qtyInput && qty) qtyInput.value = qty;
      sideSell.click();
      updateLiveTotal();
    }
    form.scrollIntoView({ behavior: 'smooth', block: 'center' });
    if (qtyInput && action === 'BUY') qtyInput.focus();
  });

  form.onsubmit = async (e) => {
    e.preventDefault();
    const sym = symInput.value.trim().toUpperCase();
    const tradeDateVal = dateInput.value.trim();
    const qtyVal = parseFloat(qtyInput.value);

    if (sideInput.value === 'SELL') {
      const info = getSellableInfo(sym, tradeDateVal);
      if (info.maxSellable <= 1e-6) {
        const msg = `Portföyünüzde seçili tarih (${tradeDateVal}) itibarıyla satılabilecek açık ${sym} pozisyonu bulunmuyor. Önce alış işlemi girmelisiniz.`;
        setSideToBuy();
        refreshSellAvailability(false);
        showBanner('error', `⚠️ ${msg}`);
        alert(msg);
        return;
      }
      if (qtyVal > info.maxSellable + 1e-6) {
        const msg = `Yetersiz pozisyon: ${sym} için bu tarihte satılabilecek maksimum miktar ${numberTR(info.maxSellable)} adet (girilen: ${numberTR(qtyVal)} adet).`;
        showBanner('error', `⚠️ ${msg}`);
        qtyInput.focus();
        alert(msg);
        return;
      }
    }

    const saveBtn = $('#saveTradeBtn');
    saveBtn.disabled = true;
    saveBtn.textContent = 'Kaydediliyor…';

    try {
      const payload = {
        symbol: sym,
        date: tradeDateVal,
        side: sideInput.value,
        price: parseFloat(priceInput.value),
        quantity: qtyVal,
        currency: currBadge ? currBadge.textContent.trim() : undefined,
        notes: ($('#tradeNotes')?.value || '').trim(),
      };

      await api('/api/portfolio/trade', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      priceInput.value = '';
      qtyInput.value = '';
      if ($('#tradeNotes')) $('#tradeNotes').value = '';
      hideBanner();
      closeTradeDropdown();
      setSideToBuy();
      updateLiveTotal();
      await window.loadPortfolio();
    } catch (err) {
      const msg = err.message === 'Failed to fetch'
        ? 'Sunucuya bağlanılamadı (127.0.0.1:8767). Arka plan sunucusunun çalıştığından emin olun.'
        : (err.message || 'İşlem kaydedilemedi.');
      showBanner('error', `⚠️ ${msg}`);
      alert(msg);
    } finally {
      saveBtn.disabled = false;
      saveBtn.textContent = '💾 HAFIZAYA KAYDET';
    }
  };

  const editDialog = $('#editTradeDialog');
  const closeEditBtn = $('#closeEditTradeDialog');
  const cancelEditBtn = $('#cancelEditTradeDialog');
  const editForm = $('#editTradeForm');

  if (closeEditBtn && editDialog) closeEditBtn.onclick = () => editDialog.close();
  if (cancelEditBtn && editDialog) cancelEditBtn.onclick = () => editDialog.close();

  document.addEventListener('click', (e) => {
    const editBtn = e.target.closest('[data-edit-trade]');
    if (!editBtn || !currentPortfolioData || !editDialog) return;
    const tId = parseInt(editBtn.dataset.editTrade, 10);
    const trade = currentPortfolioData.trades.find(t => t.id === tId);
    if (!trade) return;

    $('#editTradeId').value = trade.id;
    $('#editTradeSymbol').value = trade.symbol;
    $('#editTradeDate').value = trade.trade_date;
    $('#editTradeSide').value = trade.side;
    $('#editTradePrice').value = trade.price;
    $('#editTradeQuantity').value = trade.quantity;
    $('#editTradeNotes').value = trade.notes || '';
    editDialog.showModal();
  });

  if (editForm && editDialog) {
    editForm.onsubmit = async (e) => {
      e.preventDefault();
      const tId = $('#editTradeId').value;
      const payload = {
        symbol: $('#editTradeSymbol').value.trim().toUpperCase(),
        date: $('#editTradeDate').value.trim(),
        side: $('#editTradeSide').value,
        price: parseFloat($('#editTradePrice').value),
        quantity: parseFloat($('#editTradeQuantity').value),
        notes: $('#editTradeNotes').value.trim(),
      };

      try {
        await api(`/api/portfolio/trade/${tId}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        editDialog.close();
        await window.loadPortfolio();
      } catch (err) {
        alert(err.message || 'İşlem güncellenemedi.');
      }
    };
  }

  document.addEventListener('click', async (e) => {
    const delBtn = e.target.closest('[data-del-trade]');
    if (!delBtn) return;
    const tId = delBtn.dataset.delTrade;
    if (!confirm('Bu işlem kaydını silmek istediğinize emin misiniz?\n(Maliyet ve kâr/zarar oranları otomatik yeniden hesaplanacaktır)')) return;

    try {
      await api(`/api/portfolio/trade/${tId}`, { method: 'DELETE' });
      await window.loadPortfolio();
    } catch (err) {
      alert(err.message || 'İşlem silinemedi.');
    }
  });
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initPortfolioUI);
} else {
  initPortfolioUI();
}
