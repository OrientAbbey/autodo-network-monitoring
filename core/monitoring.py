"""ORCHESTRATION — un cycle de supervision toutes les 5 minutes + tâches périodiques.

Modèle d'exécution (choix documentés) :
* le cycle est UNE fonction synchrone exécutée par APScheduler (``max_instances=1`` effectif : tout se passe dans
  le job, plus de threads « daemon » lancés puis oubliés) ;
* collecte DIAGNET en parallèle (``collector``, threads) ; calcul du tableau de bord et règles de détection :
  quelques dizaines de valeurs ⇒ Python pur, séquentiel (la parallélisation y coûterait plus qu'elle ne rapporte) ;
* écritures des tickets : UNE transaction pour tout le cycle ; Orange et e-mails sont hors chemin critique
  (outbox + exécuteur dédié) — un partenaire lent ne retarde jamais la détection ;
* l'API HTTP (FastAPI) utilise des endpoints synchrones (pool de threads) ou ``asyncio.to_thread`` : la boucle
  d'événements ne fait jamais d'E/S base de données.
"""
from __future__ import annotations

import asyncio
import threading
import time
from contextlib import suppress
from datetime import datetime, timedelta

from . import analytics, collector, constants, governance, history, metrics, notify, orange, rules, shadow, tickets
from .config import (config_for, fmt, load_config, load_overrides, local_to_utc_naive, now_local, parse, round5,
                     settings, utc_naive_to_local_naive)
from .logger import get_logger

logger = get_logger(__name__)

TRAFFIC_COLS = ("tcp_dw", "udp_dw", "icmp_dw", "other_dw")
_cycle_lock = threading.Lock()
_orange: orange.OrangeClient | None = None


def get_orange() -> orange.OrangeClient:
    global _orange
    if _orange is None:
        _orange = orange.OrangeClient()
    return _orange


class State:
    """État d'exploitation exposé par /api/status et surveillé par le watchdog."""
    def __init__(self):
        self.cycles_ok = 0
        self.cycles_failed = 0
        self.last_ok_at: float | None = None
        self.last_ok_iso: str | None = None
        self.last_duration_s: float = 0.0
        self.last_error: str = ""
        self.last_stats: dict = {}
        self.source_failures: dict[str, int] = {}
        self.unreachable_notified: set[str] = set()
        self.watchdog_notified = False
        self.started = time.monotonic()


state = State()


# ============================================================================
# Fenêtres de temps
# ============================================================================
def db_ts(dt_local: datetime) -> str:
    """Heure locale → chaîne UTC arrondie à 5 min (format de SAUV_td32)."""
    return fmt(round5(local_to_utc_naive(dt_local)))


def build_windows(checkpoint: datetime, cfg: dict) -> tuple[list, list, dict]:
    """(tous les horodatages à lire, horodatages d'alerte, checkpoints du tableau de bord) — tous en UTC pour les bases."""
    alert = sorted({db_ts(checkpoint - timedelta(minutes=5 * i)) for i in range(cfg["traffic_consecutive_drops_count"] + 1)}
                   | {db_ts(checkpoint - timedelta(days=1))})
    cps = {"5min": {"end": checkpoint, "start": checkpoint - timedelta(minutes=5)},
           "1day": {"end": checkpoint, "start": checkpoint - timedelta(days=1)},
           "1week": {"end": checkpoint, "start": checkpoint - timedelta(weeks=1)}}
    allts = set(alert)
    for c in cps.values():
        c["end_str"], c["start_str"] = db_ts(c["end"]), db_ts(c["start"])
        allts.update((c["end_str"], c["start_str"]))
    return sorted(allts), alert, cps


