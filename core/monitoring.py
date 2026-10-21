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

from . import analytics, collector, constants, governance, metrics, notify, orange, rules, tickets
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


def _alerts_enabled(db_key: str, cfg: dict) -> bool:
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
        stats["sources_failed"] = [k for k, s in snaps.items() if not s.ok]

        # --- tableau de bord (même si certaines bases sont en échec)
        dash = {k: ({ts: s.whole.get(ts, collector.EMPTY_ROW) for ts in all_ts} if s.ok else {ts: None for ts in all_ts})
                for k, s in snaps.items()}
        tdd = {k: ({ts: s.apn.get(ts, collector.EMPTY_ROW) for ts in all_ts} if s.ok else {ts: None for ts in all_ts})
               for k, s in snaps.items() if k in constants.LABELS.get("utilisateur", {})}
        try:
            cps_dash = {i: {**c, "end": c["end"].replace(tzinfo=constants.CAMEROON_TZ),
                            "start": c["start"].replace(tzinfo=constants.CAMEROON_TZ)} for i, c in cps.items()}
            data = metrics.build_dashboard(dash, tdd, cps_dash, checkpoint.replace(tzinfo=constants.CAMEROON_TZ))
            with constants.DASHBOARD_CACHE_LOCK:
                constants.DASHBOARD_CACHE.clear()
                constants.DASHBOARD_CACHE.update(data)
        except Exception:
            logger.exception("Calcul du tableau de bord en échec (la supervision continue)")

        # --- détection + tickets
        stats.update(_process_monitoring(snaps, alert_ts, now_str, cfg, overrides))

        state.cycles_ok += 1
        state.last_ok_at, state.last_ok_iso = time.monotonic(), fmt(now_local().replace(tzinfo=None))
        state.last_error, state.watchdog_notified = "", False
        logger.info("Cycle terminé en %.1fs : %s", time.monotonic() - t0, {k: v for k, v in stats.items() if k != "sources_failed"})
    except Exception as e:
        state.cycles_failed += 1
        state.last_error = f"{type(e).__name__}: {e}"
        logger.exception("Cycle de supervision en échec")
    finally:
        state.last_duration_s = time.monotonic() - t0
        state.last_stats = stats
        _cycle_lock.release()
    return stats


def _process_monitoring(snaps: dict, alert_ts: list, now_str: str, cfg: dict, overrides: dict | None = None) -> dict:
    overrides = overrides or {}
    silenced = governance.silenced_databases(now_str)          # maintenance + silences après rejet : lus UNE fois
    open_list = tickets.load_open()                              # UNE lecture par cycle
    index = {(t["database_name"], t["type"]): t for t in open_list}
    cs = tickets.ChangeSet()
    orange_on = get_orange().configured
    id_factory = tickets.next_id
    checkpoint = parse(now_str)
    n_db = 0
    n_silenced = 0
    for db_key, snap in snaps.items():
        try:
            # M-05 : un incident de SUPERVISION (base injoignable) n'est pas un incident RÉSEAU → pas de ticket Orange
            if not snap.ok:
                n = state.source_failures.get(db_key, 0) + 1
                state.source_failures[db_key] = n
                if n >= cfg["source_unreachable_cycles"] and db_key not in state.unreachable_notified:
                    state.unreachable_notified.add(db_key)
                    notify.notify_internal(f"Source DIAGNET injoignable : {db_key}",
                                           f"La base {db_key} est injoignable depuis {n} cycles.\nDernière erreur : {snap.error}")
                continue
            if state.source_failures.pop(db_key, 0) and db_key in state.unreachable_notified:
                state.unreachable_notified.discard(db_key)
                logger.info("Source %s de nouveau joignable", db_key)
            if not _alerts_enabled(db_key, cfg):
                continue
            if governance.is_silenced(db_key, silenced):
                n_silenced += 1                      # aucune création ni mise à jour de ticket pendant la maintenance
                continue
            db_cfg = config_for(db_key, cfg, overrides)      # seuils globaux + surcharges de CETTE base
            n_db += 1
            stamps, traffic, loss = build_series(snap, alert_ts)
            tv = [{"timestamp": s, "value": v} for s, v in zip(stamps, traffic, strict=True)]
            lv = [{"timestamp": s, "value": v} for s, v in zip(stamps, loss, strict=True)]
            # (J-1 en tête de série ; la détection n'examine que les valeurs récentes)
            ctx = tickets.DbContext(
                db_key=db_key, now=now_str, traffic_values=tv, loss_values=lv,
                traffic=rules.detect_traffic(traffic[1:], db_cfg), pertes=rules.detect_pertes(loss[1:], db_cfg),
                mode7d=snap.mode7d, cfg=db_cfg, orange_enabled=orange_on)
            tt, pt = index.get((db_key, "traffic")), index.get((db_key, "pertes"))
            actions = rules.plan_actions(ctx.traffic.alert, ctx.pertes.alert, tt is not None, pt is not None)
            tickets.apply_actions(ctx, actions, {"traffic": tt, "pertes": pt}, cs, id_factory)

            last_local = utc_naive_to_local_naive(snap.last_update) if snap.last_update else None
            fresh, minutes = rules.db_freshness(last_local, checkpoint, db_cfg)
            if fresh == "unknown":
                logger.warning("[%s] Aucune donnée dans la base — fraîcheur inconnue (pas de ticket)", db_key)
            tickets.apply_db_down(db_key, fresh, minutes, fmt(last_local) if last_local else None, now_str, db_cfg,
                                  index.get((db_key, "db_down")), cs, orange_on, id_factory)
        except Exception:
            logger.exception("Échec du traitement de %s (cycle poursuivi pour les autres bases)", db_key)

    conflicts = tickets.persist(cs)
    created = [t for t in cs.inserts]
    if created:
        closed_ids = {tid for tid, f in cs.updates.items() if f.get("status") == "closed"}
        still_open = [t for t in open_list if t["id"] not in closed_ids] + created
        notify.notify_new_tickets(created, still_open)
    kinds: dict[str, int] = {}
    for kind, *_ in cs.events:
        kinds[kind] = kinds.get(kind, 0) + 1
    return {"bases_evaluees": n_db, "bases_en_silence": n_silenced, "tickets_crees": len(created), "tickets_maj": len(cs.updates), "conflits": len(conflicts), **kinds}


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
