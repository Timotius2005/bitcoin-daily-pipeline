"""Isi arsip histori ke belakang, sekali jalan.

Pipeline harian hanya menghasilkan satu file arsip per hari ke depan. Skrip
ini menghitung ulang analisis untuk N hari terakhir dari candle yang sudah
ada, supaya konsumen (dashboard) langsung punya deret waktu untuk
ditampilkan tanpa harus menunggu berminggu-minggu.

Hasilnya identik skemanya dengan output/latest.json dan ditulis ke
output/history/<tanggal>.json - file yang sama yang akan ditimpa pipeline
harian kalau tanggalnya bertemu.

Catatan kejujuran data: Fear & Greed punya endpoint histori harian, jadi
sentimen historis di sini asli. Dominansi BTC dari CoinGecko TIDAK punya
histori gratis, jadi field itu null untuk baris hasil backfill - berbeda
dengan baris harian yang mengisinya. Jangan bandingkan keduanya seolah
setara.

Jalankan: python backfill.py [--days 90]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

import analyze as az
import fetch as ft

log = logging.getLogger("backfill")

FNG_HISTORY_LIMIT = 400  # cukup untuk >1 tahun


def fetch_fng_history(limit: int = FNG_HISTORY_LIMIT) -> dict[str, dict]:
    """Fear & Greed harian, dipetakan per tanggal."""
    log.info("Fetch histori Fear & Greed (%d hari)...", limit)
    raw = ft._get(ft.FNG_URL, {"limit": limit})
    out: dict[str, dict] = {}
    for entry in raw["data"]:
        tanggal = datetime.fromtimestamp(
            int(entry["timestamp"]), timezone.utc
        ).strftime("%Y-%m-%d")
        out[tanggal] = {
            "value": int(entry["value"]),
            "classification": entry["value_classification"],
            "date": tanggal,
            "value_yesterday": None,
        }
    log.info("  %d hari tersedia (%s s/d %s)", len(out), min(out), max(out))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Isi arsip histori ke belakang.")
    parser.add_argument("--days", type=int, default=90, help="jumlah hari yang dihitung")
    parser.add_argument("--out-dir", default="output/history")
    parser.add_argument(
        "--overwrite", action="store_true",
        help="tulis ulang file yang sudah ada (default: dilewati)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    # Butuh candle sebanyak hari yang diminta + warmup indikator terpanjang.
    perlu = args.days + az.MIN_CANDLES + 10
    try:
        ohlcv = ft.fetch_ohlcv(days=perlu)
        fng = fetch_fng_history()
    except Exception as exc:  # noqa: BLE001
        log.error("FATAL: %s", exc)
        return 1

    if len(ohlcv) < perlu - 5:
        log.warning("Hanya dapat %d candle dari %d yang diminta", len(ohlcv), perlu)

    os.makedirs(args.out_dir, exist_ok=True)
    ditulis = dilewati = gagal = 0
    tanpa_sentimen = 0

    for i in range(len(ohlcv) - args.days, len(ohlcv)):
        potongan = ohlcv[: i + 1]
        tanggal = potongan[-1]["date"]
        path = os.path.join(args.out_dir, f"{tanggal}.json")

        if os.path.exists(path) and not args.overwrite:
            dilewati += 1
            continue

        sentimen = fng.get(tanggal)
        if sentimen is None:
            tanpa_sentimen += 1

        raw = {
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "symbol": ft.SYMBOL,
            "ohlcv": potongan,
            "fear_greed": sentimen,
            # Dominansi BTC historis tidak tersedia gratis - sengaja dikosongkan
            # daripada diisi nilai hari ini yang akan salah untuk tanggal lampau.
            "global_market": None,
            "fetch_errors": {},
        }

        try:
            hasil = az.analyze(raw)
        except Exception as exc:  # noqa: BLE001
            log.warning("  %s gagal: %s", tanggal, exc)
            gagal += 1
            continue

        hasil["meta"]["backfilled"] = True
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(hasil, fh, indent=2, ensure_ascii=False)
        ditulis += 1

    log.info("Selesai: %d ditulis, %d dilewati, %d gagal", ditulis, dilewati, gagal)
    if tanpa_sentimen:
        log.warning("%d hari tanpa data Fear & Greed - bobot sentimen "
                    "dinormalisasi ulang untuk baris itu", tanpa_sentimen)
    log.info("Arsip ada di %s/", args.out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
