"""Indikator teknikal, pure Python tanpa pandas/numpy.

Semua fungsi menerima list[float] urut lama -> baru, dan mengembalikan list
dengan panjang yang SAMA, memakai None untuk periode warmup di depan.
Konvensi ini bikin indeks -1 selalu berarti "hari terakhir" di semua seri.
"""

from __future__ import annotations

import math

Series = list[float | None]


def sma(values: list[float], period: int) -> Series:
    """Simple moving average."""
    out: Series = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    window = sum(values[:period])
    out[period - 1] = window / period
    for i in range(period, len(values)):
        window += values[i] - values[i - period]
        out[i] = window / period
    return out


def ema(values: list[float], period: int) -> Series:
    """Exponential moving average, di-seed dengan SMA periode pertama."""
    out: Series = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    k = 2 / (period + 1)
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi(values: list[float], period: int = 14) -> Series:
    """RSI dengan smoothing Wilder (sesuai definisi aslinya)."""
    out: Series = [None] * len(values)
    if len(values) <= period:
        return out

    gains = [max(values[i] - values[i - 1], 0.0) for i in range(1, len(values))]
    losses = [max(values[i - 1] - values[i], 0.0) for i in range(1, len(values))]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    out[period] = _rsi_from_avg(avg_gain, avg_loss)

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        out[i + 1] = _rsi_from_avg(avg_gain, avg_loss)
    return out


