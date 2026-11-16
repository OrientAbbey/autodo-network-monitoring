"""DÉTECTEURS STATISTIQUES « FUTURS » — fonctions PURES (aucun accès base ni réseau), faciles à tester.

* D1 ``seasonal_finding``    : le trafic est-il anormalement bas POUR CE CRÉNEAU HORAIRE ? (médiane/MAD des 7 jours précédents)
* D3 ``holt_winters_forecast`` : courbe « attendue » sur 24 h (Holt-Winters additif, saison = 1 jour) ;
     ``capacity_projection``  : tendance des pics quotidiens → nombre de jours avant saturation d'un lien.
* D5 ``level_shifts``        : changements durables du niveau moyen (CUSUM sur les moyennes quotidiennes).

Ces détecteurs n'ouvrent JAMAIS de ticket : ils alimentent le mode ombre (``shadow``), les analyses de la page Admin
et la simulation. Chaque verdict porte une explication lisible en français.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta

POINTS_PER_DAY = 288
STEP = timedelta(minutes=5)


@dataclass(frozen=True)
class Finding:
    """Verdict d'un détecteur sur un relevé."""
    detector: str
    alert: bool
    explanation: str = ""
    score: float | None = None


def fr(value: float, digits: int = 1) -> str:
    """Nombre à la française : virgule décimale et vrai signe moins (« −23,0 »)."""
    return f"{value:.{digits}f}".replace(".", ",").replace("-", "−")


# ============================================================================
# D1 — référence saisonnière
# ============================================================================
SEASONAL_DAYS = 7              # jours précédents comparés (même créneau)
SEASONAL_MIN_DAYS = 3          # en dessous : pas assez de référence, aucun verdict
NOISE_FLOOR = 0.03             # dispersion minimale = 3 % du niveau (évite qu'un créneau très régulier devienne hypersensible)
Z_THRESHOLD = -3.5             # écart robuste à partir duquel un relevé est anormalement bas
CONSECUTIVE_POINTS = 2         # relevés consécutifs sous le seuil exigés (filtre les aberrations isolées)
MIN_EXPECTED_GBPS = 0.1        # sous ce niveau attendu, une « chute » n'a pas de sens opérationnel


def seasonal_reference(history: dict[datetime, float], at: datetime) -> tuple[float, float, int] | None:
    """(médiane, dispersion robuste, nombre de jours) du créneau ``at`` sur les jours précédents, ou None si trop peu de jours."""
    values = [history[at - timedelta(days=d)] for d in range(1, SEASONAL_DAYS + 1)
              if history.get(at - timedelta(days=d)) is not None]
    if len(values) < SEASONAL_MIN_DAYS:
        return None
    median = statistics.median(values)
    mad = statistics.median(abs(v - median) for v in values) * 1.4826        # 1,4826 : MAD → équivalent d'un écart-type
    return median, max(mad, NOISE_FLOOR * median), len(values)


def seasonal_finding(history: dict[datetime, float], at: datetime) -> Finding:
    """Alerte si les ``CONSECUTIVE_POINTS`` derniers relevés sont tous anormalement bas pour leur créneau.

    ``history`` : ``{instant UTC: trafic Gb/s}`` couvrant les relevés ``at``, ``at-5 min`` et leurs créneaux des 7 jours précédents.
    """
    checks = []
    for offset in range(CONSECUTIVE_POINTS):
        slot = at - offset * STEP
        value = history.get(slot)
        reference = seasonal_reference(history, slot)
        if value is None or reference is None:
            return Finding("seasonal", False, "Historique insuffisant pour ce créneau")
        median, scale, days = reference
        if median < MIN_EXPECTED_GBPS:
            return Finding("seasonal", False, "Niveau attendu trop faible pour juger")
        checks.append(((value - median) / scale, value, median, days))
    alert = all(z < Z_THRESHOLD for z, *_ in checks)
    z, value, median, days = checks[0]
    change = (value - median) / median * 100
    explanation = (f"{fr(change, 0)} % par rapport à l'attendu {fr(median)} Gb/s "
                   f"(même créneau, {days} derniers jours) ; z = {fr(z)}")
    return Finding("seasonal", alert, explanation if alert else "Dans la norme pour ce créneau", round(z, 2))


# ============================================================================
# D3 — prévision Holt-Winters additive
# ============================================================================
def _fill_gaps(values: list[float | None], season: int) -> list[float]:
    """Remplace les valeurs absentes par celle du même créneau la veille (sinon la dernière valeur connue)."""
    filled: list[float] = []
    last_known = next((v for v in values if v is not None), 0.0)
    for index, value in enumerate(values):
        if value is None:
            value = filled[index - season] if index >= season else last_known
        filled.append(value)
        last_known = value
    return filled


