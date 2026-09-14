"""Tes mesin backtest: statistik, simulasi tanpa look-ahead, dan laporan.

Backtest yang salah tetap menghasilkan angka yang terlihat meyakinkan, jadi
bagian yang paling mudah salah diam-diam - penjajaran return, biaya, dan
sampel efektif - diuji dengan angka yang bisa dihitung tangan.
"""

from __future__ import annotations

import json
import math

import pytest

import backtest as bt


# ── Statistik ────────────────────────────────────────────────────────────

def test_forward_returns_sejajar_dengan_hari_skornya():
    closes = [100.0, 110.0, 99.0, 121.0]
    satu = bt.forward_returns(closes, 1)
    assert satu[:3] == pytest.approx([0.10, -0.10, 121 / 99 - 1])
    assert satu[3] is None

    dua = bt.forward_returns(closes, 2)
    assert dua[0] == pytest.approx(-0.01)
    assert dua[2:] == [None, None]


def test_rank_nilai_kembar_mendapat_rata_rata_posisi():
    assert bt.rank([10.0, 30.0, 20.0, 30.0]) == [1.0, 3.5, 2.0, 3.5]


def test_spearman_arah_monoton_dan_kasus_tak_terdefinisi():
    assert bt.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert bt.spearman([1, 2, 3, 4], [9, 7, 5, 1]) == pytest.approx(-1.0)
    # Monoton tapi jauh dari linear: korelasi peringkat tetap 1.
    assert bt.spearman([1, 2, 3, 4], [1, 8, 27, 1000]) == pytest.approx(1.0)
    assert bt.spearman([1, 2, 3], [5, 5, 5]) is None
    assert bt.spearman([1, 2], [3, 4]) is None


def test_t_stat_dan_batasnya():
    assert bt.t_stat(None, 50) is None
    assert bt.t_stat(0.5, 2) is None
    assert bt.t_stat(1.0, 50) is None
    assert bt.t_stat(0.3, 102) == pytest.approx(0.3 * math.sqrt(100 / 0.91))


def test_ic_horizon_panjang_memakai_sampel_efektif():
    skor = list(range(70))
    fwd = [float(i) for i in range(60)] + [None] * 10
    hasil = bt.information_coefficient(skor, fwd, 30)
    assert hasil["n"] == 60
    assert hasil["n_eff"] == 2  # 60 hari return 30-hari = cuma 2 pengamatan independen
    assert hasil["ic"] == pytest.approx(1.0)
    assert hasil["t"] is None


def test_bucket_stats_per_label_dalam_urutan_tetap():
    rows = bt.bucket_stats(["bullish", "bullish", "netral"], [0.10, -0.05, None])
    per = {r["label"]: r for r in rows}
    assert [r["label"] for r in rows] == list(bt.LABEL_ORDER)
    assert per["bullish"]["n"] == 2
    assert per["bullish"]["mean"] == pytest.approx(0.025)
    assert per["bullish"]["up_rate"] == pytest.approx(0.5)
    assert per["netral"]["n"] == 0
    assert per["netral"]["mean"] is None


# ── Simulasi strategi ────────────────────────────────────────────────────

def test_posisi_hari_ini_baru_mendapat_return_besok():
    closes = [100.0, 110.0, 99.0, 99.0]

    metrik, kurva = bt.simulate(closes, [1, 0, 0, 0], fee=0.0)
    assert kurva == pytest.approx([1.0, 1.1, 1.1, 1.1])
    assert metrik["total_return"] == pytest.approx(0.10)

    # Masuk di close hari ke-1 berarti TIDAK ikut kenaikan ke hari ke-1.
    metrik, kurva = bt.simulate(closes, [0, 1, 0, 0], fee=0.0)
    assert kurva == pytest.approx([1.0, 1.0, 0.9, 0.9])
    assert metrik["total_return"] == pytest.approx(-0.10)


def test_biaya_dipotong_setiap_posisi_berubah():
    closes = [100.0, 110.0, 99.0, 99.0]
    metrik, kurva = bt.simulate(closes, [1, 1, 0, 0], fee=0.01)
    assert metrik["trades"] == 2
    assert kurva[1] == pytest.approx(1 + 0.10 - 0.01)
    assert kurva[2] == pytest.approx(kurva[1] * (1 - 0.10))
    assert kurva[3] == pytest.approx(kurva[2] * (1 - 0.01))