def _rsi_from_avg(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def macd(
    values: list[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[Series, Series, Series]:
    """Return (macd_line, signal_line, histogram)."""
    ema_fast = ema(values, fast)
    ema_slow = ema(values, slow)
    line: Series = [
        (f - s) if (f is not None and s is not None) else None
        for f, s in zip(ema_fast, ema_slow)
    ]

    # Signal line = EMA dari macd line, dihitung hanya pada bagian non-None
    valid = [v for v in line if v is not None]
    offset = len(line) - len(valid)
    sig_valid = ema(valid, signal)
    sig: Series = [None] * offset + list(sig_valid)

    hist: Series = [
        (m - s) if (m is not None and s is not None) else None
        for m, s in zip(line, sig)
    ]
    return line, sig, hist


def bollinger(
    values: list[float], period: int = 20, num_std: float = 2.0
) -> tuple[Series, Series, Series]:
    """Return (upper, middle, lower). Pakai standar deviasi populasi."""
    mid = sma(values, period)
    upper: Series = [None] * len(values)
    lower: Series = [None] * len(values)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1 : i + 1]
        mean = mid[i]
        assert mean is not None
        var = sum((v - mean) ** 2 for v in window) / period
        sd = math.sqrt(var)
        upper[i] = mean + num_std * sd
        lower[i] = mean - num_std * sd
    return upper, mid, lower


def percent_b(close: float, upper: float | None, lower: float | None) -> float | None:
    """Posisi harga dalam band: 0 = lower band, 1 = upper band."""
    if upper is None or lower is None or upper == lower:
        return None
    return (close - lower) / (upper - lower)


def crossed_up(fast: Series, slow: Series, index: int) -> bool:
    """True kalau fast memotong slow ke atas TEPAT di index ini."""
    if index < 1:
        return False
    a, b = fast[index - 1], slow[index - 1]
    c, d = fast[index], slow[index]
    if None in (a, b, c, d):
        return False
    return a <= b and c > d


def crossed_down(fast: Series, slow: Series, index: int) -> bool:
    """True kalau fast memotong slow ke bawah TEPAT di index ini."""
    if index < 1:
        return False
    a, b = fast[index - 1], slow[index - 1]
    c, d = fast[index], slow[index]
    if None in (a, b, c, d):
        return False
    return a >= b and c < d


def crossed_up_within(fast: Series, slow: Series, days: int) -> int | None:
    """Berapa hari lalu fast memotong slow ke atas (0 = hari ini), atau None."""
    for back in range(days + 1):
        idx = len(fast) - 1 - back
        if idx >= 1 and crossed_up(fast, slow, idx):
            return back
    return None


def crossed_down_within(fast: Series, slow: Series, days: int) -> int | None:
    for back in range(days + 1):
        idx = len(fast) - 1 - back
        if idx >= 1 and crossed_down(fast, slow, idx):
            return back
    return None


# ── Indikator berbasis rentang harga (butuh high/low, bukan cuma close) ──

def true_range(highs: list[float], lows: list[float], closes: list[float]) -> list[float]:
    """True Range: yang terbesar dari high-low dan jarak ke close kemarin.

    Indeks 0 tidak punya close kemarin, jadi nilainya high-low saja.
    """
    out: list[float] = []
    for i in range(len(closes)):
        if i == 0:
            out.append(highs[0] - lows[0])
            continue
        prev_close = closes[i - 1]
        out.append(max(
            highs[i] - lows[i],
            abs(highs[i] - prev_close),
            abs(lows[i] - prev_close),
        ))
    return out


def wilder(values: list[float], period: int, start: int = 0) -> Series:
    """Rata-rata bergerak Wilder (RMA), smoothing yang dipakai ATR dan ADX.

    Nilai pertama = rata-rata sederhana `period` nilai mulai indeks `start`,
    setelahnya prev + (x - prev) / period.
    """
    out: Series = [None] * len(values)
    first = start + period - 1
    if period <= 0 or start < 0 or first >= len(values):
        return out
    prev = sum(values[start:first + 1]) / period
    out[first] = prev
    for i in range(first + 1, len(values)):
        prev = prev + (values[i] - prev) / period
        out[i] = prev
    return out


def atr(
    highs: list[float], lows: list[float], closes: list[float], period: int = 14
) -> Series:
    """Average True Range (Wilder). Valid mulai indeks `period`.

    TR indeks 0 dilewati karena bukan true range sejati.
    """
    return wilder(true_range(highs, lows, closes), period, start=1)


def adx(
    highs: list[float], lows: list[float], closes: list[float], period: int = 14
) -> tuple[Series, Series, Series]:
    """Return (adx, plus_di, minus_di).

    ADX mengukur KEKUATAN tren, bukan arahnya: mendekati 0 berarti pasar
    tanpa tren, makin tinggi makin kuat - naik maupun turun. Arahnya dibaca
    dari +DI vs -DI. DI valid mulai indeks `period`, ADX mulai `2*period - 1`.
    """
    n = len(closes)
    if period <= 0 or n < 2 * period:
        return [None] * n, [None] * n, [None] * n

    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    for i in range(1, n):
        naik = highs[i] - highs[i - 1]
        turun = lows[i - 1] - lows[i]
        if naik > turun and naik > 0:
            plus_dm[i] = naik
        if turun > naik and turun > 0:
            minus_dm[i] = turun

    # Smoothing RMA untuk TR, +DM, dan -DM. Rasio DM/TR identik dengan versi
    # jumlah-berjalan asli Wilder, karena keduanya diskalakan faktor yang sama.
    tr_s = wilder(true_range(highs, lows, closes), period, start=1)
    plus_s = wilder(plus_dm, period, start=1)
    minus_s = wilder(minus_dm, period, start=1)

    plus_di: Series = [None] * n
    minus_di: Series = [None] * n
    dx = [0.0] * n
    for i in range(period, n):
        tr_v, p_v, m_v = tr_s[i], plus_s[i], minus_s[i]
        if tr_v is None or p_v is None or m_v is None:
            continue
        if tr_v == 0:
            plus_di[i] = minus_di[i] = 0.0
            continue
        plus_di[i] = 100 * p_v / tr_v
        minus_di[i] = 100 * m_v / tr_v
        total = plus_di[i] + minus_di[i]
        dx[i] = 0.0 if total == 0 else 100 * abs(plus_di[i] - minus_di[i]) / total

    return wilder(dx, period, start=period), plus_di, minus_di


def obv(closes: list[float], volumes: list[float]) -> list[float]:
    """On-Balance Volume: volume ditambah saat close naik, dikurangi saat turun.

    Level absolutnya bergantung pada titik awal jendela data, jadi yang
    bermakna hanya perbandingan dengan dirinya sendiri (misal terhadap
    rata-ratanya) - pergeseran konstan membatalkan diri di perbandingan itu.
    """
    out = [0.0] * len(closes)
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            out[i] = out[i - 1] + volumes[i]
        elif closes[i] < closes[i - 1]:
            out[i] = out[i - 1] - volumes[i]
        else:
            out[i] = out[i - 1]
    return out


def percentile_rank(values: list[float], x: float) -> float | None:
    """Persentil x di dalam values (0..100), mid-rank untuk nilai kembar.

    Mid-rank mencegah deret yang semuanya sama terbaca sebagai persentil 100:
    nilai yang tidak istimewa mendapat 50.
    """
    if not values:
        return None
    di_bawah = sum(1 for v in values if v < x)
    sama = sum(1 for v in values if v == x)
    return 100.0 * (di_bawah + 0.5 * sama) / len(values)
