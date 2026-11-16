"""SOURCE DIAGNET SIMULÉE (mode démo) — données reproductibles, sans aucune base de données.

En mode ``DIAGNET_SOURCE=fake`` le collecteur n'ouvre pas de connexion DIAGNET : il appelle ce module, qui
recalcule pour chaque base du catalogue exactement les mêmes colonnes que les tables ``whole_5min`` /
``apn_5min`` / ``dns_5min`` d'une base réelle. Les valeurs sont **déterministes** (graine = clé + horodatage) :
deux cycles relisant le même créneau obtiennent les mêmes chiffres, comme le ferait une vraie base — les taux,
tendances et tickets sont donc stables.

Anomalies (mêmes noms que ``demo/seed_demo_data.py``) pilotées par ``scenario.json`` dans ``DATA_DIR`` :
``zero`` (trafic nul), ``instant_drop`` (chute ×0,35), ``gradual`` (baisses consécutives), ``loss`` (pertes TCP),
``stale`` (la base cesse d'être alimentée).

CLI : ``python -m core.demo_data config [chemin]`` écrit un ``db_config.json`` complet depuis CATALOG.
"""
from __future__ import annotations

import json
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import parse, settings
from .logger import get_logger

logger = get_logger(__name__)

UNITS_PER_GBPS = 37_500_000          # inverse de constants.DEBIT_FACTOR = 8 / (300 * 1e6)
MODAL_SAMPLE_MINUTES = 15            # pas d'échantillonnage pour la référence saisonnière 7 j
SCENARIO_FILE = settings.data_dir / "scenario.json"

# (catégorie, clé, display_name, site, c_param, débit de base Gb/s) — identique à demo/seed_demo_data.py
CATALOG = [
    ("international", "douala_camtel", "Douala CAMTEL", "Douala", "CAMTEL", 18.0),
    ("international", "yaounde_camtel", "Yaounde CAMTEL", "Yaounde", "CAMTEL", 9.0),
    ("international", "garoua_camtel", "Garoua CAMTEL", "Garoua", "CAMTEL", 3.5),
    ("international", "kribi", "KRIBI", "Douala", "KRIBI", 25.0),
    ("international", "oci_sat3", "OCI-SAT3", "Douala", "OCI_SAT3", 12.0),
    ("international", "wacs_ic", "WACS-IC", "Yaounde", "IC_WACS", 15.0),
    ("international", "wacs_bayobab", "WACS-BAYOBAB", "Douala", "WACS-BAYOBAB", 8.0),
    ("gi", "douala_gi", "Douala Gi", "Douala", "SGi", 30.0),
    ("gi", "yaounde_gi", "Yaounde Gi", "Yaounde", "SGi", 14.0),
    ("gi", "garoua_gi", "Garoua Gi", "Garoua", "SGi", 5.0),
] + [
    ("roaming", f"{city}_gi_roaming_{kind}", f"{city.capitalize()} GI Roaming {kind.upper().replace('_', ' ')}",
     city.capitalize(), f"Roaming_{kind.upper()}", 0.4 + 0.3 * i)
    for i, city in enumerate(("douala", "yaounde", "garoua"))
    for kind in ("in_up_gp", "in_up_s8", "out_up")
]
CATEGORY = {k: c for c, k, *_ in CATALOG}
BASE = {k: g for _, k, _, _, _, g in CATALOG}

TRAFFIC_COLS = ("tcp_dw", "udp_dw", "icmp_dw", "other_dw")


