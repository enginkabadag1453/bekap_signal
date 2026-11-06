(() => {
  'use strict';
  const $ = s => document.querySelector(s);
  const chartState = { instance: null, data: null };
  const COLORS = { price: '#ffe55d', upper: '#00aaf0', lower: '#00aaf0', basis: '#f47b17', ema: '#ffa500', buy: '#42b765', sell: '#ff4d55', rsi: '#3678ff', adx: '#ff9900', up: '#118b77', down: '#a64254' };
  const n = value => value == null || !Number.isFinite(Number(value)) ? '—' : Number(value).toLocaleString('tr-TR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const dateText = value => {
    if (!value) return '—';
    let d;
    if (typeof value === 'string') d = new Date(`${value.slice(0, 10)}T12:00:00`);
    else if (typeof value === 'object' && 'year' in value) d = new Date(value.year, value.month - 1, value.day, 12);
    else d = new Date(value * 1000);
    return new Intl.DateTimeFormat('tr-TR', { day: '2-digit', month: '2-digit', year: 'numeric' }).format(d);
  };
  const pct = value => Number(value).toLocaleString('tr-TR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const filterTR = { NONE: 'filtre yok', ABOVE: '200 günlük ortalamanın üstünde', BELOW: '200 günlük ortalamanın altında' };

  class RegionFill {
    constructor(rows, upperKey, lowerKey, step = false, positive = false) {
      this.rows = rows; this.upperKey = upperKey; this.lowerKey = lowerKey; this.step = step; this.positive = positive;
    }
    attached({ chart, series, requestUpdate }) { this.chart = chart; this.series = series; this.requestUpdate = requestUpdate; requestUpdate(); }
    paneViews() { return [{ zOrder: () => 'bottom', renderer: () => ({ draw: target => this.draw(target) }) }]; }
    draw(target) {
      if (!this.chart || !this.series) return;
      target.useMediaCoordinateSpace(({ context }) => {
        const ctx = context, scale = this.chart.timeScale();
        const value = (row, key) => typeof key === 'number' ? key : row[key];
        for (let i = 0; i < this.rows.length - 1; i++) {
          const a = this.rows[i], b = this.rows[i + 1];
          let u = value(a, this.upperKey), l = value(a, this.lowerKey);
          const u2 = value(b, this.upperKey), l2 = value(b, this.lowerKey);
          if ([u, l, u2, l2].some(v => v == null || !Number.isFinite(Number(v)))) continue;
          if (!this.step && typeof this.lowerKey === 'number' && u >= this.lowerKey && u2 >= this.lowerKey) continue;
          const x = scale.timeToCoordinate(a.date), xx = scale.timeToCoordinate(b.date);
          if (x == null || xx == null) continue;
          const top1 = this.series.priceToCoordinate(Math.max(value(a, this.upperKey), value(a, this.lowerKey)));
          const bot1 = this.series.priceToCoordinate(Math.min(value(a, this.upperKey), value(a, this.lowerKey)));
          const top2v = this.step ? Math.max(u2, l2) : Math.max(u2, l2);
          const bot2v = this.step ? Math.min(u2, l2) : Math.min(u2, l2);
          const top2 = this.series.priceToCoordinate(top2v), bot2 = this.series.priceToCoordinate(bot2v);
          if ([top1, bot1, top2, bot2].some(v => v == null)) continue;
          const buyZone = this.step ? value(a, this.upperKey) < value(a, this.lowerKey) : this.positive;
          ctx.fillStyle = buyZone ? 'rgba(51,181,100,.12)' : 'rgba(240,65,75,.12)';
          ctx.beginPath(); ctx.moveTo(x, top1); ctx.lineTo(xx, top2); ctx.lineTo(xx, bot2); ctx.lineTo(x, bot1); ctx.closePath(); ctx.fill();
        }
      });
    }
  }

  function addLine(chart, rows, key, color, pane = 0, options = {}) {
    const series = chart.addSeries(LightweightCharts.LineSeries, {
      color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
      crosshairMarkerVisible: false, ...options
    }, pane);
    series.setData(rows.map(r => r[key] == null ? { time: r.date } : { time: r.date, value: Number(r[key]) }));
    return series;
  }

  function setRange(range) {
    if (!chartState.instance || !chartState.data?.bars?.length) return;
    const bars = chartState.data.bars;
    const days = { '5Y': 1825, '1Y': 365, '6M': 183, '3M': 92, '1M': 31 }[range] || 365;
    const last = new Date(`${bars[bars.length - 1].date}T12:00:00`);
    const cutoff = new Date(last); cutoff.setDate(cutoff.getDate() - days);
    let first = bars.findIndex(b => new Date(`${b.date}T12:00:00`) >= cutoff);
    if (first < 0) first = 0;
    chartState.instance.timeScale().setVisibleLogicalRange({ from: first, to: bars.length + 3 });
  }
  window.setChartRange = setRange;

  window.loadSignalChart = async function (assetId) {
    const host = $('#chart');
    if (!host) return;
    if (!assetId) { host.innerHTML = '<div class="chartplaceholder">Grafik için önce bir varlık seç.</div>'; return; }
    host.innerHTML = '<div class="chartplaceholder">Günlük kapanışlar ve göstergeler yükleniyor…</div>';
    try {
      const timeframe = $('#chartInterval')?.value || 'D';
      const data = await api(`/api/assets/${assetId}/chart?timeframe=${timeframe}`);
      chartState.data = data;
      if (!data.bars?.length) {
        if ($('#instrumentName')) $('#instrumentName').textContent = `${data.symbol || 'Varlık'} · Fiyat verisi bekleniyor`;
        if ($('#instrumentMarket')) $('#instrumentMarket').textContent = data.market || data.symbol || 'SEPET';
        if ($('#instrumentPrice')) $('#instrumentPrice').textContent = '—';
        if ($('#instrumentChange')) { $('#instrumentChange').textContent = 'Kapanmış mum henüz yok'; $('#instrumentChange').className = ''; }
        if ($('#instrumentSub')) $('#instrumentSub').textContent = `${data.currency || ''} · Tamamlanmış fiyat verisi bulunamadı`;
        if ($('#inspectorRsi')) $('#inspectorRsi').textContent = '—';
        if ($('#inspectorAdx')) $('#inspectorAdx').textContent = '—';
        host.innerHTML = `<div class="chartplaceholder">${data.error || 'Kapanmış mum verisi henüz yok.'}<br><small>Otomatik veri yenilemesi tamamlanınca grafiği aç.</small></div>`;
        $('#chartMeta').textContent = data.source ? `Kaynak: ${data.source} · Veri bekleniyor` : 'İlk veri eşitlemesi bekleniyor';
        $('#detailSignals').textContent = 'Bu varlık için henüz kapanmış mum ve sinyal bulunmuyor.';
        return;
      }
      draw(data);
    } catch (error) {
      host.innerHTML = `<div class="chartplaceholder">Grafik yüklenemedi: ${String(error.message || error)}<br><small>Veriyi şimdi güncelle düğmesiyle yeniden deneyebilirsin.</small></div>`;
    }
  };

  function draw(data) {
    const host = $('#chart'), dark = document.documentElement.dataset.theme !== 'light';
    const bg = dark ? '#090c0b' : '#ffffff', fg = dark ? '#aeb7b2' : '#607068';
    const priceHighlight = dark ? COLORS.price : '#123cba';
    const priceOutline = dark ? '#090c0b' : '#ffffff';
    const grid = dark ? '#1c2220' : '#edf1ec', border = dark ? '#29312d' : '#dfe5df';
    const bars = data.bars, rows = data.indicators, lineMode = $('#chartStyle').value === 'line', p = data.profile || {};
    // Older running servers may not yet add SMA200 to the response; calculate it from the same completed bars.
    for (let i = 0; i < rows.length; i++) {
      if (rows[i].sma200 == null && i >= 199) {
        rows[i].sma200 = bars.slice(i - 199, i + 1).reduce((sum, bar) => sum + Number(bar.close), 0) / 200;
      }
    }
    if (chartState.instance) { chartState.instance.remove(); chartState.instance = null; }
    host.replaceChildren();
    const chart = LightweightCharts.createChart(host, {
      autoSize: true, layout: { background: { type: 'solid', color: bg }, textColor: fg, attributionLogo: true, fontFamily: 'Arial, sans-serif', fontSize: 11 },
      grid: { vertLines: { color: grid }, horzLines: { color: grid } },
      rightPriceScale: { borderColor: border, scaleMargins: { top: .08, bottom: .12 }, entireTextOnly: true },
      timeScale: { borderColor: border, rightOffset: 5, barSpacing: 7, timeVisible: false, secondsVisible: false, tickMarkFormatter: t => dateText(t) },
      crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
      localization: { locale: 'tr-TR', priceFormatter: n, timeFormatter: dateText },
      handleScroll: { mouseWheel: true, pressedMouseMove: true }, handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: true }
    });
    chartState.instance = chart;
    const showLevels = $('#chartLevels')?.checked ?? true;
    const showSignals = $('#chartSignals')?.checked ?? true;
    const main = lineMode
      ? chart.addSeries(LightweightCharts.LineSeries, { color: priceHighlight, lineWidth: 3, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: true, crosshairMarkerBackgroundColor: priceHighlight, crosshairMarkerBorderColor: priceOutline })
      : chart.addSeries(LightweightCharts.CandlestickSeries, { upColor: COLORS.up, downColor: COLORS.down, wickUpColor: COLORS.up, wickDownColor: COLORS.down, borderVisible: false, priceLineVisible: false, lastValueVisible: false });
    main.setData(bars.map(b => lineMode ? { time: b.date, value: Number(b.close) } : { time: b.date, open: Number(b.open), high: Number(b.high), low: Number(b.low), close: Number(b.close) }));
    const minMove = data.currency === 'TRY' ? .01 : .01;
    main.applyOptions({ priceFormat: { type: 'custom', minMove, formatter: n } });
    main.createPriceLine({ price: Number(bars.at(-1).close), color: priceHighlight, lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dotted, axisLabelVisible: true, title: '' });

    const bb = $('#chartBB').checked, sma = $('#chartSma').checked;
    let bbUpper = null, bbMiddle = null, bbLower = null, smaSeries = null;
    if (bb) {
      bbUpper = addLine(chart, rows, 'bb_upper', COLORS.upper, 0, { lineWidth: 1, lastValueVisible: false });
      bbMiddle = addLine(chart, rows, 'bb_mid', COLORS.basis, 0, { lineWidth: 1, lastValueVisible: false });
      bbLower = addLine(chart, rows, 'bb_lower', COLORS.lower, 0, { lineWidth: 1, lastValueVisible: false });
    }
    if (sma) smaSeries = addLine(chart, rows, 'sma200', COLORS.ema, 0, { lineWidth: 2, lastValueVisible: true });
    let buyLine = null, takeProfitLine = null;
    if (showLevels) {
      buyLine = addLine(chart, rows, 'last_buy', COLORS.buy, 0, { lineWidth: 2, lastValueVisible: true, lineType: LightweightCharts.LineType.WithSteps });
      takeProfitLine = addLine(chart, rows, 'last_tp', COLORS.sell, 0, { lineWidth: 2, lastValueVisible: true, lineType: LightweightCharts.LineType.WithSteps });
      buyLine.attachPrimitive(new RegionFill(rows, 'last_buy', 'last_tp', true));
    }

    const volume = chart.addSeries(LightweightCharts.HistogramSeries, {
      priceScaleId: 'volume', priceLineVisible: false, lastValueVisible: false,
      priceFormat: { type: 'volume' },
    }, 0);
    volume.setData(bars.filter(b => b.volume != null).map(b => ({
      time: b.date, value: Number(b.volume), color: Number(b.close) >= Number(b.open) ? 'rgba(26,145,121,.72)' : 'rgba(173,64,83,.72)'
    })));
    chart.priceScale('volume').applyOptions({ visible: false, scaleMargins: { top: .86, bottom: 0 } });

    const markers = [];
    if (showSignals) {
      for (const r of rows) {
        if (r.buy) markers.push({ time: r.date, position: 'belowBar', shape: 'arrowUp', color: COLORS.buy, text: `AL / EKLE (${r.buy_diff == null ? 'İlk' : `${r.buy_diff > 0 ? '+' : ''}${pct(r.buy_diff)}%`})`, size: 2 });
        if (r.tp) markers.push({ time: r.date, position: 'aboveBar', shape: 'arrowDown', color: COLORS.sell, text: `SAT / KÂR AL (${r.tp_diff == null ? 'İlk' : `${r.tp_diff > 0 ? '+' : ''}${pct(r.tp_diff)}%`})`, size: 2 });
      }
      for (const action of data.actions || []) {
        if (action.kind !== 'dividend') continue;
        const matching = bars.find(b => b.date >= action.date);
        if (matching) markers.push({ time: matching.date, position: 'belowBar', shape: 'circle', color: '#00b9ba', text: 'T', size: 1 });
      }
    }
    markers.sort((a, b) => String(a.time).localeCompare(String(b.time)));
    LightweightCharts.createSeriesMarkers(main, markers.slice(-500));

    const rsiLine = addLine(chart, rows, 'rsi', COLORS.rsi, 1, {
      lineWidth: 1, lastValueVisible: true, autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 100 } })
    });
    for (const [threshold, isAbove, color] of [[65, true, COLORS.sell], [40, false, COLORS.buy]]) {
      let segment = [];
      const flush = () => {
        if (!segment.length) return;
        const line = chart.addSeries(LightweightCharts.LineSeries, { color, lineWidth: 3, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }, 1);
        line.setData(segment); segment = [];
      };
      for (const r of rows) {
        if (r.rsi != null && (isAbove ? r.rsi >= threshold : r.rsi <= threshold)) segment.push({ time: r.date, value: r.rsi });
        else flush();
      }
      flush();
    }
    // Keep ADX on its own pane and scale so it cannot collide with similarly-valued RSI levels.
    const adx = addLine(chart, rows, 'adx', COLORS.adx, 2, { lineWidth: 2, lastValueVisible: false, autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 100 } }) });
    adx.attachPrimitive(new RegionFill(rows, 'adx', 25, false, false));
    const matchedBuy = Number(p.buy ?? 40), matchedSell = Number(p.tp ?? 65);
    for (const value of [30, 40, 50, 70, 80]) {
      if (value === matchedBuy || value === matchedSell) continue;
      rsiLine.createPriceLine({ price: value, color: 'rgba(153,163,173,.34)', lineStyle: LightweightCharts.LineStyle.Dotted, lineWidth: 1, axisLabelVisible: false, title: '' });
    }
    // Labels live in a compact legend instead of stacking on the price scale.
    rsiLine.createPriceLine({ price: matchedBuy, color: COLORS.buy, lineStyle: LightweightCharts.LineStyle.Dashed, lineWidth: 2, axisLabelVisible: false, title: '' });
    rsiLine.createPriceLine({ price: matchedSell, color: COLORS.sell, lineStyle: LightweightCharts.LineStyle.Dashed, lineWidth: 2, axisLabelVisible: false, title: '' });
    adx.createPriceLine({ price: 25, color: 'rgba(255,153,0,.8)', lineStyle: LightweightCharts.LineStyle.Dashed, lineWidth: 1, axisLabelVisible: false, title: '' });
    const latestIndicator = rows.at(-1);
    if (latestIndicator?.rsi != null) rsiLine.createPriceLine({ price: Number(latestIndicator.rsi), color: 'rgba(54,120,255,.32)', lineStyle: LightweightCharts.LineStyle.Dotted, lineWidth: 1, axisLabelVisible: false, title: '' });
    if (latestIndicator?.adx != null) adx.createPriceLine({ price: Number(latestIndicator.adx), color: 'rgba(255,153,0,.34)', lineStyle: LightweightCharts.LineStyle.Dotted, lineWidth: 1, axisLabelVisible: false, title: '' });
    const buyRsi = rows.filter(r => r.buy && Number.isFinite(Number(r.rsi))).map(r => Number(r.rsi));
    const sellRsi = rows.filter(r => r.tp && Number.isFinite(Number(r.rsi))).map(r => Number(r.rsi));
    const historyRanges = {
      buy: buyRsi.length ? [Math.min(...buyRsi), Math.max(...buyRsi)] : null,
      sell: sellRsi.length ? [Math.min(...sellRsi), Math.max(...sellRsi)] : null
    };
    if ($('#rsiHistoryBuy')) $('#rsiHistoryBuy').textContent = historyRanges.buy ? `${n(historyRanges.buy[0])} – ${n(historyRanges.buy[1])}` : 'Yeterli AL dönüşü yok';
    if ($('#rsiHistorySell')) $('#rsiHistorySell').textContent = historyRanges.sell ? `${n(historyRanges.sell[0])} – ${n(historyRanges.sell[1])}` : 'Yeterli SAT dönüşü yok';
    const indicatorLegend = $('#indicatorLegend');
    if (indicatorLegend) indicatorLegend.innerHTML = `<span class="legend-rsi">RSI <b>${n(latestIndicator?.rsi)}</b></span><span class="legend-buy">AL eşiği <b>${n(matchedBuy)}</b></span><span class="legend-sell">SAT eşiği <b>${n(matchedSell)}</b></span><span class="legend-adx">ADX <b>${n(latestIndicator?.adx)}</b> · eşik 25</span><span class="legend-muted">30 · 50 · 70 · 80 seviyeleri grafikte</span>`;
    LightweightCharts.createSeriesMarkers(rsiLine, rows.filter(r => r.cross_up || r.cross_down).map(r => ({
      time: r.date, position: 'atPriceMiddle', price: r.rsi, shape: r.cross_up ? 'arrowUp' : 'arrowDown', color: '#111111', size: 1
    })), { autoScale: false });

    // Draw a contrasting outline and a bright top stroke so price stays readable above indicator overlays.
    if (lineMode) {
      const priceData = bars.map(b => ({ time: b.date, value: Number(b.close) }));
      const outline = chart.addSeries(LightweightCharts.LineSeries, {
        color: priceOutline, lineWidth: 7, priceLineVisible: false, lastValueVisible: false,
        crosshairMarkerVisible: false, crosshairMarkerRadius: 0
      }, 0);
      outline.setData(priceData);
      const highlight = chart.addSeries(LightweightCharts.LineSeries, {
        color: priceHighlight, lineWidth: 3, priceLineVisible: false, lastValueVisible: false,
        crosshairMarkerVisible: false, crosshairMarkerRadius: 0
      }, 0);
      highlight.setData(priceData);
    }

    const panes = chart.panes();
    // Stretch factors set stable pane proportions; setHeight() is only a one-time redistribution hint.
    if (panes[0]) panes[0].setStretchFactor(60);
    if (panes[1]) panes[1].setStretchFactor(26);
    if (panes[2]) panes[2].setStretchFactor(14);
    const range = $('#chartRange')?.value || '1Y';
    setRange(range);

    const timeDescription = data.current_bar_provisional
      ? `Güncel hafta geçici · tarih ${dateText(data.last_bar_date)} · fiyat verisi ${dateText(data.last_daily_bar_date)} · son tamamlanan hafta ${dateText(data.last_confirmed_bar_date)}`
      : `Son tamamlanmış mum ${dateText(data.last_bar_date)}`;
    $('#chartMeta').textContent = `${data.source || 'Kaynak bekleniyor'} · ${data.currency} · ${timeDescription} · ${n(bars.length)} ${data.timeframe === 'D' ? 'günlük' : 'haftalık'} mum · Kurallar ${p.custom ? 'varlığa özel' : 'genel'}: AL ${n(p.buy)} / SAT ${n(p.tp)} / RSI üst eşiği ${n(p.overbought)} · ${filterTR[p.filter] || 'filtre yok'}`;
    const latest = rows.at(-1), lastBar = bars.at(-1), previousBar = bars.at(-2);
    const instrumentNames = { AAPL: 'Apple Inc.', NVDA: 'NVIDIA Corporation', GLD: 'SPDR Gold Shares', BTC: 'Bitcoin', LLY: 'Eli Lilly and Company', TUPRS: 'Tüpraş', CPER: 'United States Copper Index Fund', THYAO: 'Türk Hava Yolları' };
    const instrumentName = $('#instrumentName');
    if (instrumentName) instrumentName.textContent = `${data.symbol} · ${instrumentNames[data.symbol] || data.symbol}`;
    const instrumentMarket = $('#instrumentMarket');
    if (instrumentMarket) instrumentMarket.textContent = data.market || data.symbol;
    const instrumentPrice = $('#instrumentPrice');
    if (instrumentPrice) instrumentPrice.textContent = `${n(lastBar.close)} ${data.currency}`;
    const instrumentChange = $('#instrumentChange');
    if (instrumentChange) {
      const change = previousBar && Number(previousBar.close) ? (Number(lastBar.close) / Number(previousBar.close) - 1) * 100 : null;
      instrumentChange.textContent = change == null ? `${dateText(data.last_bar_date)} · kapanış` : `${change >= 0 ? '+' : ''}${pct(change)}% · ${dateText(data.last_bar_date)}${data.current_bar_provisional ? ' · geçici hafta' : ''}`;
      instrumentChange.className = change == null ? '' : change >= 0 ? 'positive' : 'negative';
    }
    const instrumentSub = $('#instrumentSub');
    if (instrumentSub) instrumentSub.textContent = `${data.market || 'Piyasa'} · ${data.currency} · ${data.timeframe === 'D' ? 'Günlük kapanış' : data.current_bar_provisional ? 'Haftalık mum · geçici, kapanmış günlük verilerle' : 'Haftalık kapanış'}`;
    if ($('#inspectorRsi')) $('#inspectorRsi').textContent = n(latest.rsi);
    if ($('#inspectorAdx')) $('#inspectorAdx').textContent = n(latest.adx);
    if ($('#buyThreshold')) $('#buyThreshold').textContent = n(p.buy);
    if ($('#sellThreshold')) $('#sellThreshold').textContent = n(p.tp);
    const timeframePill = document.querySelector('.timeframe-pill');
    if (timeframePill) timeframePill.textContent = data.timeframe === 'D' ? 'GÜNLÜK' : 'HAFTALIK';
    $('#chartLegend').textContent = `${data.symbol} · ${data.timeframe === 'D' ? 'Günlük' : data.current_bar_provisional ? 'Hafta başlangıcı ' + dateText(data.last_bar_date) + ' · geçici veri ' + dateText(data.last_daily_bar_date) : 'Haftalık'} · Kapanış ${n(lastBar.close)} ${data.currency} · Hacim ${n(lastBar.volume)} · Bollinger (20, 2): orta ${n(latest.bb_mid)} / üst ${n(latest.bb_upper)} / alt ${n(latest.bb_lower)} · SMA 200 ${n(latest.sma200)} · RSI ${n(latest.rsi)} · ADX ${n(latest.adx)}`;
    const signals = rows.filter(r => r.buy || r.tp).slice(-30).reverse();
    $('#detailSignals').innerHTML = signals.length ? signals.map(r => `<div class="signal-item"><span class="signal-date">${dateText(r.date)}</span><div><b>${r.buy ? 'EKLE' : 'KÂR AL'} · ${data.symbol}</b><p>Kapanış ${n(bars.find(b => b.date === r.date)?.close)} ${data.currency} · Göreli güç (RSI) ${n(r.rsi)} · Kullanılan ayar: ${p.ticker || data.symbol}</p></div><span class="signal-buy">${data.timeframe === 'D' ? 'Günlük' : 'Haftalık'}</span></div>`).join('') : '<div class="empty">Gösterilen dönemde ve tamamlanmış mumlarda EKLE veya KÂR AL işareti yok.</div>';
    chart.subscribeCrosshairMove(param => {
      const price = param.seriesData.get(main), r = param.seriesData.get(rsiLine), a = param.seriesData.get(adx);
      if (!price) return;
      const close = lineMode ? price.value : price.close;
      const upper = bbUpper && param.seriesData.get(bbUpper), middle = bbMiddle && param.seriesData.get(bbMiddle), lower = bbLower && param.seriesData.get(bbLower), smaValue = smaSeries && param.seriesData.get(smaSeries);
      $('#chartLegend').textContent = `${dateText(price.time)} · ${lineMode ? `Kapanış ${n(price.value)}` : `Açılış ${n(price.open)} · En yüksek ${n(price.high)} · En düşük ${n(price.low)} · Kapanış ${n(close)}`} ${data.currency} · Hacim ${n(bars.find(b => b.date === price.time)?.volume)} · Bollinger orta/üst/alt ${n(middle?.value)} / ${n(upper?.value)} / ${n(lower?.value)} · 200 günlük basit ortalama (SMA) ${n(smaValue?.value)} · Göreli güç (RSI) ${n(r?.value)} · Trend gücü (ADX) ${n(a?.value)}`;
    });
  }
})();
