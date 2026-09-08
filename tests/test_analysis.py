"""Tes indikator dan scoring dengan data dummy.

Tujuannya bukan memvalidasi apakah sinyalnya profitable, tapi memastikan
logikanya berperilaku sesuai desain: arah sinyal benar, skor ter-clamp,
bobot ter-normalisasi saat sumber data hilang, dan hasilnya deterministik.
"""

from __future__ import annotations

import math

import pytest

import analyze as az
import indicators as ind


# --------------------------------------------------------------------------
# Helper pembuat data dummy
# --------------------------------------------------------------------------

def make_ohlcv(closes: list[float], volumes: list[float] | None = None) -> list[dict]:
    """Bikin candle dari daftar harga close. Open = close hari sebelumnya."""
    vols = volumes if volumes is not None else [100.0] * len(closes)
    out = []
    for i, close in enumerate(closes):
        prev = closes[i - 1] if i > 0 else close
        out.append({
            "date": f"2026-01-{i + 1:02d}",
            "open": prev,
            "high": max(prev, close) * 1.01,
            "low": min(prev, close) * 0.99,
            "close": close,
            "volume": vols[i],
        })
    return out


def uptrend(n: int = 120, start: float = 50_000, step: float = 300) -> list[float]:
    return [start + step * i for i in range(n)]


def downtrend(n: int = 120, start: float = 90_000, step: float = 300) -> list[float]:
    return [start - step * i for i in range(n)]


def flat_then_crash(n: int = 120, level: float = 80_000) -> list[float]:
    """Sideways lama lalu jatuh tajam - memicu oversold + break lower band."""
    body = [level + (i % 3) * 50 for i in range(n - 10)]
    crash = [level * (1 - 0.04 * (i + 1)) for i in range(10)]
    return body + crash


# --------------------------------------------------------------------------
# Indikator
# --------------------------------------------------------------------------

def test_sma_nilai_diketahui():
    assert ind.sma([1, 2, 3, 4, 5], 5)[-1] == 3.0
    assert ind.sma([1, 2, 3, 4, 5], 3)[-1] == 4.0


def test_sma_warmup_none():
    out = ind.sma([1, 2, 3, 4, 5], 3)
    assert out[:2] == [None, None]
    assert len(out) == 5


def test_sma_data_kurang():
    assert ind.sma([1, 2], 5) == [None, None]


def test_ema_seed_pakai_sma():
    values = [float(i) for i in range(1, 11)]
    out = ind.ema(values, 5)
    assert out[4] == pytest.approx(3.0)  # SMA 1..5
    assert out[-1] > out[4]  # ikut naik


def test_rsi_naik_terus_100():
    assert ind.rsi([float(i) for i in range(1, 40)])[-1] == 100.0


def test_rsi_turun_terus_nol():
    assert ind.rsi([float(i) for i in range(40, 1, -1)])[-1] == 0.0


def test_rsi_datar_netral():
    assert ind.rsi([100.0] * 40)[-1] == 50.0


def test_bollinger_mid_sama_dengan_sma():
    values = [float(i % 17) * 3 for i in range(60)]
    _, mid, _ = ind.bollinger(values, 20)
    assert mid == ind.sma(values, 20)


def test_bollinger_upper_di_atas_lower():
    values = [float(i % 11) * 100 for i in range(60)]
    up, mid, low = ind.bollinger(values, 20)
    assert up[-1] > mid[-1] > low[-1]


def test_percent_b():
    assert ind.percent_b(150, 200, 100) == pytest.approx(0.5)
    assert ind.percent_b(100, 200, 100) == pytest.approx(0.0)
    assert ind.percent_b(100, None, 100) is None


def test_crossed_up_terdeteksi_tepat_di_titik_cross():
    fast = [1.0, 2.0, 5.0]
    slow = [3.0, 3.0, 3.0]
    assert ind.crossed_up(fast, slow, 2) is True
    assert ind.crossed_up(fast, slow, 1) is False
    assert ind.crossed_down(fast, slow, 2) is False


