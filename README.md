# Bitcoin Daily Analysis Pipeline

Pipeline otomatis yang mengambil data pasar Bitcoin setiap hari, menganalisisnya
secara rule-based, lalu mempublikasikan hasilnya sebagai JSON yang bisa dibaca
konsumen lain (email harian lewat Claude Cowork, dan dashboard terpisah).

```
[Cron 1: GitHub Actions, 07:00 WIB]
fetch.py  ->  analyze.py  ->  commit output/latest.json + output/history/<tgl>.json
                                            |
                          raw.githubusercontent.com/<user>/<repo>/main/output/latest.json
                                            |
[Cron 2: Claude Cowork scheduled task, 08:00 WIB]
                          baca JSON -> susun narasi Bahasa Indonesia -> kirim email
```

**Prinsip utamanya:** semua kesimpulan analisis dihitung deterministik di Python.
LLM hanya menarasikan hasil yang sudah jadi, tidak pernah menentukan sinyal atau
skor. Input yang sama selalu menghasilkan output yang sama, jadi hasilnya bisa
diaudit dan di-backtest.

## Status per fase

| Fase | Isi | Status |
|---|---|---|
| 0 | Setup, validasi konektivitas API | Selesai |
| 1 | `fetch.py` — data mentah dari 3 sumber | Selesai |
| 2 | `analyze.py` — indikator, sinyal, scoring + 48 tes | Selesai |
| 3 | Workflow Actions + commit hasil ke repo | Selesai (perlu di-push) |
| 4 | Scheduled task Cowork → email | Prompt siap di `cowork-task-prompt.md` |
| 5 | Hardening: logging, fallback, tuning threshold | Sebagian; tuning perlu data nyata |
| 6 | Arsip harian + `backfill.py` | Selesai — 90 hari histori sudah terisi |

## Struktur

```
fetch.py                  Fase 1 - ambil data mentah, tanpa analisis
backfill.py               Isi arsip histori ke belakang, sekali jalan
indicators.py             Indikator teknikal, pure Python (tanpa pandas/numpy)
analyze.py                Fase 2 - deteksi sinyal + composite scoring
tests/test_analysis.py    48 tes: indikator, sinyal, scoring, pipeline utuh
cowork-task-prompt.md     Fase 4 - prompt scheduled task Cowork
.github/workflows/
  daily.yml               Cron harian: fetch -> analyze -> commit
  tests.yml               Jalankan pytest tiap push/PR
output/
  latest.json             Hasil analisis terakhir (di-commit)
  history/<tanggal>.json  Arsip harian (di-commit)
  raw.json                Data mentah antara (gitignored)
```

## Menjalankan lokal

```bash
python -m venv .venv
.venv/Scripts/activate          # Windows; Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

python fetch.py                 # -> output/raw.json
python analyze.py               # -> output/latest.json + output/history/<tgl>.json
python backfill.py --days 90    # isi arsip 90 hari ke belakang, sekali saja
pytest -q                       # 48 tes
```

Dependensi runtime cuma `requests`. Indikator sengaja ditulis manual alih-alih
memakai `pandas-ta`: library itu rusak di numpy 2.x (`from numpy import NaN`),
dan lima indikator ini cuma butuh aritmatika biasa — hasilnya nol dependensi
rapuh dan setiap fungsi bisa dites terpisah.

## Sumber data

| Sumber | Data | Catatan |
|---|---|---|
| `data-api.binance.vision` | OHLCV harian BTCUSDT | Wajib; pipeline berhenti kalau gagal |
| `api.alternative.me/fng` | Fear & Greed Index | Opsional; kegagalannya dicatat, pipeline lanjut |
| `api.coingecko.com` | BTC dominance, market cap | Opsional; sama seperti di atas |

**Jangan pakai `api.binance.com`.** Endpoint itu diblokir dari Indonesia maupun
dari runner GitHub Actions (US) — sudah diverifikasi gagal dari kedua sisi.
`data-api.binance.vision` adalah endpoint market data publik Binance dengan
skema `/api/v3/klines` yang identik dan tanpa restriksi geo.

Candle terakhir dari Binance adalah hari yang masih berjalan, jadi `fetch.py`
membuangnya. Semua indikator dihitung dari candle yang sudah final.

## Cara kerja scoring

Sinyal dikelompokkan ke empat kategori, di-skor per kategori (di-clamp ke
−100..+100), baru digabung dengan bobot:

| Kategori | Bobot | Sinyal |
|---|---|---|
| Trend | 0.35 | MA20 vs MA50, golden/death cross, harga vs MA50 |
| Momentum | 0.25 | RSI(14), MACD cross / histogram |
| Sentiment | 0.25 | Fear & Greed Index (contrarian) |
| Volatility | 0.15 | Break Bollinger Band, lonjakan volume |

Pengelompokan ini bukan formalitas. Sinyal dalam satu kategori saling
berkorelasi — RSI oversold, harga di bawah lower band, dan harga di bawah MA
sering menyala bersamaan. Kalau semuanya dijumlah langsung, satu kondisi pasar
terhitung berkali-kali dan skornya melonjak ekstrem tanpa alasan.

Kategori yang datanya hilang (misal Fear & Greed gagal di-fetch) di-skip dan
bobotnya dinormalisasi ulang, bukan dianggap nol — supaya hilangnya satu sumber
data tidak diam-diam menarik skor ke arah netral.

