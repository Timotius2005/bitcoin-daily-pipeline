# Bitcoin Daily Analysis Pipeline

## Overview

Pipeline otomatis yang mengambil data pasar Bitcoin, menganalisisnya secara teknikal dan sentimen, lalu mengirimkan ringkasan harian ke email. Dua mekanisme cron berjalan terpisah dan saling menyambung:

```
[Cron 1: GitHub Actions]
Fetch Data → Analisis (Rule-based Signal + Scoring) → Commit hasil (JSON) ke repo
                                                              ↓
                                          raw.githubusercontent.com/.../latest.json
                                                              ↓
[Cron 2: Claude Cowork scheduled task, jalan beberapa menit setelahnya]
Ambil JSON hasil analisis → Susun narasi Bahasa Indonesia → Kirim email via Gmail connector
```

Kenapa dua cron terpisah: fetch + analisis teknikal butuh library Python (`pandas-ta`, dll) yang cocok dijalankan di GitHub Actions. Narasi dan pengiriman email dijalankan lewat Claude Cowork scheduled task supaya bisa memanfaatkan Claude Pro subscription langsung (tanpa perlu Claude API key terpisah yang berbayar per pemakaian), dan Gmail connector-nya sudah built-in untuk kirim email.

## Tujuan

- Dapat snapshot kondisi BTC harian (harga, sinyal teknikal, sentimen pasar) tanpa perlu cek manual.
- Analisis konsisten dan bisa diaudit — hasil skor/sinyal deterministik, bukan tebakan LLM yang bisa beda tiap run.
- Portfolio project yang menunjukkan kemampuan automation pipeline end-to-end.

## Requirements

### Sumber Data (gratis, tanpa API key untuk versi awal)

| Sumber | Data | Endpoint |
|---|---|---|
| Binance Public API | OHLCV candle BTC | `api.binance.com/api/v3/klines` |
| CoinGecko API | Market snapshot, BTC dominance | `api.coingecko.com/api/v3/` |
| Alternative.me | Fear & Greed Index | `api.alternative.me/fng/` |

### Layanan & Akun

- **GitHub repo** dengan Actions enabled — untuk scheduler fetch + analisis.
- **Claude Pro (atau Max)** — untuk akses Claude Cowork scheduled tasks.
- **Gmail connector** di Claude (Customize → Connectors) — untuk generate narasi lewat scheduled task dan kirim email.

### Tech Stack (bagian GitHub Actions)

- Python 3.11+
- `requests` — HTTP calls ke semua API
- `pandas` + `pandas-ta` — hitung indikator teknikal

### Secrets di GitHub Actions

- Tidak perlu API key eksternal untuk versi ini — `GITHUB_TOKEN` bawaan Actions sudah cukup untuk commit hasil JSON kembali ke repo.

### Setup di sisi Claude

- Hubungkan **Gmail connector** lewat Customize → Connectors.
- Tentukan mode approval pengiriman email: mulai dengan **"minta approval tiap kali"** dulu supaya bisa divalidasi hasil narasinya, baru pertimbangkan ubah ke auto-send kalau sudah percaya dengan konsistensi outputnya.

## Fase Pengembangan

### Fase 0 — Setup & Validasi Dasar
- Buat repo, virtual environment, `requirements.txt`.
- Test konektivitas tiap API data (Binance, CoinGecko, Alternative.me) secara manual/terpisah.
- Hubungkan Gmail connector di Claude, pastikan sudah authorized.
- Test kirim satu email manual dari chat biasa (bukan scheduled task dulu) untuk pastikan connector berfungsi.

**Selesai kalau:** semua API data bisa diakses, dan Claude berhasil kirim 1 email test lewat Gmail connector.

### Fase 1 — Data Fetching Layer
- `fetch.py`: ambil OHLCV BTC dari Binance (candle harian, ~100 hari untuk cukup data indikator).
- Ambil Fear & Greed Index.
- Ambil BTC dominance dari CoinGecko `/global`.
- Output distandarkan jadi satu struktur data (dict/JSON), belum ada analisis di tahap ini.

**Selesai kalau:** menjalankan `fetch.py` menghasilkan JSON berisi semua data mentah yang dibutuhkan.

### Fase 2 — Analysis Layer (Rule-based)
- `analyze.py`: hitung indikator via `pandas-ta` — RSI, MACD, MA20/MA50, Bollinger Bands.
- Fungsi deteksi sinyal: oversold/overbought, golden/death cross, volume spike, extreme fear/greed.
- Fungsi composite scoring — gabungkan semua sinyal jadi satu skor bias (misal -100 s/d +100).
- Uji fungsi scoring dengan beberapa skenario data dummy untuk memastikan logikanya masuk akal.
- Simpan hasil akhir (data mentah ringkas + sinyal + skor) sebagai `output/latest.json`.