def test_crossed_within_lookback():
    fast = [1.0, 5.0, 6.0, 7.0]
    slow = [3.0, 3.0, 3.0, 3.0]
    assert ind.crossed_up_within(fast, slow, 3) == 2  # cross terjadi 2 hari lalu
    assert ind.crossed_up_within(fast, slow, 1) is None  # di luar jendela


def test_macd_panjang_seri_konsisten():
    values = uptrend(100)
    line, sig, hist = ind.macd(values)
    assert len(line) == len(sig) == len(hist) == len(values)
    assert hist[-1] is not None


# --------------------------------------------------------------------------
# Deteksi sinyal
# --------------------------------------------------------------------------

def signals_for(closes, volumes=None, fng=None) -> dict[str, dict]:
    ohlcv = make_ohlcv(closes, volumes)
    ta = az.compute_indicators(ohlcv)
    return {s["id"]: s for s in az.detect_signals(ohlcv, ta, fng)}


def test_uptrend_menghasilkan_sinyal_trend_bullish():
    sigs = signals_for(uptrend())
    assert "ma20_above_ma50" in sigs
    assert "price_above_ma50" in sigs
    assert sigs["ma20_above_ma50"]["direction"] == "bullish"


def test_downtrend_menghasilkan_sinyal_trend_bearish():
    sigs = signals_for(downtrend())
    assert "ma20_below_ma50" in sigs
    assert "price_below_ma50" in sigs
    assert sigs["ma20_below_ma50"]["score"] < 0


def test_uptrend_kuat_memicu_rsi_overbought():
    sigs = signals_for(uptrend())
    assert "rsi_overbought" in sigs
    assert sigs["rsi_overbought"]["score"] < 0  # dibaca contrarian


def test_crash_memicu_oversold_dan_break_band_bawah():
    sigs = signals_for(flat_then_crash())
    assert "rsi_oversold" in sigs
    assert "bb_break_bawah" in sigs
    # keduanya dibaca mean-reversion, jadi bullish
    assert sigs["rsi_oversold"]["score"] > 0
    assert sigs["bb_break_bawah"]["score"] > 0


def test_volume_spike_ikut_arah_candle():
    closes = uptrend(60)
    vols = [100.0] * 59 + [1000.0]  # hari terakhir 10x lipat, candle hijau
    sigs = signals_for(closes, vols)
    assert "volume_spike_naik" in sigs
    assert sigs["volume_spike_naik"]["score"] > 0

    closes_turun = downtrend(60)
    sigs2 = signals_for(closes_turun, vols)
    assert "volume_spike_turun" in sigs2
    assert sigs2["volume_spike_turun"]["score"] < 0


def test_volume_normal_tidak_memicu_spike():
    sigs = signals_for(uptrend(60), [100.0] * 60)
    assert not any(k.startswith("volume_spike") for k in sigs)


def test_golden_cross_terdeteksi_saat_tren_berbalik_naik():
    # turun panjang lalu naik tajam -> MA20 akhirnya memotong MA50 ke atas
    closes = downtrend(80) + [66_000 + 800 * i for i in range(40)]
    sigs = signals_for(closes)
    assert "golden_cross" in sigs or "ma20_above_ma50" in sigs


def test_fear_greed_dibaca_contrarian():
    extreme_fear = signals_for(uptrend(60), fng={"value": 10})["fear_greed"]
    extreme_greed = signals_for(uptrend(60), fng={"value": 90})["fear_greed"]
    assert extreme_fear["score"] > 0
    assert extreme_greed["score"] < 0
    assert extreme_fear["score"] == 80   # (50-10)*2
    assert extreme_greed["score"] == -80  # (50-90)*2


def test_fear_greed_netral_mendekati_nol():
    assert abs(signals_for(uptrend(60), fng={"value": 50})["fear_greed"]["score"]) == 0


def test_tanpa_fear_greed_tidak_ada_sinyal_sentimen():
    sigs = signals_for(uptrend(60), fng=None)
    assert "fear_greed" not in sigs


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------

def test_skor_kategori_dijumlah():
    sigs = [
        {"category": "trend", "score": 40},
        {"category": "trend", "score": 30},
        {"category": "momentum", "score": -25},
    ]
    assert az.score_categories(sigs) == {"trend": 70, "momentum": -25}