Arah pembacaan sengaja dicampur: **RSI dan Bollinger dibaca mean-reversion**
(oversold = bullish), **MA dan MACD dibaca trend-following**. Keduanya bisa
bertentangan, dan itu memang tujuannya — `scores.by_category` di output
menunjukkan dari mana ketidaksepakatannya datang, bukan menyembunyikannya di
balik satu angka.

Label: `≥50` bullish kuat, `≥20` bullish, `−19..19` netral, `≤−20` bearish,
`≤−50` bearish kuat.

## Skema `output/latest.json`

```jsonc
{
  "generated_at": "2026-09-08T13:00:00+00:00",  // kapan analisis dibuat (UTC)
  "data_as_of":   "2026-09-07",                  // tanggal candle terakhir
  "symbol": "BTCUSDT",
  "price":      { "close": 79112.01, "change_1d_pct": -1.53, "change_7d_pct": ..., ... },
  "indicators": { "rsi14": 62.7, "ma20": ..., "macd_hist": ..., "bb_percent_b": ..., ... },
  "sentiment":  { "fear_greed": {...}, "btc_dominance_pct": 58.91, ... },
  "signals": [
    { "id": "ma20_above_ma50", "category": "trend", "direction": "bullish",
      "score": 40, "label": "...", "detail": "..." }
  ],
  "scores": {
    "composite": -4, "label": "netral",
    "by_category": { "trend": 70, "momentum": -75, "sentiment": -38 },
    "weights_used": { "trend": 0.35, "momentum": 0.25, "sentiment": 0.25 }
  },
  "meta": { "candles_used": 200, "fetch_errors": {}, "scoring_version": 1 }
}
```

Konsumen JSON ini **wajib mengecek `generated_at`** sebelum memakainya, dan
tidak boleh berasumsi cron sudah jalan berdasarkan jam saja (lihat bagian
berikutnya).

## Setup GitHub (Fase 3)

```bash
git init && git add . && git commit -m "init: pipeline analisis BTC"
git branch -M main
git remote add origin https://github.com/<user>/<repo>.git
git push -u origin main
```

Lalu di repo GitHub:

1. **Settings → Actions → General → Workflow permissions** → pilih
   *Read and write permissions*. Tanpa ini step commit gagal dengan 403.
2. **Actions → Daily BTC Analysis → Run workflow** untuk mencoba manual dulu,
   jangan menunggu jadwal.
3. Pastikan `https://raw.githubusercontent.com/<user>/<repo>/main/output/latest.json`
   bisa dibuka di browser mode incognito.

## Repo ini publik — dan itu disengaja

Isinya kode analisis dan data pasar yang memang sudah publik; tidak ada
kredensial maupun data pribadi di dalamnya. Semua rahasia (kalau nanti ada)
masuk GitHub Secrets, tidak pernah ke dalam kode.

Publik dipilih karena `raw.githubusercontent.com` mengabaikan header
`Authorization`. Kalau repo ini privat, `output/latest.json` tidak bisa
diambil lewat raw URL biasa, dan scheduled task Cowork di Fase 4 jadi buntu —
task itu memfetch URL dari teks prompt, dan menaruh personal access token di
dalam prompt bukan praktik yang baik.

Dengan repo publik, konsumen tinggal memfetch:

```
https://raw.githubusercontent.com/Timotius2005/bitcoin-daily-pipeline/main/output/latest.json
```

Dashboard tetap mendukung repo privat lewat `BTC_PIPELINE_TOKEN` kalau suatu
saat repo ini ditutup — jalur itu sudah ada dan ada tesnya.

## Hal yang perlu diawasi

**Jadwal Actions tidak presisi.** Delay 5–30 menit itu normal dan run bisa
di-skip saat platform padat. Karena itu jeda ke task Cowork dibuat 1 jam, dan
prompt Cowork tetap memverifikasi `generated_at` sendiri.

**Timezone.** Cron Actions selalu UTC. `0 0 * * *` = 07:00 WIB.

**Workflow terjadwal dinonaktifkan setelah 60 hari repo tidak aktif.** Commit
harian dari pipeline ini umumnya cukup untuk menjaganya tetap hidup, tapi kalau
suatu saat cron berhenti diam-diam, ini hal pertama yang perlu dicek — GitHub
mengirim email pemberitahuan saat menonaktifkannya.

**Threshold sinyal masih nilai default** (RSI 30/70, volume 2x, lookback cross
3 hari). Ini sengaja belum di-tuning: penyesuaiannya baru masuk akal setelah ada
data nyata beberapa minggu di `output/history/`, bukan ditebak di awal.

## Konsumen data ini

Repo dashboard terpisah ([../bitcoin-dashboard](../bitcoin-dashboard)) membaca
`output/latest.json` dan `output/history/` sebagai konsumen read-only, dan tidak pernah
menulis balik ke sini.

`backfill.py` menghitung ulang analisis untuk N hari terakhir dan menulisnya ke
`output/history/`, supaya konsumen langsung punya deret waktu tanpa menunggu
berminggu-minggu. Fear & Greed historis diambil asli dari endpoint histori
Alternative.me; dominansi BTC dibiarkan null untuk baris backfill karena CoinGecko
tidak menyediakan historinya gratis — jangan bandingkan kedua jenis baris itu seolah
setara.

## Catatan

Analisis rule-based otomatis untuk keperluan belajar dan portfolio. Bukan saran
keuangan.
