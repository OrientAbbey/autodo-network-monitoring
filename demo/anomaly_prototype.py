#!/usr/bin/env python3
"""Prototype hors-ligne : règles actuelles vs référence saisonnière robuste vs Isolation Forest.

Données : séries SIMULÉES (cycle diurne + bruit + quelques aberrations) avec anomalies injectées de 5 types.
⚠ Résultats indicatifs : les vraies séries sont plus bruitées. À rejouer sur l'historique réel avant toute décision.
Usage : python demo/anomaly_prototype.py   (nécessite scikit-learn ; dépendance de R&D, non requise par l'application)
"""
from __future__ import annotations

import math
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import rules  # noqa: E402
from core.config import ConfigParams  # noqa: E402

CFG = ConfigParams().model_dump()
STEP = 288                                  # points par jour (5 min)
DAYS, TRAIN_DAYS = 35, 21
rnd = random.Random(7)


def base_series(days: int) -> list[float]:
    out = []
    for i in range(days * STEP):
        h = (i % STEP) / 12.0
        v = 18 * (0.62 + 0.38 * math.sin((h - 14) / 24 * 2 * math.pi)) * (0.93 if (i // STEP) % 7 >= 5 else 1.0)
        v *= 1 + rnd.gauss(0, 0.05)                              # bruit multiplicatif 5 %
        if rnd.random() < 0.004:                                 # aberrations de collecte
            v *= rnd.choice([0.6, 1.5])
        out.append(max(v, 0.0))
    return out


def inject(series: list[float], kind: str, start: int, length: int = 12) -> None:
    for k in range(length):
        i = start + k
        if kind == "chute_brutale":   series[i] *= 0.35
        elif kind == "chute_20pct":    series[i] *= 0.80
        elif kind == "derive_lente":   series[i] *= max(0.55, 1 - 0.04 * (k + 1))
        elif kind == "plateau_pic":    series[i] = min(series[i], 0.8 * series[start - 1])  # écrêtage à l'heure de pointe
        elif kind == "zero":           series[i] = 0.0


KINDS = ["chute_brutale", "chute_20pct", "derive_lente", "plateau_pic", "zero"]


def expected_profile(hist: list[float], idx: int) -> tuple[float, float]:
    """Médiane et MAD du même créneau horaire sur les jours précédents (7 jours maximum)."""
    vals = [hist[idx - d * STEP] for d in range(1, 8) if idx - d * STEP >= 0]
    med = statistics.median(vals)
    mad = statistics.median(abs(v - med) for v in vals) * 1.4826 or 1e-6
    return med, mad


def run_rules(series, t):                       # une évaluation par cycle, fenêtre de 6 valeurs comme en production
    return rules.detect_traffic(series[t - 5:t + 1], {**CFG, "zero_traffic_min_samples": 1}).alert


def run_seasonal(series, t):
    flags = 0
    for k in range(2):                          # 2 points consécutifs sous le seuil robuste
        med, mad = expected_profile(series, t - k)
        flags += ((series[t - k] - med) / max(mad, 0.03 * med)) < -3.5
    return flags == 2


def features(series, t):
    med, _ = expected_profile(series, t)
    r = [series[t - k] / max(expected_profile(series, t - k)[0], 1e-6) for k in range(3)]
    return [r[0], r[1], r[2], r[0] - r[1], sum(r) / 3]


def evaluate(detect, series, events, clean_days):
    res = {}
    for kind in KINDS:
        hits, delays = 0, []
        evs = [e for e in events if e[0] == kind]
        for _, s in evs:
            first = next((t for t in range(s, s + 24) if detect(series, t)), None)
            if first is not None:
                hits += 1
                delays.append(first - s)
        res[kind] = (hits, len(evs), statistics.mean(delays) if delays else float("nan"))
    fp = 0                                      # épisodes d'alerte sur des jours sans anomalie
    prev = False
    for t in clean_days:
        cur = detect(series, t)
        fp += cur and not prev
        prev = cur
    return res, fp / (len(clean_days) / STEP)


def main():
    series = base_series(DAYS)
    ref = list(series)                          # copie propre pour l'entraînement / les faux positifs
    events, taken = [], []
    for kind in KINDS:
        for _ in range(10):
            for _try in range(100):
                s = rnd.randrange((TRAIN_DAYS + 1) * STEP, DAYS * STEP - 40)
                if kind == "plateau_pic":
                    s = rnd.randrange(TRAIN_DAYS, DAYS) * STEP + 12 * 18      # autour de 18 h
                if all(abs(s - o) > 40 for o in taken):
                    taken.append(s); events.append((kind, s)); inject(series, kind, s); break
    clean_days = [t for t in range(TRAIN_DAYS * STEP + 10, DAYS * STEP) if all(not (o - 3 <= t <= o + 40) for o in taken)]

    try:
        from sklearn.ensemble import IsolationForest
        X = [features(ref, t) for t in range(8 * STEP, TRAIN_DAYS * STEP)]
        iso = IsolationForest(n_estimators=200, contamination=0.01, random_state=1).fit(X)
        def run_iso(s, t):
            f = features(s, t)
            return iso.predict([f])[0] == -1 and f[0] < 0.9 and f[1] < 0.9      # baisse seulement (les hausses ne nous intéressent pas ici)
    except ImportError:
        run_iso = None

    detectors = {"Règles actuelles (fenêtre 6 pts)": run_rules, "Référence saisonnière robuste (médiane/MAD)": run_seasonal}
    if run_iso:
        detectors["Isolation Forest (5 variables)"] = run_iso
    print(f"{len(events)} anomalies injectées ({len(KINDS)} types × 10), {len(clean_days) // STEP:.0f} jours « propres » pour les faux positifs\n")
    head = f"{'détecteur':46s}" + "".join(f"{k:>16s}" for k in KINDS) + f"{'faux pos./jour':>16s}"
    print(head); print("-" * len(head))
    for name, fn in detectors.items():
        res, fp = evaluate(fn, series, events, clean_days)
        cells = "".join(f"{res[k][0]:>4d}/{res[k][1]:<3d} d={res[k][2]:>4.1f}".rjust(16) for k in KINDS)
        print(f"{name:46s}{cells}{fp:>16.2f}")
    print("\nlecture : « 9/12 d=2.0 » = 9 anomalies sur 12 détectées, délai moyen de 2 pas (10 min) après le début.")


if __name__ == "__main__":
    main()
