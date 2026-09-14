"""Backtest skor komposit terhadap pergerakan harga sesudahnya.

Pertanyaan yang dijawab: kalau skor hari ini tinggi, apakah harga cenderung
naik sesudahnya? Apakah indikator tambahan di scoring v2 (ADX, OBV, ATR)
membuat skor lebih informatif daripada v1? Dan apakah tiap aturan baru
didukung data, atau cuma terdengar masuk akal?

Ini EVALUASI, bukan optimasi. Semua ambang (RSI 30/70, ADX 20/25, dst.)
adalah nilai buku teks yang ditetapkan sebelum backtest ini dijalankan.
Menyetel ulang ambang supaya angka laporan membaik lalu memakai laporan yang
sama sebagai bukti adalah overfitting: data yang sama tidak bisa sekaligus
dipakai untuk mencari parameter dan membuktikannya.

Tidak ada look-ahead:
  * skor hari D hanya memakai candle sampai D, dari jendela geser 200 candle
    - persis yang dilihat pipeline harian,
  * return ke depan diukur dari close D ke close D+h,
  * posisi strategi yang diputuskan di close D baru mendapat return D -> D+1.

Jalankan: python backtest.py [--days 730]
Hasil:    output/backtest/REPORT.md, report.json, equity.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import os
import statistics
import sys
from datetime import datetime, timezone
from typing import Any, Callable

import analyze as az
import backfill as bf
import fetch as ft

log = logging.getLogger("backtest")

HORIZONS = (1, 7, 30)
VERSIONS = (1, 2)
WINDOW = ft.DAYS
FEE = 0.001  # 0,1% per transaksi, kira-kira taker fee spot Binance
BULLISH_MIN = 20
MIN_BUCKET = 20  # label dengan hari lebih sedikit dari ini tidak disimpulkan
LABEL_ORDER = ("bearish kuat", "bearish", "netral", "bullish", "bullish kuat")
CATEGORIES = ("trend", "momentum", "sentiment", "volatility")
CATEGORY_NAMES = {
    "trend": "Tren",
    "momentum": "Momentum",
    "sentiment": "Sentimen",
    "volatility": "Volatilitas & volume",
}
MAX_DAYS = bf.BINANCE_MAX_LIMIT - WINDOW


# ── Statistik dasar, tanpa numpy/scipy (sejalan dengan indicators.py) ────

def forward_returns(closes: list[float], horizon: int) -> list[float | None]:
    """Return dari close hari i ke close hari i+horizon; None kalau belum ada."""
    return [
        closes[i + horizon] / closes[i] - 1 if i + horizon < len(closes) else None
        for i in range(len(closes))
    ]


def rank(values: list[float]) -> list[float]:
    """Peringkat mulai 1; nilai kembar mendapat rata-rata posisinya."""
    urut = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(urut):
        j = i
        while j + 1 < len(urut) and values[urut[j + 1]] == values[urut[i]]:
            j += 1
        rata = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[urut[k]] = rata
        i = j + 1
    return ranks


def pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3 or n != len(ys):
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """Korelasi peringkat: tahan outlier dan tidak mengasumsikan hubungan linear."""
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    return pearson(rank(xs), rank(ys))


def t_stat(r: float | None, n: int) -> float | None:
    if r is None or n < 3 or abs(r) >= 1:
        return None
    return r * math.sqrt((n - 2) / (1 - r * r))


def describe(values: list[float | None]) -> dict[str, Any]:
    vals = [v for v in values if v is not None]
    if not vals:
        return {"n": 0, "mean": None, "median": None, "up_rate": None,
                "down_rate": None, "mean_abs": None}
    n = len(vals)
    return {
        "n": n,
        "mean": statistics.fmean(vals),
        "median": statistics.median(vals),
        "up_rate": sum(v > 0 for v in vals) / n,
        "down_rate": sum(v < 0 for v in vals) / n,
        "mean_abs": statistics.fmean(abs(v) for v in vals),
    }


# ── Pengumpulan skor ─────────────────────────────────────────────────────

def summarize_result(hasil: dict[str, Any]) -> dict[str, Any]:
    """Ambil yang dibutuhkan evaluasi dari satu output analyze()."""
    penyesuaian = {a["category"]: a for a in hasil["scores"].get("adjustments", [])}
    kategori = hasil["scores"]["by_category"]
    tren_mentah = (penyesuaian["trend"]["before"] if "trend" in penyesuaian
                   else kategori.get("trend"))
    return {
        "date": hasil["data_as_of"],
        "score": hasil["scores"]["composite"],
        "label": hasil["scores"]["label"],
        "cats": kategori,
        "trend_raw": tren_mentah,
        "adx": hasil["indicators"].get("adx14"),
        "atr_pctl": hasil["indicators"].get("atr_pct_percentile"),
        "signals": [
            {"id": s["id"], "kind": s.get("kind", "score"), "score": s["score"],
             "label": s["label"], "category": s["category"]}
            for s in hasil["signals"]
        ],
    }


def build_records(
    ohlcv: list[dict[str, Any]],
    fng: dict[str, dict],
    days: int,
    versions: tuple[int, ...] = VERSIONS,
) -> tuple[int, dict[int, list[dict[str, Any]]]]:
    """Hitung skor setiap versi untuk `days` hari terakhir yang punya jendela penuh.

    Return (indeks candle hari pertama, rekaman per versi).
    """
    n = len(ohlcv)
    mulai = max(WINDOW - 1, n - days)
    records: dict[int, list[dict[str, Any]]] = {v: [] for v in versions}
    for nomor, i in enumerate(range(mulai, n)):
        jendela = ohlcv[i - WINDOW + 1 : i + 1]
        tanggal = jendela[-1]["date"]
        # Sama dengan backfill: Fear & Greed tanggal yang sama. Pipeline live
        # memakai nilai yang terbit pagi berikutnya, jadi pilihan ini sedikit
        # lebih konservatif - tidak mungkin membocorkan informasi masa depan.
        raw = {
            "symbol": ft.SYMBOL,
            "ohlcv": jendela,
            "fear_greed": fng.get(tanggal),
            "global_market": None,
            "fetch_errors": {},
        }
        for v in versions:
            records[v].append(summarize_result(az.analyze(raw, version=v)))
        if nomor and nomor % 100 == 0:
            log.info("  %d/%d hari", nomor, n - mulai)
    return mulai, records


# ── Evaluasi ─────────────────────────────────────────────────────────────

def information_coefficient(
    scores: list[float], fwd: list[float | None], horizon: int
) -> dict[str, Any]:
    """IC = korelasi Spearman skor vs return ke depan.

    Return h-hari yang tumpang tindih saling berkorelasi: 30 sampel harian
    untuk return 30 hari sebenarnya cuma sekitar satu pengamatan independen.
    Uji t karena itu memakai sampel efektif n // h, bukan n.
    """
    pasangan = [(s, f) for s, f in zip(scores, fwd) if f is not None]
    n = len(pasangan)
    n_eff = n // horizon
    ic = spearman([p[0] for p in pasangan], [p[1] for p in pasangan])
    return {"ic": ic, "n": n, "n_eff": n_eff, "t": t_stat(ic, n_eff)}


def bucket_stats(labels: list[str], fwd: list[float | None]) -> list[dict[str, Any]]:
    per_label: dict[str, list[float]] = {}
    for label, f in zip(labels, fwd):
        if f is not None:
            per_label.setdefault(label, []).append(f)
    return [{"label": lab, **describe(per_label.get(lab, []))} for lab in LABEL_ORDER]


def category_ic(
    records: list[dict[str, Any]], fwd: list[float | None], horizon: int
) -> dict[str, dict[str, Any]]:
    out = {}
    for cat in CATEGORIES:
        pasangan = [(r["cats"][cat], f) for r, f in zip(records, fwd)
                    if f is not None and cat in r["cats"]]
        ic = spearman([p[0] for p in pasangan], [p[1] for p in pasangan])
        out[cat] = {"ic": ic, "n": len(pasangan), "t": t_stat(ic, len(pasangan) // horizon)}
    return out


def _signal_name(sig: dict[str, Any], sign: int) -> str:
    # Label Fear & Greed berubah mengikuti nilainya, jadi dikelompokkan per arah.
    if sig["id"] == "fear_greed":
        return {1: "Sentimen takut (contrarian bullish)",
                -1: "Sentimen serakah (contrarian bearish)"}.get(sign, "Sentimen netral")
    return sig["label"]


def signal_stats(records: list[dict[str, Any]], fwd: list[float | None]) -> list[dict[str, Any]]:
    """Untuk tiap sinyal berskor: seberapa sering muncul dan seberapa sering arahnya tepat."""
    kumpulan: dict[tuple[str, int], dict[str, Any]] = {}
    for r, f in zip(records, fwd):
        if f is None:
            continue
        for sig in r["signals"]:
            if sig["kind"] == "info":
                continue
            tanda = 1 if sig["score"] > 0 else -1 if sig["score"] < 0 else 0
            entri = kumpulan.setdefault((sig["id"], tanda), {
                "id": sig["id"], "label": _signal_name(sig, tanda),
                "category": sig["category"], "sign": tanda, "returns": [],
            })
            entri["returns"].append(f)

    rows = []
    for entri in kumpulan.values():
        vals = entri.pop("returns")
        n = len(vals)
        if entri["sign"] > 0:
            tepat = sum(v > 0 for v in vals) / n
        elif entri["sign"] < 0:
            tepat = sum(v < 0 for v in vals) / n
        else:
            tepat = None
        rows.append({**entri, "n": n, "mean": statistics.fmean(vals), "hit_rate": tepat})
    rows.sort(key=lambda r: (-r["n"], r["id"], -r["sign"]))
    return rows


def adx_gate_check(records: list[dict[str, Any]], fwd: list[float | None]) -> dict[str, Any]:
    """Apakah arah tren memang lebih sering salah saat ADX lemah?

    Kalau tidak, peredaman skor tren di v2 tidak punya dasar dan layak dicabut.
    """
    grup: dict[str, list[bool]] = {"kuat": [], "lemah": []}
    for r, f in zip(records, fwd):
        if f is None or f == 0 or r["adx"] is None or not r["trend_raw"]:
            continue
        tepat = (f > 0) == (r["trend_raw"] > 0)
        grup["lemah" if r["adx"] < az.ADX_WEAK else "kuat"].append(tepat)
    return {k: {"n": len(v), "hit_rate": sum(v) / len(v) if v else None}
            for k, v in grup.items()}


def atr_regime_check(records: list[dict[str, Any]], fwd: list[float | None]) -> dict[str, Any]:
    """Seberapa besar pergerakan sesudah rezim volatilitas ekstrem vs sangat rendah."""
    def rata_abs(vals: list[float]) -> float | None:
        return statistics.fmean(abs(v) for v in vals) if vals else None

    rendah, ekstrem = [], []
    for r, f in zip(records, fwd):
        if f is None or r["atr_pctl"] is None:
            continue
        if r["atr_pctl"] <= az.ATR_CALM_PCTL:
            rendah.append(f)
        elif r["atr_pctl"] >= az.ATR_EXTREME_PCTL:
            ekstrem.append(f)
    semua = [f for f in fwd if f is not None]
    return {
        "semua": {"n": len(semua), "mean_abs": rata_abs(semua)},
        "rendah": {"n": len(rendah), "mean_abs": rata_abs(rendah)},
        "ekstrem": {"n": len(ekstrem), "mean_abs": rata_abs(ekstrem)},
    }


def simulate(
    closes: list[float], positions: list[int], fee: float = FEE
) -> tuple[dict[str, Any], list[float]]:
    """Posisi (0 = kas, 1 = long) diputuskan di close i, mendapat return i -> i+1.

    Biaya dipotong setiap kali posisi berubah. Return (metrik, kurva ekuitas).
    """
    if len(closes) != len(positions):
        raise ValueError("panjang closes dan positions harus sama")
    equity = [1.0]
    harian: list[float] = []
    posisi_lama = 0
    transaksi = 0
    for i in range(len(closes) - 1):
        posisi = positions[i]
        biaya = 0.0
        if posisi != posisi_lama:
            biaya = fee
            transaksi += 1
        r = posisi * (closes[i + 1] / closes[i] - 1) - biaya
        harian.append(r)
        equity.append(equity[-1] * (1 + r))
        posisi_lama = posisi
    return _performance(equity, harian, positions[:-1], transaksi), equity


def _performance(
    equity: list[float], harian: list[float], exposure: list[int], trades: int
) -> dict[str, Any]:
    hari = len(harian)
    tahun = hari / 365  # kripto diperdagangkan setiap hari
    akhir = equity[-1]
    puncak, drawdown = equity[0], 0.0
    for e in equity:
        puncak = max(puncak, e)
        drawdown = min(drawdown, e / puncak - 1)
    sd = statistics.pstdev(harian) if hari > 1 else 0.0
    return {
        "total_return": akhir - 1,
        "cagr": akhir ** (1 / tahun) - 1 if tahun > 0 and akhir > 0 else None,
        "max_drawdown": drawdown,
        "sharpe": statistics.fmean(harian) / sd * math.sqrt(365) if sd > 0 else None,
        "exposure": sum(exposure) / len(exposure) if exposure else None,
        "trades": trades,
        "days": hari,
    }


STRATEGIES: tuple[tuple[str, str, Callable[[int], bool]], ...] = (
    ("bullish", f"long saat bullish (skor ≥ {BULLISH_MIN})", lambda s: s >= BULLISH_MIN),
    ("positive", "long saat skor > 0", lambda s: s > 0),
)


def evaluate(
    dates: list[str],
    closes: list[float],
    records: dict[int, list[dict[str, Any]]],
    fee: float = FEE,
) -> tuple[dict[str, Any], dict[str, list[float]]]:
    fwd = {h: forward_returns(closes, h) for h in HORIZONS}
    buy_hold, kurva_bh = simulate(closes, [1] * len(closes), fee)

    report: dict[str, Any] = {
        "period": {
            "start": dates[0], "end": dates[-1], "days": len(dates),
            "window": WINDOW, "fee": fee, "horizons": list(HORIZONS),
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "thresholds": {
                "bullish_min": BULLISH_MIN, "adx_weak": az.ADX_WEAK,
                "adx_strong": az.ADX_STRONG, "trend_dampen": az.TREND_DAMPEN,
                "atr_calm_pctl": az.ATR_CALM_PCTL, "atr_extreme_pctl": az.ATR_EXTREME_PCTL,
            },
        },
        "baseline": {str(h): describe(fwd[h]) for h in HORIZONS},
        "buy_hold": buy_hold,
        "versions": {},
    }
    curves = {"buy_hold": kurva_bh}

    for versi, recs in records.items():
        skor = [r["score"] for r in recs]
        label = [r["label"] for r in recs]
        strategi = {}
        for kunci, _, aturan in STRATEGIES:
            metrik, kurva = simulate(closes, [1 if aturan(s) else 0 for s in skor], fee)
            strategi[kunci] = metrik
            curves[f"v{versi}_{kunci}"] = kurva

        hasil_versi: dict[str, Any] = {
            "ic": {str(h): information_coefficient(skor, fwd[h], h) for h in HORIZONS},
            "buckets": {str(h): bucket_stats(label, fwd[h]) for h in HORIZONS},
            "categories": {str(h): category_ic(recs, fwd[h], h) for h in (7, 30)},
            "signals": signal_stats(recs, fwd[7]),
            "strategies": strategi,
        }
        if versi >= 2:
            hasil_versi["adx_gate"] = adx_gate_check(recs, fwd[7])
            hasil_versi["atr_regime"] = atr_regime_check(recs, fwd[7])
        report["versions"][str(versi)] = hasil_versi

    return report, curves


# ── Laporan ──────────────────────────────────────────────────────────────

def _num(x: float | None, digits: int = 2) -> str:
    if x is None:
        return "—"
    teks = f"{abs(x):,.{digits}f}".replace(",", "~").replace(".", ",").replace("~", ".")
    return ("−" if x < 0 else "") + teks


def _pct(x: float | None, digits: int = 1, sign: bool = True) -> str:
    if x is None:
        return "—"
    v = x * 100
    teks = _num(abs(v), digits) + "%"
    if v < 0:
        return "−" + teks
    return ("+" if sign and v > 0 else "") + teks


def _share(x: float | None) -> str:
    return "—" if x is None else _num(x * 100, 0) + "%"


def _ic(x: float | None) -> str:
    if x is None:
        return "—"
    return ("+" if x > 0 else "") + _num(x, 3)


def _significance(t: float | None) -> str:
    if t is None:
        return "tidak bisa diuji"
    return "signifikan secara statistik (|t| ≥ 2)" if abs(t) >= 2 else "tidak signifikan secara statistik"


def summary_points(report: dict[str, Any]) -> list[str]:
    versi = report["versions"]
    dasar7 = report["baseline"]["7"]
    ambang = report["period"]["thresholds"]
    poin: list[str] = []

    if "1" in versi and "2" in versi:
        a, b = versi["1"]["ic"]["7"], versi["2"]["ic"]["7"]
        poin.append(
            f"**Daya prediksi skor (7 hari ke depan):** IC v2 {_ic(b['ic'])}, v1 {_ic(a['ic'])}. "
            f"Untuk v2 ini {_significance(b['t'])}, dengan {b['n_eff']} sampel efektif."
        )
        if a["ic"] is not None and b["ic"] is not None:
            selisih = b["ic"] - a["ic"]
            catatan = (" Selisih sekecil ini sebaiknya dianggap noise, bukan perbaikan."
                       if abs(selisih) < 0.05 else "")
            poin.append(f"**Indikator tambahan:** selisih IC 7 hari v2 − v1 = {_ic(selisih)}.{catatan}")

    if "2" in versi:
        v2 = versi["2"]
        cukup = [r for r in v2["buckets"]["7"] if r["n"] >= MIN_BUCKET and r["mean"] is not None]
        if len(cukup) >= 2 and dasar7["mean"] is not None:
            tinggi = max(cukup, key=lambda r: r["mean"])
            rendah = min(cukup, key=lambda r: r["mean"])
            urutan = spearman([LABEL_ORDER.index(r["label"]) for r in cukup],
                              [r["mean"] for r in cukup]) if len(cukup) >= 3 else None
            searah = ""
            if urutan is not None:
                searah = (" Urutan rata-rata return per label **searah** dengan urutan skor."
                          if urutan > 0.5 else
                          " Urutan rata-rata return per label **tidak searah** dengan urutan skor.")
            poin.append(
                f"**Per label (v2, 7 hari):** return rata-rata tertinggi setelah *{tinggi['label']}* "
                f"({_pct(tinggi['mean'])}, {tinggi['n']} hari), terendah setelah *{rendah['label']}* "
                f"({_pct(rendah['mean'])}, {rendah['n']} hari). Semua hari: {_pct(dasar7['mean'])}.{searah}"
            )

        kategori = {k: c for k, c in v2["categories"]["7"].items() if c["ic"] is not None}
        if kategori:
            terkuat = max(kategori, key=lambda k: abs(kategori[k]["ic"]))
            poin.append(
                f"**Kategori paling informatif (7 hari):** {CATEGORY_NAMES[terkuat]}, "
                f"IC {_ic(kategori[terkuat]['ic'])} — {_significance(kategori[terkuat]['t'])}."
            )

        negatif = [CATEGORY_NAMES[k] for k, c in v2["categories"]["7"].items()
                   if c["ic"] is not None and c["ic"] < 0]
        if negatif:
            poin.append(
                f"**Arah pembacaan yang tidak didukung data (IC 7 hari negatif):** "
                f"{', '.join(negatif)}. Belum signifikan, tapi layak diuji di periode lain "
                "sebelum aturannya diubah."
            )

        gate = v2.get("adx_gate")
        if gate and gate["kuat"]["hit_rate"] is not None and gate["lemah"]["hit_rate"] is not None:
            kuat, lemah = gate["kuat"]["hit_rate"], gate["lemah"]["hit_rate"]
            if abs(kuat - lemah) < 0.03:
                tafsir = "selisihnya terlalu kecil untuk mendukung maupun menolak peredaman"
            elif lemah < kuat:
                tafsir = "mendukung peredaman skor tren saat ADX lemah"
            else:
                tafsir = ("**tidak mendukung** peredaman — sinyal tren tidak lebih buruk saat "
                          "ADX lemah, jadi aturan ini layak dicabut")
            poin.append(
                f"**Validasi ADX:** arah tren tepat {_share(kuat)} saat ADX ≥ {ambang['adx_weak']:.0f} "
                f"({gate['kuat']['n']} hari) vs {_share(lemah)} saat ADX < {ambang['adx_weak']:.0f} "
                f"({gate['lemah']['n']} hari) — {tafsir}."
            )

        rezim = v2.get("atr_regime")
        if rezim and rezim["semua"]["mean_abs"]:
            bagian = []
            for kunci, nama in (("rendah", "volatilitas sangat rendah"),
                                ("ekstrem", "volatilitas ekstrem")):
                if rezim[kunci]["n"] >= 10 and rezim[kunci]["mean_abs"] is not None:
                    rasio = rezim[kunci]["mean_abs"] / rezim["semua"]["mean_abs"]
                    bagian.append(f"{_num(rasio, 2)}× rata-rata setelah {nama} ({rezim[kunci]['n']} hari)")
            if bagian:
                poin.append("**Validasi ATR:** besar pergerakan 7 hari ke depan " + "; ".join(bagian) + ".")

        s = v2["strategies"]["bullish"]
        bh = report["buy_hold"]
        poin.append(
            f"**Strategi long saat bullish (v2):** total {_pct(s['total_return'])}, max drawdown "
            f"{_pct(s['max_drawdown'])}, di pasar {_share(s['exposure'])} waktu. Buy & hold: total "
            f"{_pct(bh['total_return'])}, max drawdown {_pct(bh['max_drawdown'])}."
        )
        if "1" in versi:
            s1 = versi["1"]["strategies"]["bullish"]
            t_semua = (versi["1"]["ic"]["7"]["t"], versi["2"]["ic"]["7"]["t"])
            catatan = ""
            if all(t is None or abs(t) < 2 for t in t_semua):
                catatan = (" Dengan IC kedua versi yang tidak signifikan, selisih sebesar ini lebih "
                           "mungkin berasal dari beberapa pergerakan besar yang kebetulan tertangkap "
                           "atau terlewat daripada dari perbedaan kualitas skor.")
            poin.append(
                f"**Strategi yang sama, v1 vs v2:** v1 total {_pct(s1['total_return'])} dari "
                f"{s1['trades']} transaksi, v2 {_pct(s['total_return'])} dari {s['trades']} "
                f"transaksi.{catatan}"
            )
    return poin


def render_markdown(report: dict[str, Any]) -> str:
    p = report["period"]
    ambang = p["thresholds"]
    versi = report["versions"]
    dasar = report["baseline"]
    urutan_versi = [k for k in ("1", "2") if k in versi]
    L: list[str] = []
    tulis = L.append

    tulis("# Backtest skor komposit")
    tulis("")
    tulis(f"**Periode:** {p['start']} s/d {p['end']} · {p['days']} hari skor · "
          f"BTCUSDT harian (Binance) + Fear & Greed (alternative.me)  ")
    tulis(f"**Dijalankan:** {p['generated_at'][:10]} · biaya {_pct(p['fee'], 1, sign=False)} per "
          f"transaksi · jendela {p['window']} candle per hari")
    tulis("")
    tulis("> Ini evaluasi, bukan optimasi: semua ambang adalah nilai buku teks yang ditetapkan "
          "sebelum backtest dijalankan. Hasil masa lalu tidak menjamin hasil masa depan, dan "
          "laporan ini bukan saran keuangan.")
    tulis("")
    tulis("## Ringkasan")
    tulis("")
    for poin in summary_points(report):
        tulis(f"- {poin}")
    tulis("")

    # 1. IC
    tulis("## 1. Apakah skor memprediksi return?")
    tulis("")
    tulis("*Information coefficient* (IC) adalah korelasi peringkat Spearman antara skor hari D "
          "dan return dari close D ke close D+h. Nol berarti tidak ada hubungan; positif berarti "
          "skor tinggi cenderung diikuti return lebih tinggi. Uji t memakai sampel efektif n/h, "
          "karena return h-hari yang tumpang tindih saling berkorelasi.")
    tulis("")
    tulis("| Versi | " + " | ".join(f"{h} hari" for h in p["horizons"]) + " |")
    tulis("|---|" + "---:|" * len(p["horizons"]))
    for k in urutan_versi:
        sel = []
        for h in p["horizons"]:
            ic = versi[k]["ic"][str(h)]
            sel.append(f"{_ic(ic['ic'])} (t {_num(ic['t'], 1)}, n efektif {ic['n_eff']})")
        tulis(f"| v{k} | " + " | ".join(sel) + " |")
    tulis("")

    # 2. Per label
    tulis("## 2. Return 7 hari setelah tiap label")
    tulis("")
    kepala = ["Label"]
    for k in urutan_versi:
        kepala += [f"v{k} hari", f"v{k} rata-rata", f"v{k} naik"]
    tulis("| " + " | ".join(kepala) + " |")
    tulis("|---|" + "---:|" * (len(kepala) - 1))
    for idx, lab in enumerate(LABEL_ORDER):
        baris = [lab]
        for k in urutan_versi:
            r = versi[k]["buckets"]["7"][idx]
            baris += [str(r["n"]), _pct(r["mean"]), _share(r["up_rate"])]
        tulis("| " + " | ".join(baris) + " |")
    d7 = dasar["7"]
    baris = ["**semua hari**"]
    for _ in urutan_versi:
        baris += [str(d7["n"]), _pct(d7["mean"]), _share(d7["up_rate"])]
    tulis("| " + " | ".join(baris) + " |")
    tulis("")
    tulis(f"Label dengan kurang dari {MIN_BUCKET} hari tidak disimpulkan di ringkasan.")
    tulis("")

    if "2" in versi:
        v2 = versi["2"]

        # 3. Kategori
        tulis("## 3. Kategori mana yang membawa informasi (v2)")
        tulis("")
        tulis("| Kategori | IC 7 hari | IC 30 hari | Hari aktif |")
        tulis("|---|---:|---:|---:|")
        for cat in CATEGORIES:
            c7, c30 = v2["categories"]["7"][cat], v2["categories"]["30"][cat]
            tulis(f"| {CATEGORY_NAMES[cat]} | {_ic(c7['ic'])} (t {_num(c7['t'], 1)}) | "
                  f"{_ic(c30['ic'])} (t {_num(c30['t'], 1)}) | {c7['n']} |")
        tulis("")

        # 4. Sinyal
        tulis("## 4. Sinyal individual (v2, 7 hari)")
        tulis("")
        tulis("*Arah tepat* = persentase hari sinyal bullish diikuti kenaikan (atau sinyal bearish "
              "diikuti penurunan). Bandingkan dengan *dasar*: persentase semua hari yang naik "
              "(untuk sinyal bullish) atau turun (untuk sinyal bearish). Sinyal baru berguna kalau "
              "arah tepatnya jelas di atas dasarnya.")
        tulis("")
        tulis("| Sinyal | Kategori | Muncul | Rata-rata 7 hari | Arah tepat | Dasar |")
        tulis("|---|---|---:|---:|---:|---:|")
        for s in v2["signals"]:
            acuan = d7["up_rate"] if s["sign"] > 0 else d7["down_rate"] if s["sign"] < 0 else None
            tulis(f"| {s['label']} | {CATEGORY_NAMES.get(s['category'], s['category'])} | {s['n']} | "
                  f"{_pct(s['mean'])} | {_share(s['hit_rate'])} | {_share(acuan)} |")
        tulis("")

        # 5. Validasi
        tulis("## 5. Validasi aturan baru")
        tulis("")
        gate = v2.get("adx_gate")
        if gate:
            tulis(f"**ADX** — v2 meredam skor tren ×{_num(ambang['trend_dampen'], 1)} saat "
                  f"ADX < {ambang['adx_weak']:.0f}. Aturan itu hanya masuk akal kalau arah tren memang "
                  f"lebih sering salah di kondisi tersebut:")
            tulis("")
            tulis("| Kondisi | Hari | Arah tren tepat (7 hari) |")
            tulis("|---|---:|---:|")
            tulis(f"| ADX ≥ {ambang['adx_weak']:.0f} | {gate['kuat']['n']} | {_share(gate['kuat']['hit_rate'])} |")
            tulis(f"| ADX < {ambang['adx_weak']:.0f} | {gate['lemah']['n']} | {_share(gate['lemah']['hit_rate'])} |")
            tulis("")
        rezim = v2.get("atr_regime")
        if rezim:
            tulis("**ATR** — tidak memengaruhi skor, tapi dilaporkan sebagai konteks. Apakah rezim "
                  "volatilitasnya memang mendahului pergerakan yang berbeda besarnya?")
            tulis("")
            tulis("| Kondisi | Hari | Rata-rata \\|return 7 hari\\| |")
            tulis("|---|---:|---:|")
            tulis(f"| Semua hari | {rezim['semua']['n']} | {_pct(rezim['semua']['mean_abs'], sign=False)} |")
            tulis(f"| Volatilitas sangat rendah (persentil ≤ {ambang['atr_calm_pctl']:.0f}) | "
                  f"{rezim['rendah']['n']} | {_pct(rezim['rendah']['mean_abs'], sign=False)} |")
            tulis(f"| Volatilitas ekstrem (persentil ≥ {ambang['atr_extreme_pctl']:.0f}) | "
                  f"{rezim['ekstrem']['n']} | {_pct(rezim['ekstrem']['mean_abs'], sign=False)} |")
            tulis("")
        tulis("**OBV** — lihat baris *Volume mengonfirmasi*, *Kenaikan tanpa dukungan volume*, dan "
              "*Akumulasi saat harga lemah* di bagian 4.")
        tulis("")

    # 6. Strategi
    tulis("## 6. Strategi sederhana")
    tulis("")
    tulis("Long atau kas saja, tanpa short dan tanpa leverage. Posisi diputuskan di close hari D "
          "dan baru mendapat return hari berikutnya. Ini ilustrasi apakah skor bisa dipakai, "
          "bukan strategi yang siap dijalankan.")
    tulis("")
    tulis("| Strategi | Total | CAGR | Max drawdown | Sharpe | Di pasar | Transaksi |")
    tulis("|---|---:|---:|---:|---:|---:|---:|")

    def baris_strategi(nama: str, m: dict[str, Any]) -> str:
        return (f"| {nama} | {_pct(m['total_return'])} | {_pct(m['cagr'])} | "
                f"{_pct(m['max_drawdown'])} | {_num(m['sharpe'], 2)} | "
                f"{_share(m['exposure'])} | {m['trades']} |")

    tulis(baris_strategi("Buy & hold", report["buy_hold"]))
    for k in urutan_versi:
        for kunci, nama, _ in STRATEGIES:
            tulis(baris_strategi(f"v{k} · {nama}", versi[k]["strategies"][kunci]))
    tulis("")

    # 7. Batasan
    tulis("## Batasan")
    tulis("")
    tulis(f"- Satu aset dan satu periode ({p['start']} s/d {p['end']}) dengan rezim pasarnya "
          "sendiri. Periode yang didominasi tren naik menguntungkan buy & hold dan aturan tren.")
    tulis("- Eksekusi di harga close tanpa slippage. Biaya dihitung per perubahan posisi.")
    tulis("- Fear & Greed historis memakai nilai tanggal yang sama, sedikit lebih konservatif "
          "daripada pipeline live yang melihat nilai terbitan pagi berikutnya.")
    tulis("- Untuk horizon 30 hari, sampel efektifnya hanya sekitar n/30 — sangat sedikit untuk "
          "menyimpulkan apa pun dengan yakin.")
    tulis("- Jangan menyetel ambang supaya angka laporan ini membaik lalu memakai laporan yang "
          "sama sebagai bukti. Itu overfitting; validasi perubahan ambang butuh periode data lain.")
    tulis("")
    tulis("Jalankan ulang: `python backtest.py --days 730`. Data mentah per strategi ada di "
          "`equity.csv`, semua angka di `report.json`.")
    return "\n".join(L) + "\n"


def write_equity_csv(path: str, dates: list[str], curves: dict[str, list[float]]) -> None:
    kolom = list(curves)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        penulis = csv.writer(fh)
        penulis.writerow(["date", *kolom])
        for i, tanggal in enumerate(dates):
            penulis.writerow([tanggal, *(f"{curves[k][i]:.6f}" for k in kolom)])


def main() -> int:
    parser = argparse.ArgumentParser(description="Backtest skor komposit.")
    parser.add_argument("--days", type=int, default=730, help="jumlah hari skor yang diuji")
    parser.add_argument("--out-dir", default=os.path.join("output", "backtest"))
    parser.add_argument("--fee", type=float, default=FEE, help="biaya per transaksi (0.001 = 0,1%%)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.days > MAX_DAYS:
        log.error("--days maksimal %d (batas %d candle per permintaan Binance)",
                  MAX_DAYS, bf.BINANCE_MAX_LIMIT)
        return 1

    try:
        ohlcv = ft.fetch_ohlcv(days=args.days + WINDOW - 1)
        fng = bf.fetch_fng_history(limit=args.days + WINDOW + 60)
    except Exception as exc:  # noqa: BLE001
        log.error("FATAL: %s", exc)
        return 1

    log.info("Menghitung skor v1 dan v2 untuk %d hari...", args.days)
    mulai, records = build_records(ohlcv, fng, args.days)
    dates = [c["date"] for c in ohlcv[mulai:]]
    closes = [c["close"] for c in ohlcv[mulai:]]
    report, curves = evaluate(dates, closes, records, args.fee)

    os.makedirs(args.out_dir, exist_ok=True)
    with open(os.path.join(args.out_dir, "report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
    with open(os.path.join(args.out_dir, "REPORT.md"), "w", encoding="utf-8") as fh:
        fh.write(render_markdown(report))
    write_equity_csv(os.path.join(args.out_dir, "equity.csv"), dates, curves)

    for k in ("1", "2"):
        ic = report["versions"][k]["ic"]["7"]
        s = report["versions"][k]["strategies"]["bullish"]
        log.info("v%s: IC 7 hari %s (t %s) | long saat bullish total %.1f%%, max DD %.1f%%",
                 k, "n/a" if ic["ic"] is None else f"{ic['ic']:+.3f}",
                 "n/a" if ic["t"] is None else f"{ic['t']:.2f}",
                 s["total_return"] * 100, s["max_drawdown"] * 100)
    bh = report["buy_hold"]
    log.info("buy & hold: total %.1f%%, max DD %.1f%%",
             bh["total_return"] * 100, bh["max_drawdown"] * 100)
    log.info("Laporan: %s", os.path.join(args.out_dir, "REPORT.md"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
