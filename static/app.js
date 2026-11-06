const $ = s => document.querySelector(s);
const roleTR = { lifetime: 'Ömürlük', trend: 'Trend', periodic: 'Dönemsel' };

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
const eventTR = value => ({ buy_confirmed: 'ALIM teyidi', take_profit_confirmed: 'KÂR AL teyidi', full_exit: 'Tam çıkış' }[value] || value || '—');
const reasonTR = value => String(value || '—').replace('BEKAPRSI AUTO EKLE:', 'BEKAPRSI alım teyidi:').replace('BEKAPRSI AUTO KAR AL:', 'BEKAPRSI kâr-al teyidi:').replace('RSI(14)', 'göreli güç endeksi (RSI)').replace(/(?<![\w.])([+-]?\d+(?:\.\d+)?)(?![\w.])/g, token => numberTR(Number(token)));
const timeframeTR = value => value === '1D' || value === 'D' ? 'Günlük' : value === '1W' || value === 'W' ? 'Haftalık' : value;
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
    $('#assetCount').textContent = numberTR(d.assets.length);
    const signalLists = await Promise.all(d.assets.map(a => api(`/api/assets/${a.id}/signals`).catch(() => [])));
    const recentSignals = signalLists.flat().sort((a, b) => String(b.signal_date).localeCompare(String(a.signal_date)) || b.id - a.id).slice(0, 30);
    $('#signalCount').textContent = numberTR(signalLists.reduce((total, list) => total + list.length, 0));
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
    $('#assetPicker').innerHTML = d.assets.length ? d.assets.map(a => `<button type="button" class="asset-card${String(a.id) === selected ? ' selected' : ''}" data-asset-id="${a.id}" aria-pressed="${String(a.id) === selected}">
      <span class="asset-symbol">${esc(a.symbol)}</span><span class="asset-price">${a.last_close == null ? '—' : `${numberTR(a.last_close)} ${esc(a.currency)}`}</span>
      <span class="asset-role">${roleTR[a.role] || esc(a.role)}</span><span class="asset-status">${a.data_status === 'ok' ? dateTR(a.price_date || a.last_bar_date) : a.data_status === 'error' ? 'Veri sorunu' : 'Veri bekleniyor'}</span>
    </button>`).join('') : '<div class="rail-empty">Sepetin boş. İlk varlığını ekleyerek başla.</div>';
    $('#assetPicker').querySelectorAll('[data-asset-id]').forEach(button => button.addEventListener('click', () => {
      $('#chartAsset').value = button.dataset.assetId;
      $('#assetPicker').querySelectorAll('.asset-card').forEach(card => { const active = card === button; card.classList.toggle('selected', active); card.setAttribute('aria-pressed', String(active)); });
      load();
    }));
    const rows = d.assets.map(a => `<tr>
      <td class="symbol">${esc(a.symbol)}</td><td>${esc(a.market)}</td>
      <td><span class="role-tag">${roleTR[a.role]}</span></td>
      <td>${numberTR(a.target_weight * 100)}% / ${numberTR(a.max_weight * 100)}%</td>
      <td>${esc(a.currency)}</td>
      <td>${a.last_close == null ? '—' : `${numberTR(a.last_close)} ${esc(a.currency)}`}</td>
      <td>${a.data_status === 'ok' ? dateTR(a.price_date || a.last_bar_date) : a.data_status === 'error' ? esc(dataErrorTR(a.last_error)) : 'İlk veri eşitlemesi bekleniyor'}</td>
      <td class="row-actions"><button class="secondary" onclick="openAssetChart(${a.id})">Grafik</button><button class="secondary" onclick="removeAsset(${a.id})">Sil</button></td>
    </tr>`).join('');
    $('#assets').innerHTML = rows || '<tr><td colspan="8" class="empty">Sepet henüz tanımlanmadı. Varlıklarını ekleyerek başla.</td></tr>';
    if (selected) await window.loadSignalChart(selected);
    const selectedAsset = d.assets.find(a => String(a.id) === selected);
    if (selectedAsset && $('#instrumentSub')) $('#instrumentSub').textContent = `${selectedAsset.market} · ${selectedAsset.currency} · ${roleTR[selectedAsset.role] || selectedAsset.role} · Günlük kapanış`;
    const selectedSignals = recentSignals.filter(s => selectedAsset && Number(s.asset_id) === Number(selectedAsset.id)).slice(0, 8);
    $('#signals').innerHTML = selectedSignals.length ? selectedSignals.map(s => `<div class="signal-item ${s.event === 'take_profit_confirmed' ? 'event-sell' : s.event === 'full_exit' ? 'event-exit' : 'event-buy'}">
      <span class="signal-date">${dateTR(s.signal_date)}</span><div><b>${esc(s.symbol)} · ${eventTR(s.event)}</b>
      <p>${esc(reasonTR(s.reason))} · Göreli güç endeksi (RSI) ${numberTR(s.rsi)} · ${timeframeTR(s.timeframe)}</p></div>
      <span class="signal-buy">${s.execution_price ? `Tahmini işlem ${numberTR(s.execution_price)}` : 'Sonraki seans bekleniyor'}</span>
    </div>`).join('') : '<div class="empty">Seçili varlıkta henüz teyit edilmiş sinyal yok.</div>';
    const st = d.settings;
    for (const key of ['initial_capital_usd', 'monthly_contribution_usd', 'fees_bps', 'slippage_bps', 'forward_start']) {
      const input = $(`[name="${key}"]`);
      if (input && st[key] != null) input.value = key === 'forward_start' ? dateTR(st[key]) : numberTR(st[key]);
    }
    const bt = await api('/api/backtest/status');
    $('#backtest').textContent = `${bt.message} Veri aralığı: ${dateTR(bt.data_range[0])} – ${dateTR(bt.data_range[1])}.`;
    const tg = await api('/api/telegram/preview');
    if (tg.preview) $('#telegramPreview').textContent = telegramTR(tg.preview);
  } catch (error) {
    console.error(error);
  }
}

window.removeAsset = async id => {
  if (confirm('Varlık ve bağlı yerel mum/sinyal kayıtları silinsin mi?')) {
    await api(`/api/assets/${id}`, { method: 'DELETE' });
    load();
  }
};
window.openAssetChart = id => {
  $('#chartAsset').value = String(id);
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
  const query = event.target.value.trim().toLocaleLowerCase('tr-TR');
  $('#assetPicker').querySelectorAll('.asset-card').forEach(card => { card.hidden = !card.querySelector('.asset-symbol').textContent.toLocaleLowerCase('tr-TR').includes(query); });
});
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
    event.target.reset(); $('#assetDialog').close(); load(); watchFirstPriceLoad(added.id);
  } catch (error) { alert(error.message); }
};

$('#chartAsset').addEventListener('change', () => load());
$('#chartAsset').addEventListener('change', () => {
  const value = $('#chartAsset').value;
  $('#assetPicker').querySelectorAll('.asset-card').forEach(card => { const active = card.dataset.assetId === value; card.classList.toggle('selected', active); card.setAttribute('aria-pressed', String(active)); });
});
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
    const data = await api('/api/data/refresh');
    $('#dataInfo').textContent = `Güncelleme tamamlandı: ${numberTR(data.updated)} / ${numberTR(data.results.length)} varlık. Başarısız varlıkların durumu tabloda gösterilir.`;
    await load();
    return data;
  } catch (error) { $('#dataInfo').textContent = error.message; return null; }
  finally { button.disabled = false; button.innerHTML = '⟳ <span>Veriyi güncelle</span>'; }
};
$('#refreshData').onclick = window.refreshMarketData;

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
