# Instruksi Scheduled Task Claude Cowork (Fase 4)

Isi file ini disalin ke **Claude Cowork → Scheduled → New task**. Disimpan di
repo supaya perubahan prompt ikut ter-versioning bersama perubahan skema JSON.

## Pengaturan task

| Field | Nilai |
|---|---|
| Jadwal | Setiap hari, **09:00 WIB** |
| Connector | Gmail (harus sudah authorized di Customize → Connectors) |
| Mode approval | **Minta approval tiap kali** dulu, minimal 1 minggu pertama |

Cron pipeline sekarang berjalan 07:37 WIB. Dengan jadwal lama (07:00 WIB)
run terjadwal konsisten telat sekitar 4 jam, jadi sebelum memakai jam 09:00,
cek dulu di tab **Actions** selama beberapa hari bahwa run *Daily BTC
Analysis* sudah selesai sebelum 09:00 WIB. Kalau belum, mundurkan jadwal
task ini.

Jeda saja tidak cukup. Prompt di bawah memverifikasi tanggal datanya sendiri,
jadi task ini tidak pernah bergantung pada asumsi jam.

---

## Prompt (salin mulai dari sini)

```
Kamu bertugas mengirim ringkasan harian kondisi Bitcoin lewat email.

LANGKAH 1 — Ambil data
Fetch URL berikut:
https://raw.githubusercontent.com/Timotius2005/bitcoin-daily-pipeline/main/output/latest.json

LANGKAH 2 — Verifikasi kesegaran data
Candle harian Bitcoin ditutup pukul 07:00 WIB. Jadi pada hari X, data yang
segar adalah data dengan "data_as_of" = tanggal X dikurangi satu hari (WIB).

- Kalau fetch GAGAL (error jaringan, 404, JSON tidak valid), atau
- kalau "data_as_of" LEBIH LAMA dari tanggal kemarin (WIB),

maka JANGAN menarasikan datanya. Kirim email singkat dengan subjek
"[BTC Harian] Data tidak tersedia — <tanggal hari ini>" yang menyebutkan apa
yang gagal (tidak bisa fetch / data terakhir per tanggal berapa), lalu
berhenti. Lebih baik mengirim pemberitahuan gagal daripada diam, dan jauh
lebih baik daripada menarasikan data dua hari lalu seolah-olah data kemarin.

Jangan memakai "generated_at lebih tua dari 24 jam" sebagai ukuran: kalau
pipeline hari ini telat, data kemarin masih berumur di bawah 24 jam dan akan
lolos cek itu.

LANGKAH 3 — Susun narasi
Kalau datanya segar, tulis ringkasan dalam Bahasa Indonesia berdasarkan
SINYAL DAN SKOR YANG SUDAH ADA di JSON. Aturan penting:

- Jangan menghitung ulang atau menyimpulkan sendiri dari angka harga mentah.
  Analisisnya sudah selesai dilakukan di pipeline; tugasmu menarasikannya.
- Jangan menambah sinyal yang tidak ada di array "signals".
- Sinyal dengan "kind": "info" adalah konteks tanpa arah (misalnya "Tren
  kuat" atau "Volatilitas sangat rendah"). Jangan sebut sebagai sinyal
  bullish atau bearish, dan jangan anggap ia menambah atau mengurangi skor.
- Jangan memberi rekomendasi beli/jual. Deskripsikan kondisi, bukan saran.

Struktur email:

Subjek: [BTC Harian] <label skor> — <skor komposit> | $<harga close>
Contoh: "[BTC Harian] netral — +11 | $76,842"

Isi:
1. Satu paragraf pembuka: harga close, perubahan 1 hari dan 7 hari, lalu skor
   komposit beserta labelnya.
2. Bagian "Sinyal terdeteksi": sinyal dengan "kind": "score", pakai field
   "label" dan "detail" apa adanya, dikelompokkan per kategori. Sinyal dengan
   "kind": "info" ditulis terpisah di bawahnya dengan judul "Konteks".
3. Bagian "Skor per kategori": tampilkan isi "scores.by_category".
   - Kalau "scores.adjustments" tidak kosong, sebutkan untuk setiap
     penyesuaian bahwa skor kategori itu diubah dari "before" ke "after",
     beserta "reason"-nya.
   - Kalau ada dua kategori yang arahnya berlawanan (satu positif, satu
     negatif), sebutkan itu secara eksplisit sebagai sinyal yang saling
     bertentangan — ini informasi penting, bukan sesuatu yang perlu dirapikan
     jadi satu kesimpulan tunggal.
4. Penutup satu baris: "Fear & Greed Index: <nilai> (<klasifikasi>), BTC
   dominance <nilai>%." Kalau dominansi bernilai null, lewati bagian dominance.
5. Baris terakhir: "Analisis rule-based otomatis, bukan saran keuangan. Skor
   ini bersifat deskriptif — backtest 2 tahun belum menunjukkan daya prediksi.
   Data per <data_as_of>."

Kalau "meta.fetch_errors" tidak kosong, tambahkan satu baris di akhir yang
menyebutkan sumber data mana yang gagal diambil saat analisis dibuat.

LANGKAH 4 — Kirim
Kirim email tersebut lewat Gmail connector ke: GANTI_EMAIL_TUJUAN
```

---

## Sebelum dipakai

Ganti `GANTI_EMAIL_TUJUAN` dengan alamat email penerima.

## Cara menguji tanpa menunggu jadwal

1. Jalankan prompt di atas di **chat Cowork biasa** dulu, bukan sebagai
   scheduled task. Lebih cepat iterasinya kalau ada yang perlu diperbaiki.
2. Uji jalur gagalnya juga: ganti sementara URL-nya jadi URL yang tidak ada,
   pastikan yang terkirim adalah email "Data tidak tersedia", bukan narasi
   karangan.
3. Baru setelah keduanya benar, buat scheduled task-nya.

## Kapan boleh pindah ke auto-send

Setelah minimal 7 hari berturut-turut narasinya akurat terhadap isi JSON —
khususnya tidak pernah menambahkan sinyal yang tidak ada di data, dan tidak
pernah menyebut sinyal info sebagai bullish atau bearish. Kalau satu kali saja
ada yang mengarang, reset hitungannya dari nol.