def build_series(snap: collector.DbSnapshot, alert_ts: list) -> tuple[list, list, list]:
    """Séries (horodatage local, trafic Gb/s, pertes %) du plus ancien au plus récent ; None = donnée manquante."""
    stamps, traffic, loss = [], [], []
    for ts in alert_ts:
        stamps.append(fmt(utc_naive_to_local_naive(parse(ts))))
        row = snap.whole.get(ts)
        if row and any(row.get(c) is not None for c in TRAFFIC_COLS):
            total = sum(row.get(c) or 0.0 for c in TRAFFIC_COLS)
            pkt = row.get("#tcp_pkt_dw") or 0.0
            traffic.append(round(total * constants.DEBIT_FACTOR, 2))
            loss.append(round(100 * (row.get("#tcp_loss_dw") or 0.0) / (pkt if pkt > 0 else 1), 3))
        else:
            traffic.append(None)
            loss.append(None)
    return stamps, traffic, loss


def widest_drop_window(cfg: dict, overrides: dict) -> int:
    """Plus grand « nombre de baisses consécutives » demandé (global ou surchargé) : la collecte doit couvrir le pire cas."""
    wanted = [cfg["traffic_consecutive_drops_count"]]
    wanted += [o["traffic_consecutive_drops_count"] for o in overrides.values() if "traffic_consecutive_drops_count" in o]
    return max(wanted)


def alerts_enabled(db_key: str, cfg: dict) -> bool:
    """Interrupteurs « alerts_enable_* » : créer (ou non) des tickets par catégorie de base (international, gi, roaming)."""
    if db_key in constants.LABELS.get("international", {}):
        return bool(cfg.get("alerts_enable_international", True))
    if db_key in constants.LABELS.get("utilisateur", {}):
        return bool(cfg.get("alerts_enable_gi", True))
    if "roaming" in db_key:
        return bool(cfg.get("alerts_enable_roaming", False))
    return True


# ============================================================================
# Le cycle
# ============================================================================
def run_cycle() -> dict:
    """Collecte → tableau de bord → détection → tickets. Retourne des statistiques (jamais d'exception)."""
    if not _cycle_lock.acquire(blocking=False):
        logger.warning("Cycle précédent encore en cours : exécution ignorée")
        return {"skipped": True}
    t0 = time.monotonic()
    stats: dict = {}
    try:
        cfg = load_config()                       # relue (cache par mtime) : les seuils modifiés sont pris en compte
        overrides = load_overrides()              # seuils par base : lus UNE fois par cycle
        constants.reload_database_configurations()
        checkpoint = round5(now_local().replace(tzinfo=None))
        now_str = fmt(checkpoint)
        logger.info("Cycle de supervision — checkpoint %s", now_str)
        all_ts, alert_ts, cps = build_windows(checkpoint, {**cfg, "traffic_consecutive_drops_count": widest_drop_window(cfg, overrides)})

        snaps = collector.collect_all(all_ts, alert_ts, monitor_roaming=bool(cfg.get("alerts_enable_roaming", False)))
        stats["sources_failed"] = [k for k, sample in snaps.items() if not sample.ok]

        # --- tableau de bord (même si certaines bases sont en échec)
        dash = {k: ({ts: sample.whole.get(ts, collector.EMPTY_ROW) for ts in all_ts} if sample.ok else {ts: None for ts in all_ts})
                for k, sample in snaps.items()}
        tdd = {k: ({ts: sample.apn.get(ts, collector.EMPTY_ROW) for ts in all_ts} if sample.ok else {ts: None for ts in all_ts})
               for k, sample in snaps.items() if k in constants.LABELS.get("utilisateur", {})}
        try:
            cps_dash = {i: {**c, "end": c["end"].replace(tzinfo=constants.CAMEROON_TZ),
                            "start": c["start"].replace(tzinfo=constants.CAMEROON_TZ)} for i, c in cps.items()}
            data = metrics.build_dashboard(dash, tdd, cps_dash, checkpoint.replace(tzinfo=constants.CAMEROON_TZ))
            constants.set_dashboard(data)
        except Exception:
            logger.exception("Calcul du tableau de bord en échec (la supervision continue)")

        # --- détection + tickets
        stats.update(_process_monitoring(snaps, alert_ts, now_str, cfg, overrides))

        state.cycles_ok += 1
        state.last_ok_at, state.last_ok_iso = time.monotonic(), fmt(now_local().replace(tzinfo=None))
        state.last_error, state.watchdog_notified = "", False
        logger.info("Cycle terminé en %.1fs : %s", time.monotonic() - t0, {k: v for k, v in stats.items() if k != "sources_failed"})
        publish_threadsafe({"type": "data_version", "version": constants.DASHBOARD_VERSION})      # les navigateurs rechargent (avec gigue)
    except Exception as e:
        state.cycles_failed += 1
        state.last_error = f"{type(e).__name__}: {e}"
        logger.exception("Cycle de supervision en échec")
    finally:
        state.last_duration_s = time.monotonic() - t0
        state.last_stats = stats
        _cycle_lock.release()
    return stats


