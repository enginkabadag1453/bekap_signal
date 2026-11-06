const $ = s => document.querySelector(s);
const roleTR = { lifetime: 'Ömürlük', trend: 'Trend', periodic: 'Dönemsel' };
let signalTableRows = [];
let portfolioAssets = [];
let basketSort = { key: 'symbol', direction: 1 };
let searchTimer = null;
let searchRequestId = 0;

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

function setView(view, updateHash = true) {
  const known = ['overview', 'basket', 'watchlist'].includes(view) ? view : 'overview';
  document.querySelectorAll('[data-view-page]').forEach(page => { page.hidden = page.dataset.viewPage !== known; });
  document.querySelectorAll('.side-link').forEach(link => {
    const active = link.dataset.view === known;
    link.classList.toggle('active', active);
    if (active) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
  });
  if (updateHash && location.hash !== `#${known}`) history.replaceState(null, '', `#${known}`);
}

function renderWatchlist(assets) {
  const host = $('#assetPicker');
  host.innerHTML = assets.length ? assets.map(asset => `<button type="button" class="watchlist-card${String(asset.id) === $('#chartAsset').value ? ' selected' : ''}" data-asset-id="${asset.id}">
    ${imageHTML(asset)}<span class="watchlist-symbol">${esc(asset.symbol)}</span><span class="watchlist-price">${asset.last_close == null ? '—' : `${numberTR(asset.last_close)} ${esc(asset.currency)}`}</span>
    <span class="watchlist-detail">${esc(asset.market)} · ${roleTR[asset.role] || esc(asset.role)}</span><span class="watchlist-date">${asset.price_date ? dateTR(asset.price_date) : asset.data_status === 'error' ? 'Veri sorunu' : 'Veri bekleniyor'}</span>
  </button>`).join('') : '<div class="rail-empty">İzleme listeniz boş. İlk hisse veya ETF’nizi ekleyin.</div>';
  host.querySelectorAll('[data-asset-id]').forEach(button => button.addEventListener('click', () => window.openAssetChart(Number(button.dataset.assetId))));
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
  const form = $('#assetForm');
  form.elements.symbol.value = result.symbol;
  form.elements.market.value = result.market || '';
  form.elements.currency.value = result.currency || '';
  form.elements.tv_symbol.value = result.symbol;
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
    const signalLists = await Promise.all(d.assets.map(a => api(`/api/assets/${a.id}/signals`).catch(() => [])));
    signalTableRows = d.assets.flatMap((asset, index) => signalLists[index]
      .map(signal => {
        const action = signalActionTR(signal.event);
        return action ? { ...signal, action, asset } : null;
      })
      .filter(Boolean))
      .sort((a, b) => String(b.signal_date).localeCompare(String(a.signal_date))
        || String(a.asset.symbol).localeCompare(String(b.asset.symbol), 'tr') || Number(b.id) - Number(a.id));
    renderSignalTable(d.as_of);
    $('#version').textContent = String(d.strategy_version).replace(/(\d+)\.(\d+)\.(\d+)/, '$1,$2,$3');
    const ready = d.assets.filter(a => a.data_status === 'ok').length;
    $('#dataState').textContent = `Otomatik kapanış verisi: ${numberTR(ready)} / ${numberTR(d.assets.length)} varlık`;
    $('#dataInfo').textContent = 'Grafik verisi Yahoo Finance günlük OHLC serisinden otomatik alınır. Bu akış gerçek zamanlı kotasyon değildir; son tamamlanmış mumun tarihi grafikte gösterilir.';
    $('#progress').style.width = d.assets.length ? `${Math.round(100 * ready / d.assets.length)}%` : '0%';
    const previousAsset = $('#chartAsset').value;
    const opts = d.assets.map(a => `<option value="${a.id}">${esc(a.symbol)}</option>`).join('');
    $('#chartAsset').innerHTML = '<option value="">Varlık seç</option>' + opts;
    $('#uploadAsset').innerHTML = '<option value="">Varlık seç</option>' + opts;
    const selected = d.assets.some(a => String(a.id) === previousAsset) ? previousAsset : String(d.assets[0]?.id || '');
    $('#chartAsset').value = selected;
    if (!$('#uploadAsset').value) $('#uploadAsset').value = selected;
    renderWatchlist(d.assets);
    renderBasket(d.assets);
    $('#assetSearch').value = d.assets.find(a => String(a.id) === selected)?.symbol || '';
    if (selected) await window.loadSignalChart(selected);
    const selectedAsset = d.assets.find(a => String(a.id) === selected);
    if (selectedAsset && $('#instrumentSub')) $('#instrumentSub').textContent = `${selectedAsset.market} · ${selectedAsset.currency} · ${roleTR[selectedAsset.role] || selectedAsset.role} · Günlük kapanış`;
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
    setView(location.hash === '#basket' ? 'basket' : location.hash === '#watchlist' ? 'watchlist' : 'overview', false);
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
    return `<tr>
      <td><span class="signal-asset-cell">${imageHTML(asset, 'signal-logo')}<span><b class="signal-asset">${esc(asset.symbol)}</b><small class="signal-market">${esc(roleTR[asset.role] || asset.role)}</small></span></span></td>
      <td class="${periodClass}">${timeframeTR(row.timeframe)}</td>
      <td><span class="signal-action ${actionClass}">${row.action}</span></td>
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

$('#addOpen').onclick = () => $('#assetDialog').showModal();
$('#basketAdd').onclick = () => $('#assetDialog').showModal();
$('#closeDialog').onclick = $('#cancelDialog').onclick = () => $('#assetDialog').close();
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
  setView(location.hash === '#basket' ? 'basket' : location.hash === '#watchlist' ? 'watchlist' : 'overview', false);
});
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
    event.target.reset(); $('#assetDialog').close(); $('#chartAsset').value = String(added.id); setView('overview'); load(); watchFirstPriceLoad(added.id);
  } catch (error) { alert(error.message); }
};

$('#chartAsset').addEventListener('change', () => load());
$('#chartInterval').addEventListener('change', () => window.loadSignalChart($('#chartAsset').value));
$('#chartStyle').addEventListener('change', () => window.loadSignalChart($('#chartAsset').value));
$('#chartRange').addEventListener('change', () => window.setChartRange?.($('#chartRange').value));
$('#chartBB').addEventListener('change', () => window.loadSignalChart($('#chartAsset').value));
$('#chartSma').addEventListener('change', () => window.loadSignalChart($('#chartAsset').value));
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
