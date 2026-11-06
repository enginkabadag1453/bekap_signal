# BEKAP Esinli Sepet Sinyal Sistemi (MVP)

Yerel ve özel kullanım için bağımsız uygulama. Ana seyahat uygulamasından ayrı çalışır.

## Başlatma

Windows'ta `start.bat` çalıştır ve `http://127.0.0.1:8766` adresini aç. İlk açılışta kullanıcının verdiği başlangıç sepeti SQLite'a eklenir:

- AAPL — ömürlük
- NVDA — trend
- GLD — ömürlük
- BTC — ömürlük
- LLY — trend
- TUPRS — ömürlük
- CPER — dönemsel

Hedef/üst ağırlıklar belirtilmediği için sıfır/boş tutulur. Sembol eşlemeleri TradingView grafiğinde kullanılır. Diğer enstrümanlar daha sonra eklenebilir.

## Fiyat verisi

Grafik, yerel Lightweight Charts motoruyla çizilir; TradingView iframe'i kullanılmaz. Ana fiyat panelinde çizgi/mum, hacim, Bollinger Bands (20,2), EMA200, son alım/kâr-al seviyeleri ve EKLE/KAR AL işaretleri bulunur. Alt panel, kullanıcı tarafından sağlanan BEKAPTREND Pine kodundaki RSI/ADX çizimlerini ve seviyelerini gösterir. Günlük/haftalık periyot, 5Y/1Y/6A/3A/1A aralıkları ve koyu/açık tema kullanılabilir.

Kapanmış günlük OHLC barları API anahtarı olmadan Yahoo Finance'in grafik akışından alınır. İlk eşitlemede beş yıllık geçmiş yüklenir; veri altı saatte bir yenilenir:

- ABD hisseleri/ETF'ler: Yahoo Finance sembolü (AAPL, NVDA, GLD, LLY, CPER).
- BTC/USD: Yahoo Finance `BTC-USD`; kaynak erişilemezse Coinbase public candles yedeği.
- TUPRS: Yahoo Finance `TUPRS.IS`.
- Alpha Vantage ve Twelve Data anahtarları varsa yedek sağlayıcı olarak kullanılabilir; ilk kurulumda anahtar gerekmez. Her sembolün kaynağı ve son kapanış tarihi tabloda/grafik başlığında gösterilir.

Yahoo Finance halka açık chart endpoint'i anahtarsızdır ancak borsa tarafından yetkilendirilmiş gerçek zamanlı kotasyon garantisi değildir. ABD/BIST hisse ve ETF grafikleri son tamamlanmış günlük mumu gösterir; gün içi/canlı akış gibi sunulmaz. Günlük mumun bugünkü tamamlanmamış barı grafiğe/sinyale alınmaz. Sağlayıcı hatası eski fiyatı yeniymiş gibi göstermez; tabloda son başarılı bar tarihi kalır.

Fiyat kaynağı TradingView ile aynı veri borsası/adjustment ayarını garanti etmediğinden grafiğin mumları ve sinyal tarihleri her piyasada birebir eşleşmeyebilir. Grafik Pine'daki AUTO parametreleri ve sinyal durum makinesini kullanır; BEKAP sitesinin sunucu tarafı fiyat kaynağı/kapalı kod davranışı ile birebirlik iddiası yoktur. Kaynak, tarih ve sinyal farkları açıkça görünür.

## Sinyal/strateji sınırı

- Grafik ve sinyal günlüğü, kullanıcı dosyasındaki BEKAPRSI AUTO Pine durum makinesinden port edilmiştir; sembole özel AUTO tabloları `bekap_params.json` içindedir. BEKAPTREND RSI/ADX alt paneli de kullanıcı Pine kaynağından çizilir.
- Sinyal kapanmış mumda teyit edilir; varsayımsal işlem fiyatı sonraki seans açılışıdır. Ham indikatör işareti kendi başına pozisyon/emir oluşturmaz.
- Grafik çizimi, veri tarih aralığı ve Pine işaretleri birleştirildi. Haftalık %21 fren ile portföy rolüne göre kâr payı/tam çıkış muhasebesi henüz bu grafik portuna bağlanmadı; bu davranışları aynı sinyal motora almak sonraki sistem adımıdır.
- Backtest, pozisyon/nakit muhasebesi, tarihsel kur, temettü/bölünme işleme ve maliyet modeli tamamlanana kadar performans üretmez. 02.01.2027 ileri-test kesimi gelecekteki sonuç gibi gösterilmez.
- Telegram şimdilik önizleme/outbox; otomatik teslim yoktur. Emir göndermez.

Uygulama yalnız `127.0.0.1` üzerinde dinler. Bu MVP internete açılmamalıdır; kullanıcı hesabı/tenant yetkileri henüz eklenmedi. Fonlar ikinci aşamadır.