def load_scenarios() -> dict:
    try:
        return json.loads(SCENARIO_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def slot_values(key: str, ts: datetime, scen: dict | None, apn_tdd: bool = False) -> dict | None:
    """Valeurs d'un créneau 5 min (ts en UTC). ``None`` => pas de ligne (base figée). Identique à seed_demo_data."""
    since = datetime.strptime(scen["since"], "%Y-%m-%d %H:%M:%S") if scen else None
    mode = scen["mode"] if scen else None
    active = since is not None and ts >= since
    if active and mode == "stale":
        return None

    rnd = random.Random(f"{key}|{ts:%Y%m%d%H%M}")
    local_h = ((ts + timedelta(hours=1)).hour) + ts.minute / 60          # Africa/Douala = UTC+1
    diurnal = 0.62 + 0.38 * math.sin((local_h - 14) / 24 * 2 * math.pi)  # pic ~20h locale
    weekend = 0.93 if ts.weekday() >= 5 else 1.0
    gbps = BASE[key] * diurnal * weekend * (1 + rnd.uniform(-0.03, 0.03))
    if apn_tdd:
        gbps *= 0.08

    loss_ratio = 0.004 + rnd.uniform(0, 0.002)
    factor = 1.0
    if active:
        if mode == "zero":
            factor = 0.0
        elif mode == "instant_drop":
            factor = 0.35
        elif mode == "gradual":
            k = int((ts - since).total_seconds() // 300) + 1
            factor = 0.75 ** k
        elif mode == "loss":
            loss_ratio = 0.08
    gbps *= factor

    total = int(gbps * UNITS_PER_GBPS)
    sessions = int(220_000 * (gbps / max(BASE[key], 0.1)) * 1.0 + rnd.randint(0, 500)) if total else 0
    pkts = int(total / 1400 * 1.0) if total else 0
    return {
        "tcp_dw": int(total * 0.88), "udp_dw": int(total * 0.10), "icmp_dw": int(total * 0.002),
        "other_dw": total - int(total * 0.88) - int(total * 0.10) - int(total * 0.002),
        "#tcp_loss_dw": int(pkts * loss_ratio), "#tcp_pkt_dw": pkts,
        "#tcp_no_response_to_syn": int(sessions * rnd.uniform(0.015, 0.03)),
        "#tcp_block_wp_up": int(sessions * rnd.uniform(0.005, 0.015)),
        "#tcp_synack_core": int(sessions * 0.9),
        "Irt_tcp_core_td22": int(sessions * 0.9 * rnd.uniform(38, 55)),   # ms * nb de synack
        "#tcp_sessions": sessions,
    }


def dns_values(key: str, ts: datetime) -> dict:
    rnd = random.Random(f"dns|{key}|{ts:%Y%m%d%H%M}")
    q = rnd.randint(40_000, 60_000)
    return {"qtype_MALFORMED": rnd.randint(0, 40), "tooshort_src": rnd.randint(0, 10),
            "queries_woresp": int(q * rnd.uniform(0.005, 0.02)), "queries": q}


def _mode7d(key: str, end_ts: datetime) -> float | None:
    """Référence saisonnière 7 j (même formule que SQL_MODE) : mode pondéré du trafic par créneau."""
    bin_w = settings.mode_bin_gbps
    counts: dict[float, int] = {}
    t = end_ts - timedelta(days=7)
    while t <= end_ts:
        row = slot_values(key, t, None)
        if row is not None:
            gbps = sum(row[c] for c in TRAFFIC_COLS) / UNITS_PER_GBPS
            b = math.floor(gbps / bin_w) * bin_w
            counts[b] = counts.get(b, 0) + 1
        t += timedelta(minutes=MODAL_SAMPLE_MINUTES)
    if not counts:
        return None
    best = max(counts, key=lambda b: (counts[b], -b))
    freq, prev, nxt = counts[best], counts.get(best - bin_w, 0), counts.get(best + bin_w, 0)
    denom = 2 * freq - prev - nxt
    return best + (bin_w / 2 if denom == 0 else ((freq - prev) / denom) * bin_w)


def fake_fields(db_key: str, timestamps: list, latest_alert_ts: datetime, want_monitoring: bool) -> dict:
    """Équivalent en mémoire d'un ``DbSnapshot`` DIAGNET (whole/apn/last_update/mode7d)."""
    scen = load_scenarios().get(db_key)
    category = CATEGORY.get(db_key)
    if category is None:
        raise KeyError(f"clé absente du catalogue de démo : {db_key}")
    whole, apn = {}, {}
    last_ts: datetime | None = None
    for ts_str in timestamps:
        ts = parse(ts_str)                                    # UTC naïf
        row = slot_values(db_key, ts, scen)
        if row is None:
            continue
        row = dict(row)
        dns = dns_values(db_key, ts)                          # alias attendus par le collecteur
        row["dns_malformed_ul"] = dns["qtype_MALFORMED"] + dns["tooshort_src"]
        row["dns_queries_woresp"] = dns["queries_woresp"]
        row["dns_queries"] = dns["queries"]
        whole[ts_str] = row
        if last_ts is None or ts > last_ts:
            last_ts = ts
        if category == "gi":
            apn[ts_str] = slot_values(db_key, ts, scen, apn_tdd=True)
    return {"whole": whole, "apn": apn,
            "last_update": last_ts if want_monitoring else None,
            "mode7d": _mode7d(db_key, latest_alert_ts) if want_monitoring else None}


def write_db_config(path) -> None:
    """Écrit un ``db_config.json`` complet (hôtes fictifs) depuis CATALOG."""
    cfg: dict[str, list] = {"international": [], "gi": [], "roaming": []}
    for cat, key, disp, site, cparam, _ in CATALOG:
        cfg[cat].append({"key": key, "host": "127.0.0.1", "db_name": f"SIM_{key}",
                         "display_name": disp, "site": site, "c_param": cparam})
    Path(path).write_text(json.dumps(cfg, indent=4, ensure_ascii=False), encoding="utf-8")


def set_scenario(key: str | None, mode: str | None, minutes_ago: int = 0) -> dict:
    """Écrit ``scenario.json`` : ``mode=None`` efface (``key=None`` efface tout). Retourne le scénario résultant."""
    scen = load_scenarios()
    if mode is None:
        scen.pop(key, None) if key else scen.clear()
    else:
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None, second=0, microsecond=0)   # DIAGNET = UTC
        since = now_utc - timedelta(minutes=minutes_ago)
        since = since.replace(minute=since.minute - since.minute % 5)
        scen[key] = {"mode": mode, "since": since.strftime("%Y-%m-%d %H:%M:%S")}
    SCENARIO_FILE.parent.mkdir(parents=True, exist_ok=True)
    SCENARIO_FILE.write_text(json.dumps(scen, indent=2), encoding="utf-8")
    return scen


def _main() -> None:
    import argparse
    p = argparse.ArgumentParser(description="Catalogue et scénarios de la démo DIAGNET simulée.")
    sp = p.add_subparsers(dest="cmd", required=True)
    c = sp.add_parser("config", help="écrit db_config.json depuis CATALOG")
    c.add_argument("path", nargs="?", default=str(Path(__file__).resolve().parent.parent / "db_config.json"))
    s = sp.add_parser("scenario", help="pilote une anomalie sur une base")
    s.add_argument("action", choices=["zero", "instant_drop", "gradual", "loss", "stale", "clear"])
    s.add_argument("key", nargs="?")
    s.add_argument("--minutes-ago", type=int, default=0)
    a = p.parse_args()
    if a.cmd == "config":
        write_db_config(a.path)
        print(f"[demo] catalogue simulé écrit : {a.path}")
    else:
        print(f"[demo] scenario.json : {set_scenario(a.key, None if a.action == 'clear' else a.action, a.minutes_ago)}")


if __name__ == "__main__":
    _main()
