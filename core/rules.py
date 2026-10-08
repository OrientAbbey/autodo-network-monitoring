"""RÈGLES MÉTIER PURES — aucune E/S (ni base, ni réseau, ni horloge implicite) : 100 % testable.

* détection  : ``detect_traffic``, ``detect_pertes``, ``db_freshness``
* fermeture  : ``closure_criterion_met``, ``evaluate_closure`` (fenêtre de persistance = hystérésis)
* décision   : ``plan_actions`` — remplace les 16 branches de l'ancien ``process_ticket_logic`` par 4 règles.
* textes     : descriptions de tickets.
Les valeurs de trafic sont en Gb/s, les pertes en %, les horodatages « YYYY-mm-dd HH:MM:SS » en heure locale.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from .config import TS_FMT, parse


# ============================================================================
# DÉTECTION
# ============================================================================
@dataclass(frozen=True)
class Verdict:
    alert: bool
    reason: str = ""
    metric: float = 0.0       # variation (%) ayant déclenché l'alerte
    category: int = 0         # trafic : 1 nul · 2 chute instantanée · 3 baisses consécutives ; pertes : 1


def percent_variation(prev: float, curr: float) -> float:
    """Variation en % de ``curr`` par rapport à ``prev`` (−20 = baisse de 20 %)."""
    if prev == 0:
        return 0.0 if curr == 0 else 100.0
    return (curr - prev) / prev * 100.0


def detect_traffic(recent: list, cfg: dict) -> Verdict:
    """``recent`` : valeurs du plus ancien au plus récent (la valeur « J-1 » en est exclue)."""
    n_drops = cfg["traffic_consecutive_drops_count"]
    min_zero = cfg.get("zero_traffic_min_samples", 1)
    if any(v is None for v in recent[-(n_drops + 1):]):
        return Verdict(False, "Données manquantes - évaluation impossible")
    values = list(recent)
    if not values:
        return Verdict(False, "Traffic conditions normal")

    # 1) trafic nul
    if values[-1] == 0:
        zeros = 0
        for v in reversed(values):
            if v != 0:
                break
            zeros += 1
        if zeros >= min_zero:
            reason = ("Perte de données persistante (trafic nul sur plusieurs intervalles)" if zeros >= 2
                      else "Trafic tombé à zéro - Ticket critique immédiat")
            return Verdict(True, reason, 0.0, 1)
        return Verdict(False, f"Trafic nul non confirmé ({zeros}/{min_zero} échantillons)")   # attente de confirmation

    # 2) chute instantanée
    thr = cfg["alert_traffic_threshold_percent"]
    if len(values) >= 2:
        var = percent_variation(values[-2], values[-1])
        if var < 0 and abs(var) >= thr:
            return Verdict(True, f"Diminution critique instantanée {values[-2]:.1f} → {values[-1]:.1f} "
                                 f"({abs(var):.1f}% ≥ seuil {thr}%)", abs(var), 2)

    # 3) diminutions consécutives : 1er pas strict ≥ seuil, pas suivants non croissants
    if len(values) >= n_drops + 1:
        w = values[-(n_drops + 1):]
        steps = len(w) - 1
        if w[1] < w[0] and all(w[i + 1] <= w[i] for i in range(1, steps)):
            var = [percent_variation(w[i], w[i + 1]) for i in range(steps)]
            first = abs(var[0])
            min_pct = cfg["traffic_consecutive_drops_min_percent"]
            if first >= min_pct:
                return Verdict(True, f"{n_drops} diminutions consécutives détectées "
                                     f"(première baisse: {first:.1f}% ≥ {min_pct:.1f}%) "
                                     f"({' → '.join(f'{v:.1f}' for v in w)} Gb/s) - "
                                     f"Détail: [{', '.join(f'{abs(d):.1f}%' for d in var)}]", first, 3)
    return Verdict(False, "Traffic conditions normal")


def detect_pertes(recent: list, cfg: dict) -> Verdict:
    count = cfg.get("pertes_consecutive_count", 1)
    thr = cfg["alert_pertes_threshold"]
    tail = list(recent)[-count:]
    if not tail or any(v is None for v in tail):
        return Verdict(False)
    if len(tail) >= min(count, len(recent)) and all(p >= thr for p in tail):
        return Verdict(True, f"Pertes critiques détectées: {max(tail):.2f}% ≥ seuil {thr}%", max(tail), 1)
    return Verdict(False)


def db_freshness(last_update: Optional[datetime], now: datetime, cfg: dict) -> tuple[str, float]:
    """(« down » | « ok » | « unknown », minutes de retard). Les deux datetimes sont en heure locale naïve."""
    if last_update is None:
        return "unknown", 0.0
    minutes = (now - last_update).total_seconds() / 60.0
    return ("down" if minutes > cfg["db_down_threshold_minutes"] else "ok"), minutes


# ============================================================================
# VALEUR D'ORIGINE & FERMETURE
# ============================================================================
def original_value(ticket_type: str, last_values: list, category: Optional[int], cfg: dict) -> tuple[float, str]:
    """Valeur de référence du ticket (index bornés : jamais d'IndexError)."""
    if not last_values:
        return 0.0, ""
    n = len(last_values)
    pick = lambda i: last_values[max(-n, min(i, n - 1))]       # noqa: E731
    if ticket_type == "traffic":
        if category in (1, 2):
            v = pick(-2)
        elif category == 3:
            v = pick(1)
        else:
            v = pick(-1)
    elif ticket_type == "pertes":
        v = pick(-cfg.get("pertes_consecutive_count", 1))
    else:
        v = pick(-1)
    return float(v["value"]), v["timestamp"]


def closure_criterion_met(ticket_type: str, current: Optional[float], original: float, category: int,
                          mode7d: Optional[float], cfg: dict) -> bool:
    if current is None:
        return False
    if ticket_type == "pertes":
        return current < cfg["alert_pertes_threshold"]
    if ticket_type == "traffic":
        if category == 1:
            return current > 0
        if original > 0:
            return (current >= original * cfg["traffic_recovery_ratio"]
                    or (mode7d is not None and current >= mode7d * cfg["mode7days_recovery_ratio"]))
        return False
    if ticket_type == "db_down":
        return current <= cfg["db_recovery_threshold_minutes"]
    return False


def closure_reason(ticket_type: str, current: float, original: float, persistence: int) -> str:
    if ticket_type == "pertes":
        return f"Pertes resolved: stable for {persistence} minutes"
    if ticket_type == "traffic":
        pct = current / original * 100 if original > 0 else 100
        return f"Traffic recovered to {pct:.1f}% of original value and stable for {persistence} minutes."
    if ticket_type == "db_down":
        return f"Database back online: {current:.1f} minutes behind. Stable for {persistence} minutes."
    return f"Resolved and stable for {persistence} minutes."


def ttr_seconds(created: str, closed: str) -> Optional[int]:
    try:
        return max(int((parse(closed) - parse(created)).total_seconds()), 0)
    except Exception:
        return None


def ttr_human(seconds: Optional[int]) -> str:
    if seconds is None:
        return ""
    d, r = divmod(int(seconds), 86400)
    h, r = divmod(r, 3600)
    m = r // 60
    return " ".join(p for p in (f"{d}d" if d else "", f"{h}h" if h else "", f"{m}m" if m or not (d or h) else "") if p)


def evaluate_closure(ticket: dict, last_values: list, now: str, cfg: dict, mode7d: Optional[float]) -> dict:
    """Tentative de fermeture avec fenêtre de persistance. Retourne les CHAMPS à modifier (jamais d'E/S)."""
    persistence = cfg["alert_persistence_duration_minutes"]
    ttype = ticket["type"]
    category = int(ticket.get("category") or 0)
    current = last_values[-1]["value"] if last_values else None
    orig = float(ticket.get("value") or 0.0)
    now_dt = parse(now)
    pending = ticket.get("closure_pending_timestamp")

    if not closure_criterion_met(ttype, current, orig, category, mode7d, cfg):
        return {"closure_pending_timestamp": None} if pending else {}

    if not pending:
        deadline = now_dt + timedelta(minutes=persistence)
        return {"closure_pending_timestamp": deadline.strftime(TS_FMT),
                "closure_reason": f"Pending closure: criterium met. Will close if maintained for {persistence} minutes."}
    try:
        deadline = parse(pending)
    except Exception:
        return {"closure_pending_timestamp": (now_dt + timedelta(minutes=persistence)).strftime(TS_FMT)}
    if now_dt < deadline:
        return {}
    return {"status": "closed", "closure_timestamp": now, "closure_pending_timestamp": None,
            "closure_reason": closure_reason(ttype, current, orig, persistence),
            "ttr_seconds": ttr_seconds(ticket["timestamp"], now)}


def evaluate_update(ticket: dict, last_values: list, reason: str, category: int, cfg: dict) -> dict:
    """Mise à jour SANS fermeture : valeurs récentes, motif/catégorie, annulation de toute fermeture en attente."""
    changes: dict = {"last_values": last_values, "closure_pending_timestamp": None}
    if ticket["type"] in ("pertes", "traffic"):
        current_cat = int(ticket.get("category") or 0)
        if category not in (current_cat, 0) and ticket["type"] == "pertes":
            changes["value"] = original_value("pertes", last_values, category, cfg)[0]
        if category != 0:
            changes["reason"] = reason or ticket.get("reason", "")
            changes["category"] = category
    return changes


def attach_pertes(ticket: dict, loss_pct: float, now: str) -> dict:
    """Ajoute un avertissement « pertes » à un ticket trafic (au plus un toutes les 30 min). Retourne les champs."""
    warnings = list(ticket.get("pertes_warnings") or [])
    try:
        if warnings and (parse(now) - parse(warnings[-1]["timestamp"])).total_seconds() / 60 < 30:
            return {}
    except (KeyError, ValueError, TypeError):
        pass                       # horodatage précédent illisible : on ajoute l'avertissement
    warnings.append({"timestamp": now, "loss_percentage": loss_pct,
                     "message": f"High packet loss ({loss_pct}%) detected during traffic issue"})
    desc = ticket.get("description") or f"Traffic issue detected on {ticket.get('database_name', 'unknown')}"
    if "PERTES ALERT" not in desc:
        desc += ("\n\n--- PERTES ALERT ---\nHigh packet loss detected during this traffic issue. "
                 "See pertes_warnings for details.")
    return {"pertes_warnings": warnings, "pertes": "Yes", "description": desc}


# ============================================================================
# DÉCISION : QUELLES ACTIONS SUR LES TICKETS ?
# ============================================================================
@dataclass(frozen=True)
class Action:
    kind: str                         # create_traffic | create_pertes | update_traffic | update_pertes | try_close
    ticket_type: str = ""             # pour try_close : « traffic » ou « pertes »
    attach_pertes: bool = False       # rattacher un avertissement de pertes au ticket trafic


def plan_actions(traffic_alert: bool, pertes_alert: bool, has_traffic_ticket: bool, has_pertes_ticket: bool) -> list[Action]:
    """Les 16 combinaisons (alerte trafic × alerte pertes × ticket trafic × ticket pertes) en 4 règles :

    1. *Trafic en alerte*  : créer le ticket trafic s'il n'existe pas, sinon le mettre à jour.
    2. *Trafic au repos*   : si des pertes persistent sans ticket « pertes » propre, le ticket trafic reste vivant
                             (mise à jour + avertissement) ; sinon tentative de fermeture.
    3. *Pertes en alerte*  : mettre à jour le ticket « pertes » existant ; sinon, créer un ticket « pertes » seulement
                             si aucun ticket trafic ne peut les porter.
    4. *Pertes au repos*   : tentative de fermeture du ticket « pertes » s'il existe.
    Les pertes sont rattachées au ticket trafic lorsque les deux coexistent et qu'il n'y a pas de ticket « pertes ».
    """
    T, P, tt, pt = traffic_alert, pertes_alert, has_traffic_ticket, has_pertes_ticket
    actions: list[Action] = []
    if T:
        actions.append(Action("update_traffic", attach_pertes=P and not pt) if tt
                       else Action("create_traffic", attach_pertes=P))
    elif tt:
        actions.append(Action("update_traffic", attach_pertes=True) if (P and not pt)
                       else Action("try_close", "traffic"))
    if P:
        if pt:
            actions.append(Action("update_pertes"))
        elif not T and not tt:
            actions.append(Action("create_pertes"))
    elif pt:
        actions.append(Action("try_close", "pertes"))
    return actions


# ============================================================================
# TEXTES DES TICKETS
# ============================================================================
def build_description(ticket_type: str, site: str, reason: str, value: float, ts: str,
                      connection_failed: bool = False, failure_reason: str = "") -> str:
    if connection_failed:
        head = f"[DATABASE CONNECTION FAILURE - {ticket_type.upper()}] Perte de connexion à la base de données {site}. Raison: {failure_reason}. "
        if ticket_type == "traffic":
            return head + f"Trafic traité comme zéro - Vérification infrastructure urgente requise. Timestamp: {ts}, Valeur: {value} Gb/s"
        if ticket_type == "pertes":
            return head + f"Pertes traitées comme zéro - Diagnostic infrastructure requis. Timestamp: {ts}, Taux de perte: {value:.3f}%"
        return head + f"Timestamp: {ts}, Valeur: {value}"
    if ticket_type == "traffic":
        if "Perte de données" in reason:
            return (f"[TRAFFIC CRITIQUE] Perte totale de données détectée sur {site}. Valeurs de trafic nulles consécutives. "
                    f"Vérification urgente requise. Timestamp: {ts}, Valeur actuelle: {value} Gb/s")
        if "Diminution critique instantanée" in reason:
            return (f"[TRAFFIC CRITIQUE] Chute instantanée critique du trafic sur {site}. {reason}. "
                    f"Investigation immédiate nécessaire. Timestamp: {ts}, Valeur actuelle: {value} Gb/s")
        if "diminutions consécutives" in reason:
            return (f"[TRAFFIC ALERTE] Pattern de diminutions consécutives détecté sur {site}. {reason}. "
                    f"Surveillance renforcée recommandée. Timestamp: {ts}, Valeur actuelle: {value} Gb/s")
        return f"[TRAFFIC] Anomalie de trafic détectée sur {site}. {reason}. Timestamp: {ts}, Valeur: {value} Gb/s"
    if ticket_type == "pertes":
        return (f"[PERTES CRITIQUES] Taux de perte de paquets élevé sur {site}. {reason}. "
                f"Diagnostic réseau requis. Timestamp: {ts}, Taux de perte: {value:.3f}%")
    return f"[{ticket_type.upper()}] Incident détecté sur {site}. {reason}. Timestamp: {ts}, Valeur: {value}"


def db_down_description(site: str, minutes: float, last_update: str, now: str, threshold: int) -> str:
    return (f"[BASE DE DONNÉES HORS LIGNE - CRITIQUE] Base de données {site} non mise à jour depuis {minutes:.1f} minutes.\n"
            f"🚨 ALERTE CRITIQUE: Absence de données récentes\n📍 Localisation: {site}\n"
            f"⏰ Dernière mise à jour: {last_update}\n🕐 Heure actuelle: {now}\n"
            f"⏱️ Retard: {minutes:.1f} minutes (Seuil critique: {threshold} minutes)\n"
            f"🔧 ACTIONS REQUISES:\n- Vérifier la connectivité de la base de données\n"
            f"- Contrôler les processus de collecte de données\n- Examiner l'infrastructure réseau locale\n"
            f"- Redémarrer les services si nécessaire\n"
            f"📊 Impact: Perte de visibilité sur le trafic et les performances du site {site}")


def db_down_reason(db_key: str, minutes: float) -> str:
    return f"Base de données {db_key} non mise à jour depuis {int(minutes // 60)}h {int(minutes % 60)}m"
