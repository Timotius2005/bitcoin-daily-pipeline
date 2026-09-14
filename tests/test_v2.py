"""Tes indikator baru (ATR, ADX, OBV) dan aturan scoring v2.

Yang paling penting dijaga di sini: v1 tetap bisa direproduksi apa adanya,
sinyal info tidak pernah menggeser skor, dan setiap penyesuaian tercatat
lengkap sehingga skor akhir bisa diaudit dari output saja.
"""

from __future__ import annotations

import pytest

import analyze as az
import indicators as ind

ATURAN_BARU = ("adx_", "obv_", "atr_")


def bars(
    closes: list[float],
    ranges: list[float] | None = None,
    volumes: list[float] | None = None,
) -> list[dict]:
    """Candle dengan rentang high-low eksplisit, sebagai fraksi dari close."""
    rentang = ranges if ranges is not None else [0.02] * len(closes)
    vol = volumes if volumes is not None else [100.0] * len(closes)
    out = []
    for i, close in enumerate(closes):
        prev = closes[i - 1] if i else close
        setengah = close * rentang[i] / 2
        out.append({
            "date": f"hari-{i:03d}",
            "open": prev,
            "high": max(prev, close) + setengah,
            "low": min(prev, close) - setengah,
            "close": close,
            "volume": vol[i],
        })
    return out


def raw(ohlcv: list[dict], fng: dict | None = None) -> dict:
    return {"symbol": "BTCUSDT", "ohlcv": ohlcv, "fear_greed": fng,
            "global_market": None, "fetch_errors": {}}


def signals_of(ohlcv: list[dict], version: int = az.SCORING_VERSION) -> dict[str, dict]:
    ta = az.compute_indicators(ohlcv)
    return {s["id"]: s for s in az.detect_signals(ohlcv, ta, None, version)}


# ── Indikator ────────────────────────────────────────────────────────────

def test_true_range_memakai_close_kemarin():
    tr = ind.true_range([10.0, 12.0], [9.0, 11.0], [9.5, 11.5])
    assert tr[0] == pytest.approx(1.0)
    # Gap naik: jarak high ke close kemarin (12 - 9,5) melebihi high-low.
    assert tr[1] == pytest.approx(2.5)


def test_wilder_diseed_rata_rata_lalu_dihaluskan():
    out = ind.wilder([1.0, 2.0, 3.0, 4.0, 5.0, 6.0], 3, start=1)
    assert out[:3] == [None, None, None]
    assert out[3] == pytest.approx(3.0)  # rata-rata 2, 3, 4
    assert out[4] == pytest.approx(3.0 + (5.0 - 3.0) / 3)


def test_atr_pada_rentang_konstan():
    out = ind.atr([101.0] * 40, [99.0] * 40, [100.0] * 40, 14)
    assert out[13] is None
    assert out[14] == pytest.approx(2.0)
    assert out[-1] == pytest.approx(2.0)


def test_adx_tinggi_pada_tren_naik_konsisten():
    closes = [100.0 + i for i in range(60)]
    adx_s, plus_di, minus_di = ind.adx(
        [c + 0.5 for c in closes], [c - 0.5 for c in closes], closes, 14)
    assert adx_s[-1] > 25
    assert plus_di[-1] > minus_di[-1]


def test_adx_tinggi_juga_pada_tren_turun():
    # ADX mengukur kekuatan, bukan arah: turun konsisten juga tren kuat.
    closes = [200.0 - i for i in range(60)]
    adx_s, plus_di, minus_di = ind.adx(
        [c + 0.5 for c in closes], [c - 0.5 for c in closes], closes, 14)
    assert adx_s[-1] > 25
    assert minus_di[-1] > plus_di[-1]


def test_adx_rendah_pada_pasar_bolak_balik():
    closes = [100.0 if i % 2 == 0 else 101.0 for i in range(80)]
    adx_s, _, _ = ind.adx([c + 0.5 for c in closes], [c - 0.5 for c in closes], closes, 14)
    assert adx_s[-1] < 20


def test_adx_warmup_dan_data_terlalu_pendek():
    closes = [100.0 + (i % 3) for i in range(60)]
    highs, lows = [c + 1 for c in closes], [c - 1 for c in closes]
    adx_s, plus_di, _ = ind.adx(highs, lows, closes, 14)
    assert len(adx_s) == 60
    assert adx_s[26] is None and adx_s[27] is not None
    assert plus_di[13] is None and plus_di[14] is not None

    pendek = ind.adx(highs[:27], lows[:27], closes[:27], 14)
    assert all(v is None for seri in pendek for v in seri)


def test_obv_mengikuti_arah_close():
    assert ind.obv([10.0, 11.0, 11.0, 9.0], [5.0, 7.0, 3.0, 4.0]) == [0.0, 7.0, 7.0, 3.0]


def test_percentile_rank_memakai_mid_rank():
    assert ind.percentile_rank([1.0, 2.0, 3.0, 4.0], 4.0) == pytest.approx(87.5)
    assert ind.percentile_rank([5.0, 5.0, 5.0], 5.0) == pytest.approx(50.0)
    assert ind.percentile_rank([], 1.0) is None


# ── Sinyal info vs sinyal berskor ────────────────────────────────────────

def test_sinyal_info_tidak_mengaktifkan_kategori():
    sinyal = [
        {"category": "volatility", "score": 0, "kind": "info"},
        {"category": "trend", "score": 40, "kind": "score"},
    ]
    assert az.score_categories(sinyal) == {"trend": 40}


