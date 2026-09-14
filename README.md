# Bitcoin Daily Analysis Pipeline

Pipeline otomatis yang mengambil data pasar Bitcoin setiap hari, menganalisisnya
secara rule-based, lalu mempublikasikan hasilnya sebagai JSON yang bisa dibaca
konsumen lain (email harian lewat Claude Cowork, dan dashboard terpisah).

```
[Cron 1: GitHub Actions, 07:37 WIB]
fetch.py  ->  analyze.py  ->  commit output/latest.json + output/history/<tgl>.json
                                            |
                          raw.githubusercontent.com/<user>/<repo>/main/output/latest.json
                                            |
[Cron 2: Claude Cowork scheduled task, 09:00 WIB]
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
| 6 | Arsip harian + `backfill.py` | Selesai — 96 hari histori, dihitung ulang dengan scoring v2 |
| 7 | Indikator ADX, ATR, OBV (scoring v2) | Selesai |
| 8 | `backtest.py` — evaluasi v1 vs v2 selama 730 hari | Selesai — lihat [bagian Backtest](#backtest-seberapa-bagus-analisisnya) |

## Struktur

```
fetch.py                  Fase 1 - ambil data mentah, tanpa analisis
backfill.py               Isi arsip histori ke belakang, atau hitung ulang (--rescore)
backtest.py               Evaluasi skor v1 vs v2 terhadap return sesudahnya
indicators.py             Indikator teknikal, pure Python (tanpa pandas/numpy)
analyze.py                Fase 2 - deteksi sinyal + composite scoring
tests/                    85 tes: indikator, sinyal, scoring v1/v2, backtest
cowork-task-prompt.md     Fase 4 - prompt scheduled task Cowork
.github/workflows/
  daily.yml               Cron harian: fetch -> analyze -> commit
  tests.yml               Jalankan pytest tiap push/PR
output/
  latest.json             Hasil analisis terakhir (di-commit)
  history/<tanggal>.json  Arsip harian (di-commit)
  backtest/               REPORT.md, report.json, equity.csv (di-commit)
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
python backfill.py --days 96 --rescore   # hitung ulang arsip saat aturan scoring berubah
python backtest.py --days 730   # -> output/backtest/REPORT.md
pytest -q                       # 85 tes
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
| Trend | 0.35 | MA20 vs MA50, golden/death cross, harga vs MA50 — diredam ×0,5 saat ADX(14) < 20 |
| Momentum | 0.25 | RSI(14), MACD cross / histogram |
| Sentiment | 0.25 | Fear & Greed Index (contrarian) |
| Volatilitas & volume | 0.15 | Break Bollinger Band, lonjakan volume, konfirmasi/divergensi OBV |

**Scoring v2** menambahkan tiga indikator yang membawa informasi yang belum ada
di v1, bukan sekadar variasi dari yang sudah ada:

- **ADX** mengukur *kekuatan* tren, bukan arahnya. Saat ADX < 20 pasar cenderung
  sideways dan sinyal MA kurang andal, jadi skor kategori tren dikali 0,5.
- **OBV** menilai apakah volume mendukung arah harga: konfirmasi (±25) atau
  divergensi (±20).
- **ATR** hanya dilaporkan sebagai rezim volatilitas (persentil dalam 180 hari).
  Volatilitas tidak punya arah, jadi ia menjadi sinyal `"kind": "info"` dengan
  skor 0 dan **tidak** ikut dihitung. Kategori yang cuma berisi sinyal info tidak
  dianggap aktif, supaya tidak menarik komposit ke netral.