**Selesai kalau:** dari JSON hasil Fase 1, `analyze.py` menghasilkan `output/latest.json` berisi sinyal-sinyal terdeteksi + skor komposit, siap dibaca pihak luar.

### Fase 3 — Publish Hasil ke Repo
- Tambahkan step di workflow GitHub Actions untuk commit `output/latest.json` kembali ke repo setiap kali cron jalan (`git commit` + `git push` pakai `GITHUB_TOKEN`).
- Pastikan file bisa diakses publik via `https://raw.githubusercontent.com/<user>/<repo>/main/output/latest.json`.
- Kalau repo privat, siapkan cara akses alternatif (misal via GitHub API dengan token) karena Cowork perlu bisa fetch file ini.

**Selesai kalau:** setelah cron jalan, file JSON ter-update di repo dan bisa diakses via URL tersebut.

### Fase 4 — Narasi & Pengiriman via Claude Cowork
- Buat **scheduled task** baru di Claude Cowork (`Scheduled` di sidebar → New task).
- Tulis instruksi task-nya, kurang lebih: ambil data dari URL JSON hasil analisis, susun narasi ringkas Bahasa Indonesia berdasarkan sinyal dan skor komposit yang ada (jangan menyimpulkan ulang dari harga mentah, cukup narasikan sinyal yang sudah terdeteksi), lalu kirim sebagai email ke alamat tujuan lewat Gmail connector.
- Jadwalkan task ini beberapa menit **setelah** cron GitHub Actions selesai (misal GitHub Actions jalan 07:00 WIB, scheduled task Cowork jalan 07:10 WIB) supaya data sudah pasti ter-update duluan.
- Jalankan beberapa kali dengan mode approval manual dulu, review hasil narasi dan formatnya, baru putuskan apakah mau lanjut ke auto-send.

**Selesai kalau:** scheduled task berhasil jalan otomatis, ambil data terbaru, dan email masuk dengan narasi yang masuk akal.

### Fase 5 — Hardening & Iterasi
- Tambahkan logging di `fetch.py`/`analyze.py` (misal print status tiap step) supaya gampang debug lewat GitHub Actions log kalau ada yang gagal.
- Tambahkan instruksi fallback di prompt scheduled task Cowork: kalau JSON gagal diambil atau datanya kosong/stale, kirim email singkat yang bilang "gagal ambil data hari ini" alih-alih diam saja.
- Pantau beberapa hari jalan otomatis, sesuaikan threshold sinyal (RSI, dst.) berdasarkan hasil nyata.
- Setelah yakin stabil, pertimbangkan ubah approval mode Gmail ke auto-send supaya benar-benar tanpa intervensi manual.

**Selesai kalau:** pipeline dua-cron ini jalan stabil otomatis selama minimal satu minggu tanpa perlu intervensi manual, termasuk saat ada kegagalan di salah satu tahap.

### Fase 6 — Pengembangan Lanjutan (Opsional)
- Tambah koin lain (ETH, dst.) dengan struktur data yang sama.
- Tambah data derivatif (funding rate dari Binance Futures).
- Simpan arsip JSON harian (histori) di folder terpisah di repo untuk analisis tren jangka panjang.
- Backtesting sederhana untuk mengevaluasi akurasi sinyal yang sudah didesain.

## Dashboard (Repo Terpisah)

### Konsep

Repo terpisah dari pipeline analisis, fungsinya sebagai lapisan visualisasi. Konsepnya mirip skema di Fase 4 — ada proses cron sendiri yang tugasnya cuma mengambil `output/latest.json` dari repo analisis, lalu menyimpannya sebagai histori lokal supaya dashboard bisa menampilkan tren dari waktu ke waktu, bukan cuma snapshot terakhir.

```
[Cron 3: lokal, misal via crontab di home lab]
Fetch output/latest.json dari repo analisis (raw GitHub URL)
        ↓
Simpan sebagai entry baru di histori lokal (SQLite/JSONL)
        ↓
Dashboard baca histori lokal → render chart & tabel
```

Karena aksesnya lokal dulu, dashboard belum perlu backend server terpisah — pakai Streamlit supaya fetch, simpan, dan render bisa dalam satu repo Python sederhana, tinggal `streamlit run app.py`.

### Requirements

- Repo terpisah, misal `bitcoin-dashboard`.
- Python 3.11+, `requests`, `streamlit`, `pandas`.
- Storage lokal ringan — SQLite (`sqlite3`, built-in di Python) atau append ke file `data/history.jsonl`. SQLite lebih enak kalau nanti mau query/filter tanggal.
- Akses ke cron lokal (crontab di home lab, atau Task Scheduler kalau di mesin lain).

### Struktur Repo

```
bitcoin-dashboard/
├── fetch_history.py     # ambil JSON dari repo analisis, simpan ke histori lokal
├── app.py                # Streamlit dashboard, baca histori, render chart
├── data/
│   └── history.db         # SQLite, di-gitignore (data lokal, bukan buat di-commit)
└── requirements.txt
```