def holt_winters_forecast(values: list[float | None], season: int = POINTS_PER_DAY, horizon: int = POINTS_PER_DAY,
                          alpha: float = 0.2, beta: float = 0.005, gamma: float = 0.3) -> dict:
    """Prévision des ``horizon`` prochains points. Retourne ``{"expected", "lower", "upper", "residual_std"}``.

    Modèle additif : niveau + tendance + profil saisonnier (une journée = 288 points de 5 min). Il faut au moins DEUX saisons.
    La bande « lower/upper » est ±1,96 écart-type des erreurs de prévision à un pas (≈ 95 %).
    """
    present = sum(v is not None for v in values)
    if len(values) < 2 * season or present < 1.5 * season:
        # (une prévision bâtie sur des trous « comblés » ne vaut rien : on exige des données réellement mesurées)
        raise ValueError("Il faut au moins 2 jours d'historique (dont 1,5 jour de relevés présents) pour prévoir une journée.")
    series = _fill_gaps(values, season)
    first, second = series[:season], series[season:2 * season]
    level = statistics.fmean(first)
    trend = (statistics.fmean(second) - level) / season
    seasonal = [x - level for x in first]
    errors: list[float] = []
    for t in range(season, len(series)):
        seasonal_part = seasonal[t % season]
        errors.append(series[t] - (level + trend + seasonal_part))          # erreur de prévision à un pas, avant mise à jour
        previous_level = level
        level = alpha * (series[t] - seasonal_part) + (1 - alpha) * (level + trend)
        trend = beta * (level - previous_level) + (1 - beta) * trend
        seasonal[t % season] = gamma * (series[t] - level) + (1 - gamma) * seasonal_part
    residual_std = statistics.pstdev(errors) if len(errors) > 1 else 0.0
    count = len(series)
    expected = [max(0.0, level + h * trend + seasonal[(count + h - 1) % season]) for h in range(1, horizon + 1)]
    margin = 1.96 * residual_std
    return {"expected": [round(v, 3) for v in expected],
            "lower": [round(max(0.0, v - margin), 3) for v in expected],
            "upper": [round(v + margin, 3) for v in expected], "residual_std": round(residual_std, 4)}


# ============================================================================
# D3 — planification de capacité
# ============================================================================
CAPACITY_MIN_DAYS = 7


def capacity_projection(daily_peaks: list[float], capacity_gbps: float) -> dict:
    """Régression linéaire des pics quotidiens → jours restants avant saturation (None = pas de saturation prévisible).

    ``daily_peaks`` : un pic par jour complet, du plus ancien au plus récent.
    """
    n = len(daily_peaks)
    if n < CAPACITY_MIN_DAYS:
        return {"status": "insufficient_data", "days_of_data": n}
    xs = list(range(n))
    mean_x, mean_y = statistics.fmean(xs), statistics.fmean(daily_peaks)
    sxx = sum((x - mean_x) ** 2 for x in xs)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, daily_peaks, strict=True)) / sxx
    intercept = mean_y - slope * mean_x
    ss_total = sum((y - mean_y) ** 2 for y in daily_peaks)
    ss_residual = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, daily_peaks, strict=True))
    r_squared = 1 - ss_residual / ss_total if ss_total > 0 else 0.0
    current = intercept + slope * (n - 1)
    if current >= capacity_gbps:
        status, days_left = "saturated", 0.0
    elif slope <= 1e-6:
        status, days_left = "stable", None
    else:
        status, days_left = "growing", (capacity_gbps - current) / slope
    return {"status": status, "days_of_data": n, "slope_gbps_per_day": round(slope, 4), "current_peak_gbps": round(current, 3),
            "capacity_gbps": capacity_gbps, "days_to_saturation": None if days_left is None else round(days_left, 1),
            "r_squared": round(r_squared, 3)}


# ============================================================================
# D5 — ruptures de niveau (CUSUM)
# ============================================================================
CUSUM_REFERENCE_DAYS = 14
CUSUM_THRESHOLD = 5.0          # somme cumulée (en écarts-types) au-delà de laquelle on déclare une rupture
CUSUM_DRIFT = 1.0              # tolérance par jour (en écarts-types) : les écarts plus petits ne s'accumulent pas
CUSUM_NOISE_FLOOR = 0.03       # dispersion minimale = 3 % du niveau (des réglages plus sensibles donnaient de fausses alertes sur du bruit)


def level_shifts(daily_means: list[tuple[object, float]], reference_days: int = CUSUM_REFERENCE_DAYS,
                 threshold: float = CUSUM_THRESHOLD, drift: float = CUSUM_DRIFT) -> list[dict]:
    """Ruptures de niveau dans une série de moyennes quotidiennes ``[(jour, moyenne)]``.

    La référence est la moyenne des ``reference_days`` premiers jours ; après une rupture, une nouvelle référence est prise
    à partir du jour détecté. Retourne ``[{"day", "direction", "before", "after", "change_percent"}]``.
    """
    shifts: list[dict] = []
    start = 0
    while start + reference_days < len(daily_means):
        reference = [m for _, m in daily_means[start:start + reference_days]]
        level = statistics.fmean(reference)
        scale = max(statistics.pstdev(reference), CUSUM_NOISE_FLOOR * level, 1e-9)
        upward = downward = 0.0
        detected = None
        for index in range(start + reference_days, len(daily_means)):
            z = (daily_means[index][1] - level) / scale
            upward = max(0.0, upward + z - drift)
            downward = max(0.0, downward - z - drift)
            if upward > threshold or downward > threshold:
                detected = (index, "up" if upward > threshold else "down")
                break
        if detected is None:
            break
        index, direction = detected
        after_values = [m for _, m in daily_means[index:index + 7]]
        after = statistics.fmean(after_values)
        shifts.append({"day": str(daily_means[index][0]), "direction": direction, "before": round(level, 3), "after": round(after, 3),
                       "change_percent": round((after - level) / level * 100, 1) if level else None})
        start = index
    return shifts


def shift_explanation(shift: dict) -> str:
    sens = "hausse" if shift["direction"] == "up" else "baisse"
    return (f"Rupture de niveau ({sens}) : moyenne quotidienne {fr(shift['before'])} → {fr(shift['after'])} Gb/s "
            f"({fr(shift['change_percent'] or 0, 0)} %)")
