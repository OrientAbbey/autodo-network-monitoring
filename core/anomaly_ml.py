"""DÉTECTEURS À APPRENTISSAGE (OPTIONNELS) — D4 « Isolation Forest » par base, D7 « qualité d'alerte ».

scikit-learn et joblib ne sont PAS requis par l'application (``requirements-ml.txt``). Sans eux, ``ml_available()`` est faux
et toute fonction d'entraînement lève ``MlUnavailable`` (l'API répond alors HTTP 501). Rien ici n'ouvre ni ne ferme un ticket.

D4 — repère des COMBINAISONS inhabituelles (trafic / attendu, pertes, latence, TCP sans réponse, variation du ratio) même
     quand chaque métrique reste sous son seuil. Un modèle par base, entraîné sur l'historique local.
     Verdict : relevé « isolé » ET trafic sous 90 % de l'attendu aux deux derniers relevés (les hausses ne nous intéressent pas).
D7 — probabilité qu'une alerte soit un VRAI incident, apprise sur les retours des superviseurs (« réel » / « faux positif »).
     Précautions : variables connues à la CRÉATION du ticket uniquement (jamais le TTR : fuite d'information), validation
     TEMPORELLE 80/20 (jamais aléatoire), classes déséquilibrées pondérées, au moins ``MIN_LABELED_TICKETS`` retours.
"""
from __future__ import annotations

import re
import threading
from datetime import datetime, timedelta

from sqlalchemy import text

from . import anomaly, history
from .anomaly import Finding
from .config import fmt, now_local, parse, settings
from .db import fetch_all, local_conn
from .logger import get_logger

logger = get_logger(__name__)

STEP = anomaly.STEP
ISOLATION_TRAINING_DAYS = 30
ISOLATION_MIN_ROWS = 1000              # ≈ 3,5 jours de relevés exploitables
ISOLATION_CONTAMINATION = 0.01         # on suppose ≈ 1 % de relevés atypiques dans l'historique
DROP_RATIO = 0.9                       # « trafic < 90 % de l'attendu »
QUALITY_MIN_PER_CLASS = 10
FEATURE_NAMES = ("trafic/attendu", "pertes %", "latence ms", "TCP sans réponse %", "variation du ratio")


class MlUnavailable(RuntimeError):
    """scikit-learn / joblib non installés."""


class NotEnoughData(RuntimeError):
    """Pas assez de données (ou d'étiquettes) pour entraîner un modèle fiable."""


def ml_available() -> bool:
    try:
        import joblib  # noqa: F401
        import sklearn  # noqa: F401
    except ImportError:
        return False
    return True


def _require_ml() -> None:
    if not ml_available():
        raise MlUnavailable("scikit-learn et joblib ne sont pas installés (pip install -r requirements-ml.txt)")