def _process_monitoring(snapshots_by_base: dict, alert_ts: list, now_str: str, cfg: dict, overrides: dict | None = None) -> dict:
    """Évalue chaque base d'un cycle : détecte, crée/met à jour/ferme les tickets (une seule transaction), notifie, puis alimente l'historique et les détecteurs en mode ombre. Retourne les compteurs du cycle."""
    overrides = overrides or {}
    # maintenance + silences après rejet : lus UNE fois, à l'heure RÉELLE (le checkpoint est arrondi jusqu'à 5 min en arrière :
    # une fenêtre ouverte « maintenant » ne s'appliquerait pas)
    silenced = governance.silenced_databases()
    open_list = tickets.load_open()                              # UNE lecture par cycle
    open_ticket_by_base_and_type = {(ticket["database_name"], ticket["type"]): ticket for ticket in open_list}
    changes = tickets.ChangeSet()
    orange_enabled = get_orange().configured
    id_factory = tickets.next_id
    checkpoint = parse(now_str)
    evaluated_bases = 0
    silenced_bases = 0
    for db_key, snap in snapshots_by_base.items():
        try:
            # M-05 : un incident de SUPERVISION (base injoignable) n'est pas un incident RÉSEAU → pas de ticket Orange
            if not snap.ok:
                consecutive_failures = state.source_failures.get(db_key, 0) + 1
                state.source_failures[db_key] = consecutive_failures
                if consecutive_failures >= cfg["source_unreachable_cycles"] and db_key not in state.unreachable_notified:
                    state.unreachable_notified.add(db_key)
                    notify.notify_internal(f"Source DIAGNET injoignable : {db_key}",
                                           f"La base {db_key} est injoignable depuis {consecutive_failures} cycles.\nDernière erreur : {snap.error}")
                continue
            if state.source_failures.pop(db_key, 0) and db_key in state.unreachable_notified:
                state.unreachable_notified.discard(db_key)
                logger.info("Source %s de nouveau joignable", db_key)
            if not alerts_enabled(db_key, cfg):
                continue
            if governance.is_silenced(db_key, silenced):
                silenced_bases += 1                      # aucune création ni mise à jour de ticket pendant la maintenance
                continue
            db_cfg = config_for(db_key, cfg, overrides)      # seuils globaux + surcharges de CETTE base
            evaluated_bases += 1
            stamps, traffic, loss = build_series(snap, alert_ts)
            traffic_samples = [{"timestamp": stamp, "value": value} for stamp, value in zip(stamps, traffic, strict=True)]
            loss_samples = [{"timestamp": stamp, "value": value} for stamp, value in zip(stamps, loss, strict=True)]
            # (J-1 en tête de série ; la détection n'examine que les valeurs récentes)
            db_context = tickets.DbContext(
                db_key=db_key, now=now_str, traffic_values=traffic_samples, loss_values=loss_samples,
                traffic=rules.detect_traffic(traffic[1:], db_cfg), pertes=rules.detect_pertes(loss[1:], db_cfg),
                mode7d=snap.mode7d, cfg=db_cfg, orange_enabled=orange_enabled)
            open_traffic_ticket, open_loss_ticket = open_ticket_by_base_and_type.get((db_key, "traffic")), open_ticket_by_base_and_type.get((db_key, "pertes"))
            actions = rules.plan_actions(db_context.traffic.alert, db_context.pertes.alert, open_traffic_ticket is not None, open_loss_ticket is not None)
            tickets.apply_actions(db_context, actions, {"traffic": open_traffic_ticket, "pertes": open_loss_ticket}, changes, id_factory)

            last_local = utc_naive_to_local_naive(snap.last_update) if snap.last_update else None
            fresh, minutes = rules.db_freshness(last_local, checkpoint, db_cfg)
            if fresh == "unknown":
                logger.warning("[%s] Aucune donnée dans la base — fraîcheur inconnue (pas de ticket)", db_key)
            tickets.apply_db_down(db_key, fresh, minutes, fmt(last_local) if last_local else None, now_str, db_cfg,
                                  open_ticket_by_base_and_type.get((db_key, "db_down")), changes, orange_enabled, id_factory)
        except Exception:
            logger.exception("Échec du traitement de %s (cycle poursuivi pour les autres bases)", db_key)

    conflicts = tickets.persist(changes)
    created = list(changes.inserts)
    announced = tickets.visible_to_recipients(created)           # une création en attente de validation n'est PAS annoncée par e-mail
    if announced:                                                # (l'e-mail part à l'approbation : voir routers/tickets.py)
        closed_ids = {tid for tid, f in changes.updates.items() if f.get("status") == "closed"}
        still_open = tickets.visible_to_recipients([ticket for ticket in open_list if ticket["id"] not in closed_ids] + created)
        notify.notify_new_tickets(announced, still_open)
    # Historique local puis détecteurs « futurs » en mode ombre : aucun des deux ne peut faire échouer le cycle ni toucher un ticket
    rows_recorded = history.record_snapshots(snapshots_by_base)
    shadow_verdicts = sum(shadow.run_shadow(snapshots_by_base, created).values())
    kinds: dict[str, int] = {}
    for kind, *_ in changes.events:
        kinds[kind] = kinds.get(kind, 0) + 1
    return {"bases_evaluees": evaluated_bases, "bases_en_silence": silenced_bases, "tickets_crees": len(created), "tickets_maj": len(changes.updates), "conflits": len(conflicts), "historique": rows_recorded, "ombre": shadow_verdicts, **kinds}