### Fase Pengembangan

**Fase D0 — Setup Repo & Cron Lokal**
- Buat repo baru, venv, `requirements.txt`.
- Test fetch manual dari raw URL repo analisis, pastikan bisa diakses dari luar (kalau repo analisis privat, siapkan token akses).
- Setup crontab lokal yang menjalankan `fetch_history.py` (jadwalnya bisa disamakan dengan cron GitHub Actions di pipeline analisis, atau lebih sering kalau mau granularity lebih halus).

**Selesai kalau:** crontab lokal berhasil jalan otomatis dan `fetch_history.py` bisa dites manual tanpa error.

**Fase D1 — Data Ingestion & Histori**
- `fetch_history.py`: GET raw JSON URL, parse, simpan sebagai row baru (timestamp + semua field sinyal & skor) ke SQLite.
- Tambahkan pengecekan sederhana: skip insert kalau timestamp datanya sama dengan entry terakhir, biar nggak dobel kalau cron jalan lebih sering dari update datanya.

**Selesai kalau:** setelah beberapa kali cron jalan, ada beberapa row histori dengan tanggal berbeda di database lokal.

**Fase D2 — Dashboard Visualisasi**
- `app.py` pakai Streamlit: baca histori dari SQLite ke pandas DataFrame.
- Tampilkan: snapshot terakhir (harga, RSI, skor komposit, label Fear & Greed), line chart tren harga dan skor komposit dari waktu ke waktu, tabel histori sinyal per hari.
- Jalankan lokal dengan `streamlit run app.py`, akses via browser di `localhost`.

**Selesai kalau:** dashboard bisa dibuka lokal dan menampilkan data historis yang sudah terkumpul dari Fase D1, bukan data statis/dummy.

**Fase D3 — Akses Tanpa Start Manual**

Dua opsi supaya dashboard nggak perlu dijalankan manual tiap mau diakses, tergantung kebutuhan:

- **Tetap lokal, jalan sebagai background service** — pakai `systemd` service (atau Docker dengan `restart: unless-stopped`) di mesin home lab supaya `streamlit run app.py` otomatis start pas boot dan tetap jalan di background. Dashboard bisa diakses dari perangkat manapun di jaringan rumah lewat `http://<ip-mesin>:8501`, tanpa perlu start manual sama sekali. Paling relevan kalau akses cuma dari jaringan rumah.
- **Deploy ke Streamlit Community Cloud, repo tetap privat** — connect repo GitHub yang privat, appnya otomatis ikut jadi **private**: nggak muncul/bisa diakses siapapun kecuali diberi izin eksplisit (ditambahkan sebagai developer di GitHub, atau di-invite sebagai viewer langsung dari Streamlit Cloud). Kamu tetap satu-satunya yang bisa akses (sign in pakai akun GitHub yang sama), tapi bisa dibuka dari mana saja tanpa perlu mesin di rumah nyala. Cocok kalau mau akses dari luar jaringan rumah juga.

Kalau nanti butuh keduanya (privat + host sendiri di rumah, bukan di cloud Streamlit), alternatifnya pasang **Cloudflare Tunnel** atau **Tailscale** ke mesin home lab, jadi nggak perlu expose port ke internet langsung.

**Fase D4 — Pengembangan Lanjutan (Opsional)**
- Narasi yang dikirim Claude Cowork saat ini cuma sampai ke email, belum tersimpan di tempat yang bisa dibaca program. Kalau mau narasi juga tampil di dashboard, perlu tambahan langkah supaya narasi ikut disimpan ke suatu tempat yang bisa diakses `fetch_history.py`, misal repo analisis juga commit `output/narrative-YYYY-MM-DD.txt` selain JSON sinyal.

## Prinsip Desain

- **Layer sinyal & scoring harus deterministik (rule-based).** Ini dihitung murni di Python (Fase 2), bukan oleh LLM — sehingga hasilnya konsisten dan bisa diaudit. Claude Cowork di Fase 4 hanya bertugas menarasikan hasil yang sudah jadi, bukan menentukan kesimpulan analisis.
- **Dua cron saling bergantung urutan waktu.** Selalu beri jeda antara cron fetch+analisis dan cron narasi+email supaya data yang diambil Cowork bukan data basi dari run sebelumnya.
- **Mulai dari mode approval manual di Gmail** sebelum beralih ke auto-send, supaya ada kontrol kualitas dulu terhadap narasi yang dihasilkan.
- **Kredensial GitHub Actions selalu di GitHub Secrets**, tidak pernah hardcode di kode atau commit ke repo.
- **Dashboard cuma konsumen read-only** terhadap repo analisis — nggak pernah nulis balik ke sana. Ini menjaga tiga komponen (pipeline analisis, Cowork narasi/email, dashboard) tetap independen dan gampang di-debug terpisah kalau salah satu bermasalah.