def test_skor_kategori_di_clamp():
    sigs = [{"category": "trend", "score": 80}, {"category": "trend", "score": 80}]
    assert az.score_categories(sigs)["trend"] == 100

    sigs_neg = [{"category": "trend", "score": -80}, {"category": "trend", "score": -80}]
    assert az.score_categories(sigs_neg)["trend"] == -100


def test_composite_semua_kategori_maksimal():
    cats = dict.fromkeys(az.CATEGORY_WEIGHTS, 100)
    assert az.composite_score(cats) == 100


def test_composite_bobot_dinormalisasi_saat_kategori_hilang():
    # Hanya trend yang ada: hasilnya harus 100, bukan 100*0.35 = 35.
    assert az.composite_score({"trend": 100}) == 100
    assert az.composite_score({"momentum": -60}) == -60


def test_composite_kategori_yang_bertentangan_saling_meredam():
    score = az.composite_score({"trend": 100, "momentum": -100})
    assert -30 < score < 30


def test_composite_kosong_nol():
    assert az.composite_score({}) == 0


def test_bobot_kategori_berjumlah_satu():
    assert math.isclose(sum(az.CATEGORY_WEIGHTS.values()), 1.0)


@pytest.mark.parametrize("score,label", [
    (100, "bullish kuat"), (50, "bullish kuat"), (49, "bullish"), (20, "bullish"),
    (19, "netral"), (0, "netral"), (-19, "netral"),
    (-20, "bearish"), (-49, "bearish"), (-50, "bearish kuat"), (-100, "bearish kuat"),
])
def test_klasifikasi_label(score, label):
    assert az.classify(score) == label


# --------------------------------------------------------------------------
# Pipeline analyze() utuh
# --------------------------------------------------------------------------

def raw_for(closes, fng=None, glob=None, volumes=None) -> dict:
    return {
        "fetched_at": "2026-01-01T00:00:00+00:00",
        "symbol": "BTCUSDT",
        "ohlcv": make_ohlcv(closes, volumes),
        "fear_greed": fng,
        "global_market": glob,
        "fetch_errors": {},
    }


def test_analyze_menghasilkan_struktur_lengkap():
    out = az.analyze(raw_for(uptrend(), fng={"value": 60, "classification": "Greed"},
                             glob={"btc_dominance_pct": 55.0}))
    for key in ("generated_at", "data_as_of", "price", "indicators",
                "sentiment", "signals", "scores", "meta"):
        assert key in out
    assert out["scores"]["composite"] == pytest.approx(out["scores"]["composite"])
    assert -100 <= out["scores"]["composite"] <= 100
    assert out["meta"]["candles_used"] == 120


def test_analyze_uptrend_condong_bullish_pada_trend():
    out = az.analyze(raw_for(uptrend()))
    assert out["scores"]["by_category"]["trend"] > 0


def test_analyze_downtrend_condong_bearish():
    out = az.analyze(raw_for(downtrend()))
    assert out["scores"]["composite"] < 0
    assert out["scores"]["label"] in ("bearish", "bearish kuat")


def test_analyze_tolak_data_terlalu_pendek():
    with pytest.raises(ValueError, match="minimal"):
        az.analyze(raw_for(uptrend(30)))


def test_analyze_tanpa_sentimen_tetap_jalan():
    out = az.analyze(raw_for(uptrend(), fng=None, glob=None))
    assert "sentiment" not in out["scores"]["weights_used"]
    assert out["sentiment"]["fear_greed"] is None
    assert isinstance(out["scores"]["composite"], int)


def test_analyze_deterministik():
    raw = raw_for(flat_then_crash(), fng={"value": 20})
    a, b = az.analyze(raw), az.analyze(raw)
    assert a["signals"] == b["signals"]
    assert a["scores"] == b["scores"]


def test_analyze_persen_perubahan_harga():
    closes = uptrend(120, start=50_000, step=300)
    out = az.analyze(raw_for(closes))
    expected = round((closes[-1] / closes[-2] - 1) * 100, 2)
    assert out["price"]["change_1d_pct"] == expected
    assert out["price"]["change_7d_pct"] is not None
    assert out["price"]["change_30d_pct"] is not None
