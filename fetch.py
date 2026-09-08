"""Fase 1 - Data fetching layer.

Mengambil data mentah dari tiga sumber publik (tanpa API key) dan
menormalkannya jadi satu struktur JSON. Tidak ada analisis di sini.

Jalankan: python fetch.py [--out output/raw.json]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from typing import Any

import requests

log = logging.getLogger("fetch")

# data-api.binance.vision = endpoint market data publik Binance. Dipakai
# menggantikan api.binance.com yang diblokir dari Indonesia maupun dari
# runner GitHub Actions (US) dengan HTTP 451.
BINANCE_KLINES = "https://data-api.binance.vision/api/v3/klines"
COINGECKO_GLOBAL = "https://api.coingecko.com/api/v3/global"
FNG_URL = "https://api.alternative.me/fng/"

SYMBOL = "BTCUSDT"
DAYS = 200  # cukup untuk MA50 + warmup RSI/MACD, dengan margin

TIMEOUT = 30
RETRIES = 3
BACKOFF = 3  # detik, dikalikan nomor percobaan


def _get(url: str, params: dict[str, Any] | None = None) -> Any:
    """GET dengan retry sederhana. Melempar exception kalau semua gagal."""
    last: Exception | None = None
    for attempt in range(1, RETRIES + 1):
        try:
            resp = requests.get(
                url,
                params=params,
                timeout=TIMEOUT,
                headers={"User-Agent": "bitcoin-daily-pipeline/1.0"},
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001 - sengaja luas, di-retry
            last = exc
            log.warning("  percobaan %d/%d gagal (%s): %s", attempt, RETRIES, url, exc)
            if attempt < RETRIES:
                time.sleep(BACKOFF * attempt)
    raise RuntimeError(f"gagal fetch {url} setelah {RETRIES} percobaan") from last


def fetch_ohlcv(symbol: str = SYMBOL, days: int = DAYS) -> list[dict[str, Any]]:
    """Candle harian BTC dari Binance, urut lama -> baru.

    Candle terakhir dari Binance adalah hari yang MASIH BERJALAN (belum
    close), jadi dibuang supaya semua indikator dihitung dari candle final.
    """
    log.info("Fetch OHLCV %s (%d hari) dari Binance...", symbol, days)
    raw = _get(BINANCE_KLINES, {"symbol": symbol, "interval": "1d", "limit": days + 1})

    candles = [
        {
            "date": datetime.fromtimestamp(k[0] / 1000, timezone.utc).strftime("%Y-%m-%d"),
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
        }
        for k in raw
    ]
    dropped = candles.pop()  # candle hari berjalan
    log.info(
        "  %d candle final (%s s/d %s), candle berjalan %s dibuang",
        len(candles), candles[0]["date"], candles[-1]["date"], dropped["date"],
    )
    return candles


def fetch_fear_greed() -> dict[str, Any]:
    """Fear & Greed Index terbaru + nilai kemarin (untuk lihat perubahan)."""
    log.info("Fetch Fear & Greed Index...")
    raw = _get(FNG_URL, {"limit": 2})
    entries = raw["data"]
    today = entries[0]
    result = {
        "value": int(today["value"]),
        "classification": today["value_classification"],
        "date": datetime.fromtimestamp(
            int(today["timestamp"]), timezone.utc
        ).strftime("%Y-%m-%d"),
        "value_yesterday": int(entries[1]["value"]) if len(entries) > 1 else None,
    }
    log.info("  %s (%s)", result["value"], result["classification"])
    return result


def fetch_global_market() -> dict[str, Any]:
    """Dominance BTC dan total market cap dari CoinGecko."""
    log.info("Fetch global market dari CoinGecko...")
    data = _get(COINGECKO_GLOBAL)["data"]
    result = {
        "btc_dominance_pct": round(data["market_cap_percentage"]["btc"], 2),
        "eth_dominance_pct": round(data["market_cap_percentage"]["eth"], 2),
        "total_market_cap_usd": data["total_market_cap"]["usd"],
        "market_cap_change_24h_pct": round(data["market_cap_change_percentage_24h_usd"], 2),
    }
    log.info("  BTC dominance %.2f%%", result["btc_dominance_pct"])
    return result


def fetch_all() -> dict[str, Any]:
    """Ambil semua sumber. Sumber sekunder boleh gagal; OHLCV tidak.

    OHLCV adalah basis semua indikator teknikal, jadi kalau itu gagal
    pipeline harus berhenti. Fear & Greed dan data global cuma pelengkap
    sentimen, jadi kegagalannya dicatat sebagai None dan dilanjut.
    """
    errors: dict[str, str] = {}

    ohlcv = fetch_ohlcv()  # wajib - biarkan exception naik ke atas

    try:
        fng = fetch_fear_greed()
    except Exception as exc:  # noqa: BLE001
        log.error("Fear & Greed gagal, dilanjut tanpa data ini: %s", exc)
        errors["fear_greed"] = str(exc)
        fng = None

    try:
        glob = fetch_global_market()
    except Exception as exc:  # noqa: BLE001
        log.error("Global market gagal, dilanjut tanpa data ini: %s", exc)
        errors["global_market"] = str(exc)
        glob = None

    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "symbol": SYMBOL,
        "ohlcv": ohlcv,
        "fear_greed": fng,
        "global_market": glob,
        "fetch_errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Ambil data mentah pasar Bitcoin.")
    parser.add_argument("--out", default="output/raw.json", help="path file output JSON")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    try:
        data = fetch_all()
    except Exception as exc:  # noqa: BLE001
        log.error("FATAL: %s", exc)
        return 1

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    log.info("Tersimpan ke %s (%d candle)", args.out, len(data["ohlcv"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
