# Backtest skor komposit

**Periode:** 2024-09-14 s/d 2026-09-13 · 730 hari skor · BTCUSDT harian (Binance) + Fear & Greed (alternative.me)  
**Dijalankan:** 2026-09-14 · biaya 0,1% per transaksi · jendela 200 candle per hari

> Ini evaluasi, bukan optimasi: semua ambang adalah nilai buku teks yang ditetapkan sebelum backtest dijalankan. Hasil masa lalu tidak menjamin hasil masa depan, dan laporan ini bukan saran keuangan.

## Ringkasan

- **Daya prediksi skor (7 hari ke depan):** IC v2 +0,050, v1 +0,019. Untuk v2 ini tidak signifikan secara statistik, dengan 103 sampel efektif.
- **Indikator tambahan:** selisih IC 7 hari v2 − v1 = +0,032. Selisih sekecil ini sebaiknya dianggap noise, bukan perbaikan.
- **Per label (v2, 7 hari):** return rata-rata tertinggi setelah *netral* (+0,7%, 550 hari), terendah setelah *bearish* (−1,1%, 41 hari). Semua hari: +0,4%. Urutan rata-rata return per label **tidak searah** dengan urutan skor.
- **Kategori paling informatif (7 hari):** Volatilitas & volume, IC +0,048 — tidak signifikan secara statistik.
- **Arah pembacaan yang tidak didukung data (IC 7 hari negatif):** Momentum, Sentimen. Belum signifikan, tapi layak diuji di periode lain sebelum aturannya diubah.
- **Validasi ADX:** arah tren tepat 52% saat ADX ≥ 20 (498 hari) vs 45% saat ADX < 20 (225 hari) — mendukung peredaman skor tren saat ADX lemah.
- **Validasi ATR:** besar pergerakan 7 hari ke depan 1,04× rata-rata setelah volatilitas sangat rendah (163 hari); 0,96× rata-rata setelah volatilitas ekstrem (100 hari).
- **Strategi long saat bullish (v2):** total +7,0%, max drawdown −12,1%, di pasar 18% waktu. Buy & hold: total +28,0%, max drawdown −53,0%.
- **Strategi yang sama, v1 vs v2:** v1 total +27,2% dari 108 transaksi, v2 +7,0% dari 76 transaksi. Dengan IC kedua versi yang tidak signifikan, selisih sebesar ini lebih mungkin berasal dari beberapa pergerakan besar yang kebetulan tertangkap atau terlewat daripada dari perbedaan kualitas skor.

## 1. Apakah skor memprediksi return?

*Information coefficient* (IC) adalah korelasi peringkat Spearman antara skor hari D dan return dari close D ke close D+h. Nol berarti tidak ada hubungan; positif berarti skor tinggi cenderung diikuti return lebih tinggi. Uji t memakai sampel efektif n/h, karena return h-hari yang tumpang tindih saling berkorelasi.

| Versi | 1 hari | 7 hari | 30 hari |
|---|---:|---:|---:|
| v1 | +0,032 (t 0,9, n efektif 729) | +0,019 (t 0,2, n efektif 103) | +0,099 (t 0,5, n efektif 23) |
| v2 | +0,024 (t 0,7, n efektif 729) | +0,050 (t 0,5, n efektif 103) | +0,130 (t 0,6, n efektif 23) |

## 2. Return 7 hari setelah tiap label

| Label | v1 hari | v1 rata-rata | v1 naik | v2 hari | v2 rata-rata | v2 naik |
|---|---:|---:|---:|---:|---:|---:|
| bearish kuat | 4 | +6,5% | 100% | 0 | — | — |
| bearish | 50 | −0,9% | 52% | 41 | −1,1% | 46% |
| netral | 476 | +0,5% | 53% | 550 | +0,7% | 55% |
| bullish | 171 | +0,5% | 51% | 127 | −0,3% | 45% |
| bullish kuat | 22 | +0,2% | 50% | 5 | −0,2% | 40% |
| **semua hari** | 723 | +0,4% | 53% | 723 | +0,4% | 53% |

Label dengan kurang dari 20 hari tidak disimpulkan di ringkasan.

## 3. Kategori mana yang membawa informasi (v2)

| Kategori | IC 7 hari | IC 30 hari | Hari aktif |
|---|---:|---:|---:|
| Tren | +0,044 (t 0,4) | +0,058 (t 0,3) | 723 |
| Momentum | −0,044 (t −0,4) | +0,068 (t 0,3) | 723 |
| Sentimen | −0,035 (t −0,4) | −0,024 (t −0,1) | 722 |
| Volatilitas & volume | +0,048 (t 0,5) | +0,125 (t 0,6) | 723 |

## 4. Sinyal individual (v2, 7 hari)

*Arah tepat* = persentase hari sinyal bullish diikuti kenaikan (atau sinyal bearish diikuti penurunan). Bandingkan dengan *dasar*: persentase semua hari yang naik (untuk sinyal bullish) atau turun (untuk sinyal bearish). Sinyal baru berguna kalau arah tepatnya jelas di atas dasarnya.