def test_skor_nol_sungguhan_tetap_dihitung():
    # Fear & Greed 50 memberi skor 0 yang sungguhan: kategorinya aktif.
    assert az.score_categories(
        [{"category": "sentiment", "score": 0, "kind": "score"}]) == {"sentiment": 0}


# ── Peredaman tren oleh ADX ──────────────────────────────────────────────

def ta_adx(nilai: float | None) -> dict:
    return {"adx14": [None, nilai]}


def test_adx_lemah_meredam_skor_tren_dan_mencatatnya():
    cats, catatan = az.apply_adjustments({"trend": 70, "momentum": -75}, ta_adx(15.0), version=2)
    assert cats == {"trend": 35, "momentum": -75}
    assert len(catatan) == 1
    assert catatan[0]["category"] == "trend"
    assert (catatan[0]["before"], catatan[0]["after"]) == (70, 35)


def test_adx_kuat_tidak_mengubah_skor():
    assert az.apply_adjustments({"trend": 70}, ta_adx(30.0), version=2) == ({"trend": 70}, [])


def test_adx_tidak_tersedia_tidak_mengubah_skor():
    assert az.apply_adjustments({"trend": 70}, ta_adx(None), version=2) == ({"trend": 70}, [])


def test_v1_tidak_pernah_diredam():
    assert az.apply_adjustments({"trend": 70}, ta_adx(10.0), version=1) == ({"trend": 70}, [])


def test_peredaman_tidak_mengubah_dict_masukan():
    asli = {"trend": 70}
    az.apply_adjustments(asli, ta_adx(10.0), version=2)
    assert asli == {"trend": 70}


# ── Sinyal OBV ───────────────────────────────────────────────────────────

def test_obv_konfirmasi_pada_tren_naik():
    sinyal = signals_of(bars([100.0 + i for i in range(120)]))
    assert sinyal["obv_konfirmasi_naik"]["score"] > 0


def test_obv_divergensi_bearish_saat_kenaikan_tanpa_volume():
    # Naik lewat hari naik bervolume kecil dan hari turun bervolume besar:
    # harga di atas MA20, tapi OBV terus merosot.
    closes, vols, harga = [], [], 100.0
    for i in range(120):
        if i % 2 == 0:
            harga += 3
            vols.append(1.0)
        else:
            harga -= 1
            vols.append(50.0)
        closes.append(harga)
    sinyal = signals_of(bars(closes, volumes=vols))
    assert sinyal["obv_divergensi_bearish"]["score"] < 0


def test_obv_divergensi_bullish_saat_penurunan_dengan_akumulasi():
    closes, vols, harga = [], [], 500.0
    for i in range(120):
        if i % 2 == 0:
            harga -= 3
            vols.append(1.0)
        else:
            harga += 1
            vols.append(50.0)
        closes.append(harga)
    sinyal = signals_of(bars(closes, volumes=vols))
    assert sinyal["obv_divergensi_bullish"]["score"] > 0


# ── Rezim ATR ────────────────────────────────────────────────────────────

def test_atr_ekstrem_saat_rentang_melebar():
    sinyal = signals_of(bars([100.0] * 200, ranges=[0.01] * 170 + [0.08] * 30))
    ekstrem = sinyal["atr_volatilitas_ekstrem"]
    assert ekstrem["kind"] == "info"
    assert ekstrem["score"] == 0


def test_atr_rendah_saat_rentang_menyempit():
    sinyal = signals_of(bars([100.0] * 200, ranges=[0.06] * 170 + [0.005] * 30))
    assert "atr_volatilitas_rendah" in sinyal


# ── analyze() utuh ───────────────────────────────────────────────────────

def test_v1_tidak_mengandung_aturan_baru():
    hasil = az.analyze(raw(bars([100.0 + i * 0.5 for i in range(150)])), version=1)
    assert hasil["meta"]["scoring_version"] == 1
    assert not any(s["id"].startswith(ATURAN_BARU) for s in hasil["signals"])
    assert hasil["scores"]["adjustments"] == []
    assert hasil["scores"]["composite"] == az.composite_score(
        az.score_categories(hasil["signals"]))


def test_v2_menjadi_default_dan_melaporkan_indikator_baru():
    hasil = az.analyze(raw(bars([100.0 + i * 0.5 for i in range(150)])))
    assert hasil["meta"]["scoring_version"] == az.SCORING_VERSION == 2
    for kunci in ("atr14", "atr_pct", "atr_pct_percentile", "adx14",
                  "plus_di", "minus_di", "obv_above_sma20"):
        assert kunci in hasil["indicators"]
    assert all(s["kind"] in ("score", "info") for s in hasil["signals"])


def test_skor_akhir_bisa_diaudit_dari_penyesuaiannya():
    # Pasar bolak-balik dengan rentang ABSOLUT simetris. Rentang proporsional
    # (seperti di bars()) membuat selisih high/low naik-turun berbeda ~0,01
    # karena galat floating point, dan ADX membacanya sebagai tren searah.
    closes = [100.0 if i % 2 == 0 else 101.0 for i in range(150)]
    sideways = [{"date": f"hari-{i:03d}", "open": closes[i - 1] if i else c,
                 "high": c + 0.5, "low": c - 0.5, "close": c, "volume": 100.0}
                for i, c in enumerate(closes)]
    hasil = az.analyze(raw(sideways))
    ids = {s["id"] for s in hasil["signals"]}
    cats = hasil["scores"]["by_category"]

    assert "adx_tren_lemah" in ids
    assert [a["category"] for a in hasil["scores"]["adjustments"]] == ["trend"]
    for adj in hasil["scores"]["adjustments"]:
        assert cats[adj["category"]] == adj["after"]
    assert hasil["scores"]["composite"] == az.composite_score(cats)