Setiap penyesuaian skor tercatat di `scores.adjustments` (sebelum, sesudah,
alasan), jadi skor akhir tetap bisa diaudit dari JSON saja. v1 tetap bisa
dijalankan dengan `python analyze.py --scoring-version 1`.

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
    { "id": "ma20_above_ma50", "category": "trend", "kind": "score", "direction": "bullish",
      "score": 40, "label": "...", "detail": "..." }
  ],
  "scores": {
    "composite": -4, "label": "netral",
    "by_category": { "trend": 70, "momentum": -75, "sentiment": -38 },
    "weights_used": { "trend": 0.35, "momentum": 0.25, "sentiment": 0.25 },
    "adjustments": []   // atau [{ "category": "trend", "before": 70, "after": 35, ... }]
  },
  "meta": { "candles_used": 200, "fetch_errors": {}, "scoring_version": 2 }
}
```

Konsumen JSON ini **wajib mengecek `data_as_of`** sebelum memakainya, dan
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

**Jadwal Actions tidak presisi.** Dengan cron `0 0 * * *`, keenam run terjadwal
pertama (9–14 Sep 2026) baru jalan 11:04–11:23 WIB — telat sekitar 4 jam, karena
awal jam adalah slot tersibuk penjadwal Actions dan 00:00 UTC yang tersibuk dari
semuanya. Cron sekarang `37 0 * * *`; cek jam run beberapa hari ke depan di tab
Actions sebelum mengandalkannya. Prompt Cowork tetap memverifikasi `data_as_of`
sendiri.

**Timezone.** Cron Actions selalu UTC. `37 0 * * *` = 07:37 WIB.

**Workflow terjadwal dinonaktifkan setelah 60 hari repo tidak aktif.** Commit
harian dari pipeline ini umumnya cukup untuk menjaganya tetap hidup, tapi kalau
suatu saat cron berhenti diam-diam, ini hal pertama yang perlu dicek — GitHub
mengirim email pemberitahuan saat menonaktifkannya.

**Ambang sinyal sengaja tidak disetel dari hasil backtest.** Mencari ambang terbaik
di data yang sama lalu memakai data yang sama sebagai bukti adalah overfitting:
hasilnya tampak bagus di laporan dan gagal di data baru. Perubahan ambang perlu
diuji di periode yang tidak dipakai untuk menemukannya.

## Backtest: seberapa bagus analisisnya?

`backtest.py` menghitung skor setiap hari selama 730 hari (14 Sep 2024 – 13 Sep 2026)
dengan v1 dan v2, dari jendela 200 candle yang persis sama dengan yang dilihat pipeline
harian, lalu mengukurnya terhadap pergerakan harga sesudahnya. Laporan lengkap:
[output/backtest/REPORT.md](output/backtest/REPORT.md).

**Jawaban jujurnya: skor ini belum terbukti memprediksi harga.**

| | v1 | v2 |
|---|---:|---:|
| IC 7 hari (korelasi peringkat skor vs return 7 hari) | +0,019 (t 0,2) | +0,050 (t 0,5) |
| Long saat bullish — total / max drawdown | +27,2% / −14,1% | +7,0% / −12,1% |
| Long saat skor > 0 — total / max drawdown | +25,7% / −26,5% | +28,5% / −29,8% |

Buy & hold di periode yang sama: +28,0%, max drawdown −53,0%.

- IC kedua versi jauh dari signifikan. Selisih v2 − v1 (+0,032) masih dalam batas noise,
  jadi indikator tambahan **belum terbukti** memperbaiki skor — tapi juga tidak
  memperburuk daya prediksinya.
- Urutan label tidak monoton: return 7 hari rata-rata setelah label *bullish* (v2)
  justru −0,3%, di bawah rata-rata semua hari (+0,4%).
- v1 *long saat bullish* menyamai buy & hold dengan hanya 26% waktu di pasar, sementara
  v2 dengan aturan serupa hanya +7%. Dengan IC yang sama-sama tidak signifikan, selisih
  sebesar ini lebih mungkin berasal dari beberapa pergerakan besar yang kebetulan
  tertangkap atau terlewat daripada dari perbedaan kualitas skor.

Temuan per aturan:

- **ADX didukung data.** Arah tren tepat 52% saat ADX ≥ 20 (498 hari), hanya 45% saat
  ADX < 20 (225 hari) — peredaman skor tren di v2 punya dasar.
- **ATR tidak memprediksi besar pergerakan.** Pergerakan 7 hari sesudah volatilitas
  sangat rendah 1,04× rata-rata, sesudah volatilitas ekstrem 0,96×. Karena ATR hanya
  sinyal info, ini tidak memengaruhi skor.
- **Pembacaan contrarian lemah di periode ini.** Kategori sentimen (Fear & Greed dibaca
  terbalik) dan momentum punya IC 7 hari negatif. RSI overbought diikuti rata-rata
  +2,3%, bukan penurunan.
- **Sinyal cross paling tajam, tapi jarang.** Death cross diikuti penurunan 62% dari 32
  kejadian (dasar 47%); golden cross diikuti kenaikan 61% dari 36 kejadian (dasar 53%).

Artinya untuk pemakaian: dashboard dan email ini berguna sebagai **ringkasan kondisi
pasar yang konsisten dan bisa diaudit**, bukan sebagai sinyal beli/jual. Temuan seperti
"pembacaan contrarian lemah" adalah hipotesis untuk diuji di periode lain (misalnya
2021–2024), bukan alasan untuk langsung membalik aturannya.

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
