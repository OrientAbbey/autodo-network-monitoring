"""Détecteurs statistiques purs (D1 saisonnier, D3 Holt-Winters + capacité, D5 CUSUM) — sans base de données."""
import math
import random
from datetime import datetime

import pytest

from core import anomaly, rules
from core.config import ConfigParams

STEP = anomaly.STEP
PER_DAY = anomaly.POINTS_PER_DAY
START = datetime(2026, 1, 1, 0, 0)
CFG = ConfigParams().model_dump()


def daily_profile(index: int, base: float = 18.0) -> float:
    """Trafic théorique (Gb/s) : cycle diurne entre ≈ 11 et 18 Gb/s."""
    hour = (index % PER_DAY) / 12.0
    return base * (0.62 + 0.38 * math.sin((hour - 14) / 24 * 2 * math.pi))


def make_history(days: int, noise: float = 0.03, seed: int = 1) -> dict[datetime, float]:
    rnd = random.Random(seed)
    return {START + i * STEP: daily_profile(i) * (1 + rnd.gauss(0, noise)) for i in range(days * PER_DAY)}


def last_instant(history: dict) -> datetime:
    return max(history)


# ------------------------------------------------------------------ D1 : référence saisonnière
def test_a_normal_day_raises_no_seasonal_alert():
    history = make_history(9)
    alerts = [anomaly.seasonal_finding(history, START + i * STEP).alert for i in range(8 * PER_DAY, 9 * PER_DAY)]
    assert sum(alerts) <= 1                                                              # bruit seul : quasi aucune alerte


def test_seasonal_detects_a_20_percent_drop_that_the_global_threshold_misses():
    history = make_history(9)
    end = last_instant(history)
    for k in range(2):                                                                   # −20 % sur les deux derniers relevés
        history[end - k * STEP] *= 0.8
    finding = anomaly.seasonal_finding(history, end)
    assert finding.alert and finding.score < anomaly.Z_THRESHOLD
    percent = int(finding.explanation.split(" %")[0].replace("−", "-"))
    assert -25 <= percent <= -15                                                         # ≈ −20 % (le bruit déplace un peu la médiane)
    assert "Gb/s" in finding.explanation and "z = −" in finding.explanation              # explication lisible, virgule décimale
    # même série vue par les règles actuelles (fenêtre de 6 valeurs) : le seuil global de 40 % ne bouge pas
    window = [history[end - 5 * STEP]] * 4 + [history[end - STEP], history[end]]
    smooth = [window[0]] * 4 + [window[0] * 0.8] * 2
    assert rules.detect_traffic(smooth, CFG).alert is False


def test_a_single_aberrant_point_is_not_enough():
    history = make_history(9)
    end = last_instant(history)
    history[end] *= 0.5
    assert anomaly.seasonal_finding(history, end).alert is False


def test_zero_traffic_is_flagged_by_the_seasonal_reference_too():
    history = make_history(9)
    end = last_instant(history)
    history[end] = history[end - STEP] = 0.0
    assert anomaly.seasonal_finding(history, end).alert is True


def test_not_enough_history_gives_no_verdict_rather_than_a_false_alarm():
    history = make_history(2)                                                            # 1 seul jour de référence < 3
    end = last_instant(history)
    history[end] = history[end - STEP] = 0.0
    finding = anomaly.seasonal_finding(history, end)
    assert finding.alert is False and "insuffisant" in finding.explanation


def test_a_very_low_expected_level_is_ignored():
    history = {START + i * STEP: 0.02 for i in range(9 * PER_DAY)}
    end = last_instant(history)
    history[end] = history[end - STEP] = 0.0
    assert anomaly.seasonal_finding(history, end).alert is False


def test_a_perfectly_regular_slot_does_not_become_hypersensitive():
    flat = {START + i * STEP: 10.0 for i in range(9 * PER_DAY)}                          # dispersion nulle → plancher de 3 %
    end = last_instant(flat)
    flat[end] = flat[end - STEP] = 9.8                                                   # −2 % seulement
    assert anomaly.seasonal_finding(flat, end).alert is False