def test_max_drawdown_diukur_dari_puncak_tertinggi():
    metrik, _ = bt.simulate([100.0, 120.0, 90.0, 150.0], [1, 1, 1, 1], fee=0.0)
    assert metrik["max_drawdown"] == pytest.approx(90 / 120 - 1)
    assert metrik["exposure"] == pytest.approx(1.0)


def test_panjang_posisi_harus_sama_dengan_harga():
    with pytest.raises(ValueError):
        bt.simulate([1.0, 2.0], [1], fee=0.0)


# ── Evaluasi sinyal dan aturan ───────────────────────────────────────────

def rekaman(sinyal=(), trend_raw=None, adx=None, atr=None) -> dict:
    return {"date": "d", "score": 0, "label": "netral", "cats": {},
            "trend_raw": trend_raw, "adx": adx, "atr_pctl": atr, "signals": list(sinyal)}


def sinyal(sid: str, score: int, kind: str = "score", category: str = "sentiment") -> dict:
    return {"id": sid, "score": score, "kind": kind, "category": category, "label": sid}


def test_signal_stats_memisahkan_fear_greed_per_arah_dan_melewati_info():
    records = [
        rekaman([sinyal("fear_greed", 40),
                 sinyal("atr_volatilitas_ekstrem", 0, kind="info", category="volatility")]),
        rekaman([sinyal("fear_greed", -30)]),
        rekaman([sinyal("fear_greed", 60)]),
    ]
    rows = bt.signal_stats(records, [0.05, 0.02, -0.01])
    per = {(r["id"], r["sign"]): r for r in rows}
    assert set(per) == {("fear_greed", 1), ("fear_greed", -1)}
    assert per[("fear_greed", 1)]["n"] == 2
    assert per[("fear_greed", 1)]["hit_rate"] == pytest.approx(0.5)
    assert per[("fear_greed", -1)]["hit_rate"] == pytest.approx(0.0)


def test_validasi_adx_memisahkan_kondisi_dan_melewati_data_kosong():
    records = [
        rekaman(trend_raw=40, adx=30.0), rekaman(trend_raw=40, adx=30.0),
        rekaman(trend_raw=-40, adx=12.0), rekaman(trend_raw=-40, adx=12.0),
        rekaman(trend_raw=None, adx=30.0), rekaman(trend_raw=40, adx=None),
    ]
    hasil = bt.adx_gate_check(records, [0.02, -0.01, 0.03, -0.02, 0.05, 0.05])
    assert hasil["kuat"] == {"n": 2, "hit_rate": 0.5}
    assert hasil["lemah"] == {"n": 2, "hit_rate": 0.5}


# ── Jalur utuh ───────────────────────────────────────────────────────────

def synthetic_ohlcv(n: int = 260) -> list[dict]:
    def harga(i: int) -> float:
        return 100 + 10 * math.sin(i / 7) + i * 0.15

    out = []
    for i in range(n):
        close, prev = harga(i), harga(i - 1) if i else harga(0)
        out.append({
            "date": f"hari-{i:04d}", "open": prev,
            "high": max(prev, close) * 1.01, "low": min(prev, close) * 0.99,
            "close": close, "volume": 100.0 + (i % 5) * 20,
        })
    return out


def test_backtest_utuh_pada_data_sintetis():
    ohlcv = synthetic_ohlcv()
    fng = {c["date"]: {"value": 30 + (i % 40), "classification": "x"}
           for i, c in enumerate(ohlcv)}

    mulai, records = bt.build_records(ohlcv, fng, days=45)
    assert mulai == len(ohlcv) - 45
    assert set(records) == {1, 2}
    assert len(records[1]) == len(records[2]) == 45

    dates = [c["date"] for c in ohlcv[mulai:]]
    closes = [c["close"] for c in ohlcv[mulai:]]
    report, curves = bt.evaluate(dates, closes, records, fee=0.001)

    assert set(report["versions"]) == {"1", "2"}
    assert "adx_gate" in report["versions"]["2"]
    assert "adx_gate" not in report["versions"]["1"]
    assert all(len(kurva) == len(closes) for kurva in curves.values())
    json.dumps(report)  # harus bisa diserialisasi apa adanya

    teks = bt.render_markdown(report)
    assert teks.startswith("# Backtest skor komposit")
    assert "## 6. Strategi sederhana" in teks