# ============================================================================
# Tâches périodiques
# ============================================================================
def _safe(fn, name: str):
    def wrapper():
        try:
            fn()
        except Exception:
            logger.exception("Tâche %s en échec", name)
    wrapper.__name__ = name
    return wrapper


def job_orange_sync() -> None:
    """Outbox (création/restauration) puis notes de mise à jour des tickets déjà créés chez Orange."""
    orange.sync_outbox(get_orange(), on_failed=lambda t, err: notify.notify_internal(
        f"Orange : abandon du ticket {t['id']}", f"Le ticket AUTODO {t['id']} n'a pas pu être synchronisé avec Orange.\n{err}"))
    orange.sync_notes(get_orange())


def job_orange_reconcile() -> None:
    orange.reconcile(get_orange())


def job_watchdog() -> None:
    """Auto-supervision : alerte si aucun cycle réussi depuis WATCHDOG_MAX_AGE_MIN minutes."""
    ref = state.last_ok_at or state.started
    age_min = (time.monotonic() - ref) / 60
    if age_min > settings.watchdog_max_age_min and not state.watchdog_notified:
        state.watchdog_notified = True
        logger.error("WATCHDOG : aucun cycle réussi depuis %.0f min", age_min)
        notify.notify_internal("AUTODO silencieux", f"Aucun cycle de supervision réussi depuis {age_min:.0f} minutes.\n"
                                                    f"Dernière erreur : {state.last_error or 'aucune'}")