| Sinyal | Kategori | Muncul | Rata-rata 7 hari | Arah tepat | Dasar |
|---|---|---:|---:|---:|---:|
| Sentimen takut (contrarian bullish) | Sentimen | 419 | +0,0% | 51% | 53% |
| MA20 di atas MA50 | Tren | 408 | +0,6% | 52% | 53% |
| Harga di atas MA50 | Tren | 397 | +0,9% | 54% | 53% |
| Harga di bawah MA50 | Tren | 326 | −0,2% | 48% | 47% |
| MA20 di bawah MA50 | Tren | 315 | +0,2% | 46% | 47% |
| Volume mengonfirmasi kenaikan | Volatilitas & volume | 302 | +0,8% | 56% | 53% |
| Volume mengonfirmasi penurunan | Volatilitas & volume | 297 | +0,3% | 47% | 47% |
| Histogram MACD positif | Momentum | 284 | +0,7% | 56% | 53% |
| Sentimen serakah (contrarian bearish) | Sentimen | 283 | +0,9% | 45% | 47% |
| Histogram MACD negatif | Momentum | 229 | −0,4% | 48% | 47% |
| RSI tinggi | Momentum | 142 | −0,2% | 45% | 47% |
| MACD cross bullish | Momentum | 123 | +0,1% | 47% | 53% |
| RSI lemah | Momentum | 89 | −1,4% | 47% | 53% |
| MACD cross bearish | Momentum | 87 | +1,9% | 47% | 47% |
| Kenaikan tanpa dukungan volume | Volatilitas & volume | 78 | +0,6% | 53% | 47% |
| RSI overbought | Momentum | 55 | +2,3% | 42% | 47% |
| Harga menembus upper Bollinger Band | Volatilitas & volume | 48 | +0,5% | 48% | 47% |
| Akumulasi saat harga lemah | Volatilitas & volume | 46 | −1,9% | 46% | 53% |
| Golden cross MA20/MA50 | Tren | 36 | +1,0% | 61% | 53% |
| Harga menembus lower Bollinger Band | Volatilitas & volume | 34 | +0,6% | 59% | 53% |
| Death cross MA20/MA50 | Tren | 32 | −2,7% | 62% | 47% |
| RSI oversold | Momentum | 26 | +0,3% | 58% | 53% |
| Sentimen netral | Sentimen | 20 | +1,0% | — | — |
| Lonjakan volume pada candle merah | Volatilitas & volume | 19 | −1,4% | 42% | 47% |
| Lonjakan volume pada candle hijau | Volatilitas & volume | 15 | +2,1% | 47% | 53% |

## 5. Validasi aturan baru

**ADX** — v2 meredam skor tren ×0,5 saat ADX < 20. Aturan itu hanya masuk akal kalau arah tren memang lebih sering salah di kondisi tersebut:

| Kondisi | Hari | Arah tren tepat (7 hari) |
|---|---:|---:|
| ADX ≥ 20 | 498 | 52% |
| ADX < 20 | 225 | 45% |

**ATR** — tidak memengaruhi skor, tapi dilaporkan sebagai konteks. Apakah rezim volatilitasnya memang mendahului pergerakan yang berbeda besarnya?

| Kondisi | Hari | Rata-rata \|return 7 hari\| |
|---|---:|---:|
| Semua hari | 723 | 4,4% |
| Volatilitas sangat rendah (persentil ≤ 10) | 163 | 4,6% |
| Volatilitas ekstrem (persentil ≥ 90) | 100 | 4,2% |

**OBV** — lihat baris *Volume mengonfirmasi*, *Kenaikan tanpa dukungan volume*, dan *Akumulasi saat harga lemah* di bagian 4.

## 6. Strategi sederhana

Long atau kas saja, tanpa short dan tanpa leverage. Posisi diputuskan di close hari D dan baru mendapat return hari berikutnya. Ini ilustrasi apakah skor bisa dipakai, bukan strategi yang siap dijalankan.

| Strategi | Total | CAGR | Max drawdown | Sharpe | Di pasar | Transaksi |
|---|---:|---:|---:|---:|---:|---:|
| Buy & hold | +28,0% | +13,1% | −53,0% | 0,50 | 100% | 1 |
| v1 · long saat bullish (skor ≥ 20) | +27,2% | +12,8% | −14,1% | 0,76 | 26% | 108 |
| v1 · long saat skor > 0 | +25,7% | +12,2% | −26,5% | 0,53 | 61% | 137 |
| v2 · long saat bullish (skor ≥ 20) | +7,0% | +3,5% | −12,1% | 0,30 | 18% | 76 |
| v2 · long saat skor > 0 | +28,5% | +13,4% | −29,8% | 0,57 | 58% | 127 |

## Batasan

- Satu aset dan satu periode (2024-09-14 s/d 2026-09-13) dengan rezim pasarnya sendiri. Periode yang didominasi tren naik menguntungkan buy & hold dan aturan tren.
- Eksekusi di harga close tanpa slippage. Biaya dihitung per perubahan posisi.
- Fear & Greed historis memakai nilai tanggal yang sama, sedikit lebih konservatif daripada pipeline live yang melihat nilai terbitan pagi berikutnya.
- Untuk horizon 30 hari, sampel efektifnya hanya sekitar n/30 — sangat sedikit untuk menyimpulkan apa pun dengan yakin.
- Jangan menyetel ambang supaya angka laporan ini membaik lalu memakai laporan yang sama sebagai bukti. Itu overfitting; validasi perubahan ambang butuh periode data lain.

Jalankan ulang: `python backtest.py --days 730`. Data mentah per strategi ada di `equity.csv`, semua angka di `report.json`.
