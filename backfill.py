"""Isi atau hitung ulang arsip histori ke belakang.

Pipeline harian hanya menghasilkan satu file arsip per hari ke depan. Skrip
ini menghitung analisis untuk N hari terakhir dari candle yang sudah ada,
supaya konsumen (dashboard) langsung punya deret waktu, dan menghitung ulang
arsip lama saat aturan scoring berubah supaya deretnya tetap sebanding.

Setiap hari dihitung dari jendela geser 200 candle yang berakhir di hari itu
- persis sebanyak yang dilihat pipeline harian. Indikator dengan smoothing
rekursif (RSI, MACD, ATR, ADX) bergantung pada titik awal datanya, jadi
jendela yang terus membesar akan memberi angka yang sedikit berbeda dari
yang benar-benar dipublikasikan pipeline.

Catatan kejujuran data: Fear & Greed punya endpoint histori harian, jadi
sentimen historis di sini asli. Dominansi BTC dari CoinGecko TIDAK punya
histori gratis, jadi field itu null untuk baris hasil backfill. Dengan
--rescore, input yang tidak bisa direkonstruksi (Fear & Greed yang benar-benar
dilihat pipeline hari itu, dominansi, status backfill) diambil dari arsip lama.

Jalankan:
  python backfill.py --days 90             # isi hari yang belum punya arsip
  python backfill.py --days 90 --rescore   # hitung ulang semua dengan scoring terbaru
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

import analyze as az
import fetch as ft

log = logging.getLogger("backfill")

FNG_HISTORY_LIMIT = 400  # cukup untuk >1 tahun
WINDOW = ft.DAYS  # sama persis dengan jumlah candle yang dilihat pipeline harian
BINANCE_MAX_LIMIT = 1000


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


def load_previous_inputs(path: str) -> dict[str, Any] | None:
    """Ambil input yang tidak bisa direkonstruksi dari arsip yang sudah ada."""
    try:
        with open(path, encoding="utf-8") as fh:
            lama = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None

    sentimen = lama.get("sentiment") or {}
    meta = lama.get("meta") or {}
    dominansi = sentimen.get("btc_dominance_pct")
    return {
        "fear_greed": sentimen.get("fear_greed"),
        "global_market": None if dominansi is None else {
            "btc_dominance_pct": dominansi,
            "market_cap_change_24h_pct": sentimen.get("market_cap_change_24h_pct"),
        },
        "fetched_at": meta.get("fetched_at"),
        "fetch_errors": meta.get("fetch_errors") or {},
        "backfilled": bool(meta.get("backfilled", False)),
        "scoring_version": meta.get("scoring_version"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Isi atau hitung ulang arsip histori.")
    parser.add_argument("--days", type=int, default=90, help="jumlah hari yang dihitung")
    parser.add_argument("--out-dir", default="output/history")
    parser.add_argument(
        "--overwrite", action="store_true",
        help="tulis ulang file yang sudah ada dari nol (input lama tidak dipertahankan)",
    )
    parser.add_argument(
        "--rescore", action="store_true",
        help="hitung ulang file yang sudah ada dengan scoring terbaru, "
             "mempertahankan input yang tidak bisa direkonstruksi",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    perlu = args.days + WINDOW - 1
    if perlu + 1 > BINANCE_MAX_LIMIT:
        log.error("--days maksimal %d (batas %d candle per permintaan Binance)",
                  BINANCE_MAX_LIMIT - WINDOW, BINANCE_MAX_LIMIT)
        return 1

    try:
        ohlcv = ft.fetch_ohlcv(days=perlu)
        fng = fetch_fng_history(limit=max(FNG_HISTORY_LIMIT, args.days + 30))
    except Exception as exc:  # noqa: BLE001
        log.error("FATAL: %s", exc)
        return 1

    n = len(ohlcv)
    mulai = max(WINDOW - 1, n - args.days)
    if n - args.days < WINDOW - 1:
        log.warning("Hanya %d hari yang punya jendela %d candle penuh", n - mulai, WINDOW)

    os.makedirs(args.out_dir, exist_ok=True)
    ditulis = dihitung_ulang = dilewati = gagal = 0
    sekarang = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for i in range(mulai, n):
        jendela = ohlcv[i - WINDOW + 1 : i + 1]
        tanggal = jendela[-1]["date"]
        path = os.path.join(args.out_dir, f"{tanggal}.json")
        ada = os.path.exists(path)

        if ada and not (args.overwrite or args.rescore):
            dilewati += 1
            continue

        lama = load_previous_inputs(path) if (ada and args.rescore) else None
        if lama is not None:
            # Pakai Fear & Greed yang benar-benar dilihat pipeline hari itu,
            # bukan nilai histori yang bisa berbeda dari angka intraday-nya.
            fear_greed = lama["fear_greed"] if lama["fear_greed"] is not None else fng.get(tanggal)
            global_market = lama["global_market"]
            backfilled = lama["backfilled"]
            fetched_at = lama["fetched_at"] or sekarang
            fetch_errors = lama["fetch_errors"]
        else:
            fear_greed = fng.get(tanggal)
            # Dominansi BTC historis tidak tersedia gratis - sengaja dikosongkan
            # daripada diisi nilai hari ini yang akan salah untuk tanggal lampau.
            global_market = None
            backfilled = True
            fetched_at = sekarang
            fetch_errors = {}

        raw = {
            "fetched_at": fetched_at,
            "symbol": ft.SYMBOL,
            "ohlcv": jendela,
            "fear_greed": fear_greed,
            "global_market": global_market,
            "fetch_errors": fetch_errors,
        }

        try:
            hasil = az.analyze(raw)
        except Exception as exc:  # noqa: BLE001
            log.warning("  %s gagal: %s", tanggal, exc)
            gagal += 1
            continue

        hasil["meta"]["backfilled"] = backfilled
        if lama is not None:
            hasil["meta"]["rescored_from_version"] = lama["scoring_version"]
            dihitung_ulang += 1

        with open(path, "w", encoding="utf-8") as fh:
            json.dump(hasil, fh, indent=2, ensure_ascii=False)
        ditulis += 1

    log.info("Selesai: %d ditulis (%d dihitung ulang), %d dilewati, %d gagal",
             ditulis, dihitung_ulang, dilewati, gagal)
    log.info("Scoring v%d, jendela %d candle, arsip di %s/",
             az.SCORING_VERSION, WINDOW, args.out_dir)
    return 0 if gagal == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