def _model_path(name: str):
    """Chemin d'un modèle ; le nom est restreint à [A-Za-z0-9_-] (une clé de base ne doit jamais sortir du dossier)."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", name):
        raise ValueError("nom de modèle invalide")
    settings.models_dir.mkdir(parents=True, exist_ok=True)
    return settings.models_dir / f"{name}.joblib"


_model_cache: dict[str, tuple[float, dict]] = {}
_model_lock = threading.Lock()


def _load_model(name: str) -> dict | None:
    """Modèle sauvegardé (mis en cache tant que le fichier ne change pas), ou None."""
    import joblib
    path = _model_path(name)
    if not path.exists():
        return None
    modified = path.stat().st_mtime
    with _model_lock:
        cached = _model_cache.get(name)
        if cached and cached[0] == modified:
            return cached[1]
        bundle = joblib.load(path)
        _model_cache[name] = (modified, bundle)
        return bundle


def has_model(name: str) -> bool:
    return ml_available() and _model_path(name).exists()


# ============================================================================
# D4 — Isolation Forest par base
# ============================================================================
def isolation_features(rows: dict[datetime, dict], at: datetime) -> list[float] | None:
    """Vecteur de variables du relevé ``at`` (None si le trafic attendu ou une mesure manque).

    ``rows`` : relevés de l'historique indexés par instant (il doit contenir ``at``, ``at - 5 min`` et leurs 7 jours précédents).
    """
    traffic = {ts: row["traffic_gbps"] for ts, row in rows.items() if row.get("traffic_gbps") is not None}
    ratios = []
    for offset in range(2):
        slot = at - offset * STEP
        reference = anomaly.seasonal_reference(traffic, slot)
        if reference is None or slot not in traffic or reference[0] < anomaly.MIN_EXPECTED_GBPS:
            return None
        ratios.append(traffic[slot] / reference[0])
    row = rows[at]
    if any(row.get(column) is None for column in ("loss_pct", "latency_ms", "tcp_no_response_pct")):
        return None
    return [ratios[0], row["loss_pct"], row["latency_ms"], row["tcp_no_response_pct"], ratios[0] - ratios[1]]


def isolation_training_matrix(db_key: str, days: int = ISOLATION_TRAINING_DAYS) -> list[list[float]]:
    end = history.utc_now()
    rows = {r["ts"]: r for r in history.load_series(db_key, end - timedelta(days=days + anomaly.SEASONAL_DAYS), end)}
    cutoff = end - timedelta(days=days)
    matrix = []
    for at in sorted(ts for ts in rows if ts >= cutoff):
        features = isolation_features(rows, at)
        if features:
            matrix.append(features)
    return matrix


def train_isolation(db_key: str, days: int = ISOLATION_TRAINING_DAYS) -> dict:
    """Entraîne et sauvegarde le modèle d'une base. Retourne ses métriques (nombre de relevés, part isolée à l'entraînement)."""
    _require_ml()
    import joblib
    from sklearn.ensemble import IsolationForest
    matrix = isolation_training_matrix(db_key, days)
    if len(matrix) < ISOLATION_MIN_ROWS:
        raise NotEnoughData(f"{db_key} : {len(matrix)} relevés exploitables (il en faut au moins {ISOLATION_MIN_ROWS}) — importez davantage d'historique.")
    model = IsolationForest(n_estimators=200, contamination=ISOLATION_CONTAMINATION, random_state=1).fit(matrix)
    flagged = int((model.predict(matrix) == -1).sum())
    trained_at = fmt(now_local().replace(tzinfo=None))
    joblib.dump({"model": model, "trained_at": trained_at, "rows": len(matrix), "features": FEATURE_NAMES}, _model_path(f"iforest_{db_key}"))
    with _model_lock:
        _model_cache.pop(f"iforest_{db_key}", None)
    return {"db_key": db_key, "rows": len(matrix), "flagged_in_training": flagged, "trained_at": trained_at}


def isolation_finding(db_key: str, at: datetime) -> Finding | None:
    """Verdict D4 sur le relevé ``at`` ; None si aucun modèle n'est entraîné pour cette base ou si les données manquent."""
    if not ml_available():
        return None
    bundle = _load_model(f"iforest_{db_key}")
    if bundle is None:
        return None
    needed = [at - o * STEP - timedelta(days=d) for o in range(2) for d in range(anomaly.SEASONAL_DAYS + 1)]
    rows = history.values_at(db_key, needed)
    if at not in rows:
        return None
    features = isolation_features(rows, at)
    if features is None:
        return None
    model = bundle["model"]
    isolated = int(model.predict([features])[0]) == -1
    score = float(-model.decision_function([features])[0])             # plus grand = plus atypique
    ratio, previous_ratio = features[0], features[0] - features[4]
    alert = isolated and ratio < DROP_RATIO and previous_ratio < DROP_RATIO
    explanation = (f"Combinaison inhabituelle (score {anomaly.fr(score, 2)}) : trafic à {anomaly.fr(ratio * 100, 0)} % de l'attendu, "
                   f"pertes {anomaly.fr(features[1], 2)} %, latence {anomaly.fr(features[2], 0)} ms, "
                   f"TCP sans réponse {anomaly.fr(features[3], 1)} %") if alert else "Combinaison habituelle"
    return Finding("isolation", alert, explanation, round(score, 3))


# ============================================================================
# D7 — qualité d'alerte (apprentissage supervisé sur les retours des superviseurs)
# ============================================================================
TICKET_TYPES = ("traffic", "pertes", "db_down")
QUALITY_FEATURES = ("type_traffic", "type_pertes", "type_db_down", "catégorie", "heure", "jour_semaine", "valeur", "tickets_7j_même_base")


def ticket_features(ticket: dict, prior_tickets_7d: int) -> list[float]:
    """Variables connues À LA CRÉATION du ticket. Volontairement absents : durée de résolution, fermeture, avertissements ajoutés après coup."""
    created = parse(ticket["timestamp"])
    return [*(1.0 if ticket["type"] == kind else 0.0 for kind in TICKET_TYPES), float(ticket.get("category") or 0),
            float(created.hour), float(created.weekday()), float(ticket.get("value") or 0.0), float(prior_tickets_7d)]