def register_jobs(scheduler) -> None:
    """Enregistre les tâches périodiques : cycle de supervision (toutes les 5 min), synchronisation et réconciliation Orange, vidage des visites, pic de spectateurs, chien de garde, purges nocturnes."""
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger
    tz = constants.CAMEROON_TZ
    scheduler.add_job(_safe(run_cycle, "cycle"), CronTrigger(minute="1-59/5", timezone=tz), id="cycle",
                      max_instances=1, coalesce=True, misfire_grace_time=120, next_run_time=now_local())
    scheduler.add_job(_safe(job_orange_sync, "orange_sync"), IntervalTrigger(seconds=max(5, settings.orange_sync_interval)), id="orange_sync",
                      max_instances=1, coalesce=True)
    scheduler.add_job(_safe(job_orange_reconcile, "orange_reconcile"), CronTrigger(minute="*/15", timezone=tz),
                      id="orange_reconcile", max_instances=1, coalesce=True)
    scheduler.add_job(_safe(analytics.flush_visits, "flush_visits"), IntervalTrigger(seconds=10), id="flush_visits",
                      max_instances=1, coalesce=True)
    scheduler.add_job(_safe(analytics.purge, "purge"), CronTrigger(hour=3, minute=17, timezone=tz), id="purge",
                      max_instances=1, coalesce=True)
    scheduler.add_job(_safe(job_viewer_peak, "viewer_peak"), "interval", minutes=1, id="viewer_peak", max_instances=1, coalesce=True)
    scheduler.add_job(_safe(history.purge, "purge_history"), CronTrigger(hour=3, minute=27, timezone=tz), id="purge_history",
                      max_instances=1, coalesce=True)
    scheduler.add_job(_safe(job_watchdog, "watchdog"), IntervalTrigger(minutes=5), id="watchdog",
                      max_instances=1, coalesce=True)


def status() -> dict:
    return {"cycles_ok": state.cycles_ok, "cycles_failed": state.cycles_failed, "last_ok": state.last_ok_iso,
            "last_duration_s": round(state.last_duration_s, 2), "last_error": state.last_error,
            "last_stats": state.last_stats, "sources_failing": sorted(state.source_failures),
            "uptime_s": int(time.monotonic() - state.started), "orange": get_orange().test_connection_light()}


# ============================================================================
# SSE (appelé depuis l'API ; les abonnés sont dans app.state)
# ============================================================================
class _Runtime:
    """Boucle asyncio et application FastAPI, liées au démarrage : le cycle (thread APScheduler) s'en sert pour publier en SSE."""
    loop: asyncio.AbstractEventLoop | None = None
    app = None


runtime = _Runtime()


def bind_runtime(loop: asyncio.AbstractEventLoop, app) -> None:
    runtime.loop, runtime.app = loop, app


def publish_threadsafe(event: dict) -> None:
    """Publie un évènement SSE depuis un AUTRE thread (cycle, tâches). Sans effet hors serveur ; ne lève jamais."""
    loop, app = runtime.loop, runtime.app
    if loop is None or app is None or loop.is_closed():
        return
    try:
        asyncio.run_coroutine_threadsafe(sse_publish({**event, "at": now_local().isoformat(timespec="seconds")}, app), loop)
    except Exception:
        logger.exception("Publication SSE impossible")


def viewer_count() -> int:
    """Nombre de flux SSE ouverts (= spectateurs connectés)."""
    subscribers = getattr(getattr(runtime.app, "state", None), "sse_subscribers", None)
    return len(subscribers) if subscribers is not None else 0


def job_viewer_peak() -> None:
    """Chaque minute : conserve le pic de spectateurs simultanés de la journée (T5)."""
    analytics.record_viewer_peak(viewer_count())


async def sse_publish(event: dict, app) -> None:
    subs = getattr(app.state, "sse_subscribers", None)
    if not subs:
        return
    async with app.state.sse_subscribers_lock:
        for q in list(subs):
            try:
                if q.full():
                    with suppress(Exception):
                        q.get_nowait()
                q.put_nowait(event)
            except Exception:
                subs.discard(q)


def next_tick(now: datetime) -> datetime:
    """Prochain hh:02, hh:07, hh:12… (1 min après la collecte)."""
    base = now.replace(second=0, microsecond=0)
    return base + timedelta(minutes=((2 - base.minute) % 5) or 5)


async def sse_tick_task(app) -> None:
    while True:
        try:
            now = now_local()
            await asyncio.sleep(max(0.5, (next_tick(now) - now).total_seconds()))
            await sse_publish({"type": "scheduled_update", "at": now_local().isoformat(timespec="seconds")}, app)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Erreur sse_tick_task")
            await asyncio.sleep(5)
