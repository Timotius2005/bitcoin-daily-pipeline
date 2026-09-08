"""Fase 2 - Analysis layer, rule-based dan deterministik.

Menghitung indikator, mendeteksi sinyal, lalu menggabungkannya jadi satu
skor komposit -100..+100. Tidak ada LLM di sini: input yang sama selalu
menghasilkan output yang sama, sehingga hasilnya bisa diaudit.

Skor per kategori dihitung dulu (trend / momentum / volatility / sentiment)
baru digabung dengan bobot. Ini disengaja: sinyal dalam satu kategori
saling berkorelasi (RSI oversold, harga di bawah lower band, dan harga di
bawah MA sering menyala bersamaan), jadi kalau semuanya dijumlah langsung
satu kondisi pasar akan terhitung berkali-kali dan skornya melonjak.

Catatan arah sinyal: RSI dan Bollinger dibaca secara MEAN-REVERSION
(oversold = bullish), sementara MA dan MACD dibaca secara TREND-FOLLOWING.
Keduanya memang bisa bertentangan, dan itu wajar - composite score yang
menengahi, dan breakdown per kategori di output menunjukkan dari mana
ketidaksepakatannya datang.

Jalankan: python analyze.py [--in output/raw.json] [--out output/latest.json]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

import indicators as ind

log = logging.getLogger("analyze")

CATEGORY_WEIGHTS = {
    "trend": 0.35,
    "momentum": 0.25,
    "sentiment": 0.25,
    "volatility": 0.15,
}

CROSS_LOOKBACK = 3  # cross masih dianggap "baru" sampai N hari setelahnya
VOLUME_SPIKE_MULT = 2.0
MIN_CANDLES = 60  # butuh MA50 + sedikit margin


def _signal(sid: str, category: str, score: int, label: str, detail: str) -> dict[str, Any]:
    return {
        "id": sid,
        "category": category,
        "direction": "bullish" if score > 0 else "bearish" if score < 0 else "neutral",
        "score": score,
        "label": label,
        "detail": detail,
    }


def compute_indicators(ohlcv: list[dict[str, Any]]) -> dict[str, Any]:
    """Hitung semua indikator, kembalikan seri lengkapnya."""
    closes = [c["close"] for c in ohlcv]
    volumes = [c["volume"] for c in ohlcv]

    macd_line, macd_sig, macd_hist = ind.macd(closes)
    bb_up, bb_mid, bb_low = ind.bollinger(closes, 20, 2.0)

    return {
        "closes": closes,
        "volumes": volumes,
        "ma20": ind.sma(closes, 20),
        "ma50": ind.sma(closes, 50),
        "rsi14": ind.rsi(closes, 14),
        "macd_line": macd_line,
        "macd_signal": macd_sig,
        "macd_hist": macd_hist,
        "bb_upper": bb_up,
        "bb_middle": bb_mid,
        "bb_lower": bb_low,
        "vol_ma20": ind.sma(volumes, 20),
    }


def detect_signals(
    ohlcv: list[dict[str, Any]], ta: dict[str, Any], fear_greed: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """Terapkan aturan sinyal ke kondisi hari terakhir."""
    signals: list[dict[str, Any]] = []
    last = ohlcv[-1]
    close = last["close"]

    ma20, ma50 = ta["ma20"][-1], ta["ma50"][-1]
    rsi = ta["rsi14"][-1]
    hist = ta["macd_hist"][-1]
    bb_up, bb_low = ta["bb_upper"][-1], ta["bb_lower"][-1]
    vol_avg = ta["vol_ma20"][-1]

    # --- TREND ---------------------------------------------------------
    if ma20 is not None and ma50 is not None:
        if ma20 > ma50:
            signals.append(_signal(
                "ma20_above_ma50", "trend", 40, "MA20 di atas MA50",
                f"MA20 {ma20:,.0f} > MA50 {ma50:,.0f} - struktur tren jangka pendek naik",
            ))
        else:
            signals.append(_signal(
                "ma20_below_ma50", "trend", -40, "MA20 di bawah MA50",
                f"MA20 {ma20:,.0f} < MA50 {ma50:,.0f} - struktur tren jangka pendek turun",
            ))

        gc = ind.crossed_up_within(ta["ma20"], ta["ma50"], CROSS_LOOKBACK)
        dc = ind.crossed_down_within(ta["ma20"], ta["ma50"], CROSS_LOOKBACK)
        if gc is not None:
            signals.append(_signal(
                "golden_cross", "trend", 30, "Golden cross MA20/MA50",
                f"MA20 memotong MA50 ke atas {gc} hari lalu",
            ))
        elif dc is not None:
            signals.append(_signal(
                "death_cross", "trend", -30, "Death cross MA20/MA50",
                f"MA20 memotong MA50 ke bawah {dc} hari lalu",
            ))

    if ma50 is not None:
        above = close > ma50
        posisi = "atas" if above else "bawah"
        signals.append(_signal(
            "price_above_ma50" if above else "price_below_ma50", "trend",
            30 if above else -30,
            f"Harga di {posisi} MA50",
            f"Close {close:,.0f} vs MA50 {ma50:,.0f}",
        ))

    # --- MOMENTUM ------------------------------------------------------
    if rsi is not None:
        if rsi < 30:
            signals.append(_signal(
                "rsi_oversold", "momentum", 50, "RSI oversold",
                f"RSI(14) {rsi:.1f} di bawah 30",
            ))
        elif rsi < 40:
            signals.append(_signal(
                "rsi_lemah", "momentum", 25, "RSI lemah",
                f"RSI(14) {rsi:.1f} mendekati wilayah oversold",
            ))
        elif rsi > 70:
            signals.append(_signal(
                "rsi_overbought", "momentum", -50, "RSI overbought",
                f"RSI(14) {rsi:.1f} di atas 70",
            ))
        elif rsi > 60:
            signals.append(_signal(
                "rsi_tinggi", "momentum", -25, "RSI tinggi",
                f"RSI(14) {rsi:.1f} mendekati wilayah overbought",
            ))

    if hist is not None:
        mc_up = ind.crossed_up_within(ta["macd_line"], ta["macd_signal"], CROSS_LOOKBACK)
        mc_down = ind.crossed_down_within(ta["macd_line"], ta["macd_signal"], CROSS_LOOKBACK)
        if mc_up is not None:
            signals.append(_signal(
                "macd_bullish_cross", "momentum", 50, "MACD cross bullish",
                f"MACD memotong signal line ke atas {mc_up} hari lalu",
            ))
        elif mc_down is not None:
            signals.append(_signal(
                "macd_bearish_cross", "momentum", -50, "MACD cross bearish",
                f"MACD memotong signal line ke bawah {mc_down} hari lalu",
            ))
        elif hist > 0:
            signals.append(_signal(
                "macd_hist_positif", "momentum", 25, "Histogram MACD positif",
                f"Histogram {hist:,.1f} - momentum masih di sisi naik",
            ))
        elif hist < 0:
            signals.append(_signal(
                "macd_hist_negatif", "momentum", -25, "Histogram MACD negatif",
                f"Histogram {hist:,.1f} - momentum masih di sisi turun",
            ))

    # --- VOLATILITY ----------------------------------------------------
    if bb_up is not None and bb_low is not None:
        if close < bb_low:
            signals.append(_signal(
                "bb_break_bawah", "volatility", 60,
                "Harga menembus lower Bollinger Band",
                f"Close {close:,.0f} di bawah lower band {bb_low:,.0f}",
            ))
        elif close > bb_up:
            signals.append(_signal(
                "bb_break_atas", "volatility", -60,
                "Harga menembus upper Bollinger Band",
                f"Close {close:,.0f} di atas upper band {bb_up:,.0f}",
            ))

    if vol_avg is not None and vol_avg > 0 and last["volume"] > vol_avg * VOLUME_SPIKE_MULT:
        # Volume spike sendiri tidak punya arah - arahnya diambil dari warna
        # candle hari itu (hijau = akumulasi, merah = distribusi).
        green = last["close"] >= last["open"]
        warna = "hijau" if green else "merah"
        ratio = last["volume"] / vol_avg
        signals.append(_signal(
            "volume_spike_naik" if green else "volume_spike_turun", "volatility",
            40 if green else -40,
            f"Lonjakan volume pada candle {warna}",
            f"Volume {ratio:.1f}x rata-rata 20 hari",
        ))

    # --- SENTIMENT -----------------------------------------------------
    if fear_greed is not None:
        v = fear_greed["value"]
        # Dibaca contrarian: pasar takut = peluang beli, serakah = waspada.
        score = max(-100, min(100, round((50 - v) * 2)))
        if v < 25:
            label = "Extreme Fear"
        elif v < 45:
            label = "Fear"
        elif v > 75:
            label = "Extreme Greed"
        elif v > 55:
            label = "Greed"
        else:
            label = "Netral"
        signals.append(_signal(
            "fear_greed", "sentiment", score, f"Sentimen pasar: {label}",
            f"Fear & Greed Index {v} ({label})",
        ))

    return signals


def score_categories(signals: list[dict[str, Any]]) -> dict[str, int]:
    """Jumlahkan skor per kategori, di-clamp ke -100..100."""
    totals: dict[str, int] = {}
    for sig in signals:
        totals[sig["category"]] = totals.get(sig["category"], 0) + sig["score"]
    return {cat: max(-100, min(100, val)) for cat, val in totals.items()}


def composite_score(category_scores: dict[str, int]) -> int:
    """Rata-rata tertimbang dari skor kategori.

    Kategori yang datanya tidak tersedia di-skip dan bobotnya dinormalisasi
    ulang, bukan dianggap nol - supaya hilangnya satu sumber data tidak
    diam-diam menarik skor ke arah netral.
    """
    used = {c: w for c, w in CATEGORY_WEIGHTS.items() if c in category_scores}
    if not used:
        return 0
    total_w = sum(used.values())
    return round(sum(category_scores[c] * w for c, w in used.items()) / total_w)


def classify(score: int) -> str:
    if score >= 50:
        return "bullish kuat"
    if score >= 20:
        return "bullish"
    if score > -20:
        return "netral"
    if score > -50:
        return "bearish"
    return "bearish kuat"


def _pct_change(closes: list[float], days: int) -> float | None:
    if len(closes) <= days:
        return None
    old = closes[-1 - days]
    if old == 0:
        return None
    return round((closes[-1] / old - 1) * 100, 2)


def _r(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


def analyze(raw: dict[str, Any]) -> dict[str, Any]:
    """Ubah data mentah Fase 1 jadi output analisis final."""
    ohlcv = raw["ohlcv"]
    if len(ohlcv) < MIN_CANDLES:
        raise ValueError(f"butuh minimal {MIN_CANDLES} candle, hanya ada {len(ohlcv)}")

    ta = compute_indicators(ohlcv)
    signals = detect_signals(ohlcv, ta, raw.get("fear_greed"))
    cats = score_categories(signals)
    score = composite_score(cats)

    closes = ta["closes"]
    last = ohlcv[-1]
    vol_ma = ta["vol_ma20"][-1]
    glob = raw.get("global_market") or {}

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data_as_of": last["date"],
        "symbol": raw.get("symbol", "BTCUSDT"),
        "price": {
            "close": round(last["close"], 2),
            "open": round(last["open"], 2),
            "high": round(last["high"], 2),
            "low": round(last["low"], 2),
            "change_1d_pct": _pct_change(closes, 1),
            "change_7d_pct": _pct_change(closes, 7),
            "change_30d_pct": _pct_change(closes, 30),
        },
        "indicators": {
            "rsi14": _r(ta["rsi14"][-1], 2),
            "ma20": _r(ta["ma20"][-1], 2),
            "ma50": _r(ta["ma50"][-1], 2),
            "macd_line": _r(ta["macd_line"][-1], 2),
            "macd_signal": _r(ta["macd_signal"][-1], 2),
            "macd_hist": _r(ta["macd_hist"][-1], 2),
            "bb_upper": _r(ta["bb_upper"][-1], 2),
            "bb_middle": _r(ta["bb_middle"][-1], 2),
            "bb_lower": _r(ta["bb_lower"][-1], 2),
            "bb_percent_b": _r(
                ind.percent_b(last["close"], ta["bb_upper"][-1], ta["bb_lower"][-1]), 3),
            "volume": round(last["volume"], 2),
            "volume_ma20": _r(vol_ma, 2),
            "volume_ratio": _r(last["volume"] / vol_ma if vol_ma else None, 2),
        },
        "sentiment": {
            "fear_greed": raw.get("fear_greed"),
            "btc_dominance_pct": glob.get("btc_dominance_pct"),
            "market_cap_change_24h_pct": glob.get("market_cap_change_24h_pct"),
        },
        "signals": signals,
        "scores": {
            "composite": score,
            "label": classify(score),
            "by_category": cats,
            "weights_used": {c: w for c, w in CATEGORY_WEIGHTS.items() if c in cats},
        },
        "meta": {
            "candles_used": len(ohlcv),
            "fetched_at": raw.get("fetched_at"),
            "fetch_errors": raw.get("fetch_errors", {}),
            "scoring_version": 1,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Analisis rule-based data Bitcoin.")
    parser.add_argument("--in", dest="infile", default="output/raw.json")
    parser.add_argument("--out", dest="outfile", default="output/latest.json")
    parser.add_argument(
        "--archive-dir", default="output/history",
        help="folder arsip harian; kosongkan ('') untuk melewati pengarsipan",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    try:
        with open(args.infile, encoding="utf-8") as fh:
            raw = json.load(fh)
        result = analyze(raw)
    except Exception as exc:  # noqa: BLE001
        log.error("FATAL: %s", exc)
        return 1

    payload = json.dumps(result, indent=2, ensure_ascii=False)
    with open(args.outfile, "w", encoding="utf-8") as fh:
        fh.write(payload)

    # Arsip harian: latest.json cuma menyimpan snapshot terakhir, jadi kalau
    # konsumen (dashboard) mati beberapa hari datanya hilang permanen. Arsip
    # per tanggal bikin data yang terlewat masih bisa di-backfill, sekaligus
    # jadi dataset untuk backtesting nanti.
    if args.archive_dir:
        os.makedirs(args.archive_dir, exist_ok=True)
        archive_path = os.path.join(args.archive_dir, f"{result['data_as_of']}.json")
        with open(archive_path, "w", encoding="utf-8") as fh:
            fh.write(payload)
        log.info("Arsip: %s", archive_path)

    log.info("Data per %s, close %s", result["data_as_of"], result["price"]["close"])
    for sig in result["signals"]:
        log.info("  [%-10s] %+4d  %s", sig["category"], sig["score"], sig["label"])
    log.info("Skor per kategori: %s", result["scores"]["by_category"])
    log.info("KOMPOSIT: %+d (%s) -> %s",
             result["scores"]["composite"], result["scores"]["label"], args.outfile)
    return 0


if __name__ == "__main__":
    sys.exit(main())