# ------------------------------------------------------------------ D3 : Holt-Winters
def test_holt_winters_forecasts_the_next_day_within_a_few_percent():
    history = make_history(6, noise=0.03, seed=3)
    training = [history[START + i * STEP] for i in range(5 * PER_DAY)]
    truth = [history[START + (5 * PER_DAY + i) * STEP] for i in range(PER_DAY)]
    forecast = anomaly.holt_winters_forecast(training)
    errors = [abs(f - t) / t for f, t in zip(forecast["expected"], truth, strict=True)]
    assert sum(errors) / len(errors) < 0.06
    inside = sum(lo <= t <= hi for lo, hi, t in zip(forecast["lower"], forecast["upper"], truth, strict=True)) / PER_DAY
    assert inside > 0.85                                                                 # la bande couvre l'essentiel du réel
    assert len(forecast["expected"]) == PER_DAY and forecast["residual_std"] > 0


def test_holt_winters_needs_two_days_and_survives_gaps():
    with pytest.raises(ValueError, match="2 jours"):
        anomaly.holt_winters_forecast([1.0] * (PER_DAY + 10))
    series = [daily_profile(i) for i in range(4 * PER_DAY)]
    for i in range(300, 340):
        series[i] = None                                                                 # panne de collecte de ≈ 3 h
    forecast = anomaly.holt_winters_forecast(series)
    assert all(v is not None and v >= 0 for v in forecast["expected"])


# ------------------------------------------------------------------ D3 : capacité
def test_capacity_projection_on_a_growing_link():
    peaks = [10 + 0.05 * day for day in range(60)]                                       # +0,05 Gb/s par jour, capacité 20 Gb/s
    result = anomaly.capacity_projection(peaks, 20.0)
    assert result["status"] == "growing" and result["slope_gbps_per_day"] == pytest.approx(0.05, abs=1e-3)
    assert result["days_to_saturation"] == pytest.approx((20 - peaks[-1]) / 0.05, abs=0.5)
    assert result["r_squared"] > 0.99


def test_capacity_projection_other_statuses():
    assert anomaly.capacity_projection([10.0] * 30, 20.0)["status"] == "stable"
    assert anomaly.capacity_projection([20 - 0.1 * d for d in range(30)], 20.0)["status"] == "stable"       # en baisse
    assert anomaly.capacity_projection([15 + d for d in range(30)], 20.0)["status"] == "saturated"
    assert anomaly.capacity_projection([10, 11, 12], 20.0) == {"status": "insufficient_data", "days_of_data": 3}


# ------------------------------------------------------------------ D5 : CUSUM
def daily_series(levels: list[tuple[int, float]], noise: float, seed: int = 5) -> list[tuple[int, float]]:
    """``levels`` = [(nombre de jours, niveau)] mis bout à bout, avec un bruit multiplicatif."""
    rnd = random.Random(seed)
    out, day = [], 0
    for length, level in levels:
        for _ in range(length):
            out.append((day, level * (1 + rnd.gauss(0, noise))))
            day += 1
    return out


def test_cusum_raises_no_false_alarm_on_pure_noise():
    for seed in range(20):                                                               # 20 séries de 90 jours, bruit de 2 %
        assert anomaly.level_shifts(daily_series([(90, 15.0)], 0.02, seed)) == [], seed


def test_cusum_detects_a_durable_drop_within_a_few_days():
    shifts = anomaly.level_shifts(daily_series([(30, 15.0), (30, 12.75)], 0.015))        # −15 % au jour 30
    assert len(shifts) == 1 and shifts[0]["direction"] == "down"
    assert 30 <= int(shifts[0]["day"]) <= 33
    assert shifts[0]["change_percent"] == pytest.approx(-15, abs=3)
    assert "baisse" in anomaly.shift_explanation(shifts[0])


def test_cusum_detects_an_increase_and_successive_shifts():
    shifts = anomaly.level_shifts(daily_series([(30, 10.0), (30, 13.0), (30, 9.0)], 0.01))
    assert [s["direction"] for s in shifts] == ["up", "down"]


def test_cusum_needs_a_reference_period():
    assert anomaly.level_shifts(daily_series([(10, 15.0)], 0.01)) == []


def test_french_number_format():
    assert anomaly.fr(-4.123) == "−4,1" and anomaly.fr(14.06) == "14,1" and anomaly.fr(-23.4, 0) == "−23"
