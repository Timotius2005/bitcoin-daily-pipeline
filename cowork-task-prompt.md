# Instruksi Scheduled Task Claude Cowork (Fase 4)

Isi file ini disalin ke **Claude Cowork → Scheduled → New task**. Disimpan di
repo supaya perubahan prompt ikut ter-versioning bersama perubahan skema JSON.

## Pengaturan task

| Field | Nilai |
|---|---|
| Jadwal | Setiap hari, **08:00 WIB** (1 jam setelah cron GitHub Actions 07:00 WIB) |
| Connector | Gmail (harus sudah authorized di Customize → Connectors) |
| Mode approval | **Minta approval tiap kali** dulu, minimal 1 minggu pertama |

Jeda 1 jam sengaja dibuat longgar, bukan 10 menit: penjadwal GitHub Actions
sering telat 5–30 menit saat platform padat. Tapi jeda saja tidak cukup —
prompt di bawah tetap memverifikasi umur data sendiri, jadi task ini tidak
pernah bergantung pada asumsi jam.

---

## Prompt (salin mulai dari sini)

```
Kamu bertugas mengirim ringkasan harian kondisi Bitcoin lewat email.

LANGKAH 1 — Ambil data
Fetch URL berikut:
https://raw.githubusercontent.com/GANTI_USERNAME/GANTI_REPO/main/output/latest.json

LANGKAH 2 — Verifikasi kesegaran data
Sebelum menulis apapun, cek field "data_as_of" (tanggal candle terakhir) dan
"generated_at" (waktu UTC hasil analisis dibuat).

- Kalau fetch GAGAL (error jaringan, 404, JSON tidak valid), atau
- kalau "generated_at" lebih tua dari 24 jam dari waktu sekarang,

maka JANGAN menarasikan datanya. Kirim email singkat dengan subjek
"[BTC Harian] Data tidak tersedia — <tanggal hari ini>" yang menyebutkan apa
yang gagal (tidak bisa fetch / data basi per tanggal berapa), lalu berhenti.
Lebih baik mengirim pemberitahuan gagal daripada diam, dan jauh lebih baik
daripada menarasikan data kemarin seolah-olah data hari ini.

LANGKAH 3 — Susun narasi
Kalau datanya segar, tulis ringkasan dalam Bahasa Indonesia berdasarkan
SINYAL DAN SKOR YANG SUDAH ADA di JSON. Aturan penting:

- Jangan menghitung ulang atau menyimpulkan sendiri dari angka harga mentah.
  Analisisnya sudah selesai dilakukan di pipeline; tugasmu menarasikannya.
- Jangan menambah sinyal yang tidak ada di array "signals".
- Jangan memberi rekomendasi beli/jual. Deskripsikan kondisi, bukan saran.

Struktur email:

Subjek: [BTC Harian] <label skor> — <skor komposit> | $<harga close>
Contoh: "[BTC Harian] netral — -4 | $79,112"

Isi:
1. Satu paragraf pembuka: harga close, perubahan 1 hari dan 7 hari, lalu skor
   komposit beserta labelnya.
2. Bagian "Sinyal terdeteksi": daftar isi array "signals", pakai field "label"
   dan "detail" apa adanya, dikelompokkan per kategori.
3. Bagian "Skor per kategori": tampilkan isi "scores.by_category". Kalau ada
   dua kategori yang arahnya berlawanan (satu positif, satu negatif),
   sebutkan itu secara eksplisit sebagai sinyal yang saling bertentangan —
   ini informasi penting, bukan sesuatu yang perlu dirapikan jadi satu
   kesimpulan tunggal.
4. Penutup satu baris: "Fear & Greed Index: <nilai> (<klasifikasi>), BTC
   dominance <nilai>%."
5. Baris terakhir: "Analisis rule-based otomatis, bukan saran keuangan.
   Data per <data_as_of>."

Kalau "meta.fetch_errors" tidak kosong, tambahkan satu baris di akhir yang
menyebutkan sumber data mana yang gagal diambil saat analisis dibuat.

LANGKAH 4 — Kirim
Kirim email tersebut lewat Gmail connector ke: GANTI_EMAIL_TUJUAN
```

---

## Sebelum dipakai, ganti tiga placeholder ini

1. `GANTI_USERNAME` / `GANTI_REPO` — username GitHub dan nama repo kamu.
2. `GANTI_EMAIL_TUJUAN` — alamat email penerima.

## Cara menguji tanpa menunggu jadwal

1. Jalankan prompt di atas di **chat Cowork biasa** dulu, bukan sebagai
   scheduled task. Lebih cepat iterasinya kalau ada yang perlu diperbaiki.
2. Uji jalur gagalnya juga: ganti sementara URL-nya jadi URL yang tidak ada,
   pastikan yang terkirim adalah email "Data tidak tersedia", bukan narasi
   karangan.
3. Baru setelah keduanya benar, buat scheduled task-nya.

## Kapan boleh pindah ke auto-send

Setelah minimal 7 hari berturut-turut narasinya akurat terhadap isi JSON —
khususnya tidak pernah menambahkan sinyal yang tidak ada di data. Kalau satu
kali saja ada yang mengarang, reset hitungannya dari nol.