def prior_ticket_count(database_name: str, timestamp: str, exclude_id: int | None = None) -> int:
    """Nombre de tickets de la même base dans les 7 jours qui précèdent ``timestamp``."""
    start = fmt(parse(timestamp) - timedelta(days=7))
    with local_conn() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM tickets_tb WHERE database_name = :d AND timestamp >= :s AND timestamp < :t "
                                  "AND id <> :i AND status <> 'deleted'"),
                             {"d": database_name, "s": start, "t": timestamp, "i": exclude_id or -1}).scalar()
    return int(count)


def labeled_tickets() -> list[dict]:
    """Tickets ayant reçu un retour explicite (« real » = 1, « false » = 0), du plus ancien au plus récent."""
    with local_conn() as conn:
        rows = fetch_all(conn, "SELECT t.id, t.type, t.database_name, t.timestamp, t.category, t.value, f.label FROM tickets_tb t "
                               "JOIN ticket_feedback f ON f.ticket_id = t.id ORDER BY t.timestamp, t.id")
    for row in rows:
        row["timestamp"] = fmt(row["timestamp"])
        row["value"] = float(row["value"])
    return rows


def train_quality() -> dict:
    """Entraîne le modèle de qualité d'alerte. Évalué par découpage temporel 80/20, puis ré-entraîné sur tout le jeu."""
    _require_ml()
    import joblib
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.metrics import accuracy_score, precision_score, recall_score
    tickets = labeled_tickets()
    labels = [1 if t["label"] == "real" else 0 for t in tickets]
    if len(tickets) < settings.min_labeled_tickets:
        raise NotEnoughData(f"{len(tickets)} tickets étiquetés ; il en faut au moins {settings.min_labeled_tickets}.")
    if min(sum(labels), len(labels) - sum(labels)) < QUALITY_MIN_PER_CLASS:
        raise NotEnoughData(f"Il faut au moins {QUALITY_MIN_PER_CLASS} retours « réel » ET {QUALITY_MIN_PER_CLASS} « faux positif ».")
    vectors = [ticket_features(t, prior_ticket_count(t["database_name"], t["timestamp"], t["id"])) for t in tickets]
    split = int(len(vectors) * 0.8)                                      # temporel : les 20 % les plus RÉCENTS servent à évaluer

    def forest():
        return RandomForestClassifier(n_estimators=200, min_samples_leaf=3, class_weight="balanced", random_state=1)

    metrics: dict = {"labeled": len(tickets), "real": sum(labels), "false": len(labels) - sum(labels),
                     "train": split, "test": len(vectors) - split, "validation": "temporelle 80/20"}
    test_labels = labels[split:]
    if len(set(labels[:split])) == 2 and test_labels:
        predicted = forest().fit(vectors[:split], labels[:split]).predict(vectors[split:])
        metrics.update(accuracy=round(float(accuracy_score(test_labels, predicted)), 3),
                       precision_real=round(float(precision_score(test_labels, predicted, zero_division=0)), 3),
                       recall_real=round(float(recall_score(test_labels, predicted, zero_division=0)), 3),
                       precision_false=round(float(precision_score(test_labels, predicted, pos_label=0, zero_division=0)), 3),
                       recall_false=round(float(recall_score(test_labels, predicted, pos_label=0, zero_division=0)), 3),
                       baseline_accuracy=round(max(test_labels.count(0), test_labels.count(1)) / len(test_labels), 3))
    final = forest().fit(vectors, labels)
    trained_at = fmt(now_local().replace(tzinfo=None))
    joblib.dump({"model": final, "trained_at": trained_at, "features": QUALITY_FEATURES}, _model_path("quality"))
    with _model_lock:
        _model_cache.pop("quality", None)
    return {**metrics, "trained_at": trained_at}


def quality_score(ticket: dict) -> float | None:
    """Probabilité (0-1) que l'alerte soit un vrai incident ; None si le modèle n'est pas entraîné."""
    if not ml_available():
        return None
    bundle = _load_model("quality")
    if bundle is None:
        return None
    features = ticket_features(ticket, prior_ticket_count(ticket["database_name"], ticket["timestamp"], ticket.get("id")))
    probabilities = bundle["model"].predict_proba([features])[0]
    classes = list(bundle["model"].classes_)
    return round(float(probabilities[classes.index(1)]), 3) if 1 in classes else None
