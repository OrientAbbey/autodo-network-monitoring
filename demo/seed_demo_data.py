#!/usr/bin/env python3
"""
Source de données SIMULÉE pour la démo AUTODO (aucune donnée réelle).

Crée, dans un serveur MySQL/MariaDB local :
  * une base `SIM_<clé>` par entrée du catalogue (international / gi / roaming),
    avec les tables lues par l'application : whole_5min, apn_5min, dns_5min ;
  * la base locale `tickets_db` et les utilisateurs applicatifs ;
  * un fichier `db_config.json` pointant vers ces bases (host = 127.0.0.1).

Sous-commandes
  seed      crée le schéma et remplit N jours d'historique (UTC, pas de 5 min)
  follow    ajoute en continu les créneaux de 5 min manquants (flux « live »)
  scenario  injecte / retire une anomalie sur une base (fichier scenario.json)
  config    écrit db_config.json

Scénarios d'anomalie (appliqués aux créneaux >= `since`, en UTC)
  zero          trafic nul                       -> ticket traffic cat.1
  instant_drop  chute instantanée (x0.35)        -> ticket traffic cat.2
  gradual       baisses consécutives (x0.75 / pas)-> ticket traffic cat.3
  loss          pertes TCP à 8 %                 -> ticket pertes
  stale         la base cesse d'être alimentée   -> ticket db_down

Variables d'environnement (valeurs par défaut entre parenthèses)
  DEMO_DB_SOCKET (/run/mysqld/mysqld.sock) ou DEMO_DB_HOST/DEMO_DB_PORT
  DEMO_DB_ADMIN_USER (root)  DEMO_DB_ADMIN_PASS ("")
  DEMO_DIR (dossier de ce script)  -> scenario.json, db_config.json
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pymysql

DEMO_DIR = Path(os.getenv("DEMO_DIR", Path(__file__).resolve().parent))
SCENARIO_FILE = DEMO_DIR / "scenario.json"
UNITS_PER_GBPS = 37_500_000  # inverse de DEBIT_FACTOR = 8 / (300 * 1e6)

# (catégorie, clé, display_name, site, c_param, débit de base Gb/s)
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
BASE = {k: g for _, k, _, _, _, g in CATALOG}

METRIC_COLS = [
    ("tcp_dw", "BIGINT"), ("udp_dw", "BIGINT"), ("icmp_dw", "BIGINT"), ("other_dw", "BIGINT"),
    ("`#tcp_loss_dw`", "BIGINT"), ("`#tcp_pkt_dw`", "BIGINT"),
    ("`#tcp_no_response_to_syn`", "BIGINT"), ("`#tcp_block_wp_up`", "BIGINT"),
    ("`#tcp_synack_core`", "BIGINT"), ("Irt_tcp_core_td22", "BIGINT"), ("`#tcp_sessions`", "BIGINT"),
]
COL_NAMES = ", ".join(c for c, _ in METRIC_COLS)
COL_DDL = ",\n  ".join(f"{c} {t} NULL" for c, t in METRIC_COLS)


# ----------------------------------------------------------------------------
# Connexions
# ----------------------------------------------------------------------------
def admin_conn(database: str | None = None):
    kw = dict(user=os.getenv("DEMO_DB_ADMIN_USER", "root"), password=os.getenv("DEMO_DB_ADMIN_PASS", ""), autocommit=False)
    sock = os.getenv("DEMO_DB_SOCKET", "/run/mysqld/mysqld.sock")
    if not os.getenv("DEMO_DB_HOST") and os.path.exists(sock):
        kw["unix_socket"] = sock
    else:
        kw.update(host=os.getenv("DEMO_DB_HOST", "127.0.0.1"), port=int(os.getenv("DEMO_DB_PORT", "3306")))
    if database:
        kw["database"] = database
    return pymysql.connect(**kw)


def db_name(key: str) -> str:
    return f"SIM_{key}"


# ----------------------------------------------------------------------------
# Génération des valeurs
# ----------------------------------------------------------------------------
def floor5(dt: datetime) -> datetime:
    return dt.replace(minute=dt.minute - dt.minute % 5, second=0, microsecond=0)


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def load_scenarios() -> dict:
    try:
        return json.loads(SCENARIO_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def slot_values(key: str, ts: datetime, scen: dict | None, apn_tdd: bool = False) -> dict | None:
    """Valeurs d'un créneau 5 min (ts en UTC). None => pas de ligne (base figée)."""
    since = datetime.strptime(scen["since"], "%Y-%m-%d %H:%M:%S") if scen else None
    mode = scen["mode"] if scen else None
    active = since is not None and ts >= since
    if active and mode == "stale":
        return None

    rnd = random.Random(f"{key}|{ts:%Y%m%d%H%M}")
    local_h = ((ts + timedelta(hours=1)).hour) + ts.minute / 60  # Africa/Douala = UTC+1
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
        "Irt_tcp_core_td22": int(sessions * 0.9 * rnd.uniform(38, 55)),  # ms * nb de synack
        "#tcp_sessions": sessions,
    }


def dns_values(key: str, ts: datetime) -> dict:
    rnd = random.Random(f"dns|{key}|{ts:%Y%m%d%H%M}")
    q = rnd.randint(40_000, 60_000)
    return {"qtype_MALFORMED": rnd.randint(0, 40), "tooshort_src": rnd.randint(0, 10),
            "queries_woresp": int(q * rnd.uniform(0.005, 0.02)), "queries": q}


# ----------------------------------------------------------------------------
# Schéma + insertion
# ----------------------------------------------------------------------------
def create_schema(cur, key: str, category: str):
    db = db_name(key)
    cur.execute(f"CREATE DATABASE IF NOT EXISTS `{db}`")
    cur.execute(f"USE `{db}`")
    cur.execute(f"CREATE TABLE IF NOT EXISTS whole_5min (SAUV_td32 DATETIME NOT NULL PRIMARY KEY,\n  {COL_DDL})")
    cur.execute(f"CREATE TABLE IF NOT EXISTS apn_5min (SAUV_td32 DATETIME NOT NULL, apn VARCHAR(64) NOT NULL,\n"
                f"  {COL_DDL}, PRIMARY KEY (SAUV_td32, apn))")
    cur.execute("CREATE TABLE IF NOT EXISTS dns_5min (SAUV_td32 DATETIME NOT NULL, ip VARCHAR(64) NOT NULL,"
                " supra VARCHAR(16) NOT NULL, qtype_MALFORMED INT, tooshort_src INT, queries_woresp INT, queries INT,"
                " PRIMARY KEY (SAUV_td32, ip, supra))")


def insert_slots(cur, key: str, category: str, slots: list[datetime], scen: dict | None):
    db = db_name(key)
    cur.execute(f"USE `{db}`")
    w, a, d = [], [], []
    ph = ", ".join(["%s"] * len(METRIC_COLS))
    for ts in slots:
        v = slot_values(key, ts, scen)
        if v is None:
            continue
        w.append((ts, *v.values()))
        if category == "gi":
            t = slot_values(key, ts, scen, apn_tdd=True)
            a.append((ts, "internet", *v.values()))
            a.append((ts, "tdd.apn", *t.values()))
        dv = dns_values(key, ts)
        d.append((ts, "8.8.8.0/24", "*", *dv.values()))
    if w:
        cur.executemany(f"INSERT IGNORE INTO whole_5min (SAUV_td32, {COL_NAMES}) VALUES (%s, {ph})", w)
    if a:
        cur.executemany(f"INSERT IGNORE INTO apn_5min (SAUV_td32, apn, {COL_NAMES}) VALUES (%s, %s, {ph})", a)
    if d:
        cur.executemany("INSERT IGNORE INTO dns_5min (SAUV_td32, ip, supra, qtype_MALFORMED, tooshort_src,"
                        " queries_woresp, queries) VALUES (%s,%s,%s,%s,%s,%s,%s)", d)


def write_config(path: Path):
    cfg: dict[str, list] = {"international": [], "gi": [], "roaming": []}
    for cat, key, disp, site, cparam, _ in CATALOG:
        cfg[cat].append({"key": key, "host": "127.0.0.1", "db_name": db_name(key),
                         "display_name": disp, "site": site, "c_param": cparam})
    path.write_text(json.dumps(cfg, indent=4, ensure_ascii=False))
    print(f"[config] {path}")


# ----------------------------------------------------------------------------
# Commandes
# ----------------------------------------------------------------------------
def cmd_seed(a):
    conn = admin_conn()
    cur = conn.cursor()
    app_user = os.getenv("DEMO_APP_DB_USER", "u"), os.getenv("DEMO_APP_DB_PASS", "p")
    diag_user = os.getenv("DEMO_DIAG_USER", "diag"), os.getenv("DEMO_DIAG_PASS", "diagpass")
    cur.execute("CREATE DATABASE IF NOT EXISTS tickets_db")
    for host in ("127.0.0.1", "localhost", "%"):
        cur.execute(f"CREATE USER IF NOT EXISTS '{app_user[0]}'@'{host}' IDENTIFIED BY '{app_user[1]}'")
        cur.execute(f"GRANT ALL ON tickets_db.* TO '{app_user[0]}'@'{host}'")
        cur.execute(f"CREATE USER IF NOT EXISTS '{diag_user[0]}'@'{host}' IDENTIFIED BY '{diag_user[1]}'")
    scen = load_scenarios()
    end = floor5(utc_now())
    start = end - timedelta(days=a.days)
    n = int((end - start).total_seconds() // 300) + 1
    slots = [start + timedelta(minutes=5 * i) for i in range(n)]
    t0 = time.time()
    for cat, key, *_ in CATALOG:
        create_schema(cur, key, cat)
        for host in ("127.0.0.1", "localhost", "%"):
            cur.execute(f"GRANT SELECT ON `{db_name(key)}`.* TO '{diag_user[0]}'@'{host}'")
        if a.reset:
            cur.execute(f"USE `{db_name(key)}`")
            for t in ("whole_5min", "apn_5min", "dns_5min"):
                cur.execute(f"TRUNCATE {t}")
        for i in range(0, len(slots), 2000):
            insert_slots(cur, key, cat, slots[i:i + 2000], scen.get(key))
        conn.commit()
    if a.reset:
        cur.execute("USE tickets_db")
        for t in ("tickets_tb", "daily_users", "logs", "ticket_counters", "notification_state", "schema_version"):
            cur.execute(f"DROP TABLE IF EXISTS {t}")
    cur.execute("FLUSH PRIVILEGES")
    conn.commit()
    write_config(DEMO_DIR / "db_config.json")
    print(f"[seed] {len(CATALOG)} bases, {n} créneaux chacun, jusqu'à {end} UTC ({time.time() - t0:.1f}s)")


def cmd_follow(a):
    print("[follow] alimentation continue (Ctrl+C pour arrêter)")
    conn = admin_conn()
    cur = conn.cursor()
    while True:
        scen = load_scenarios()
        now = floor5(utc_now())
        for cat, key, *_ in CATALOG:
            cur.execute(f"SELECT MAX(SAUV_td32) FROM `{db_name(key)}`.whole_5min")
            last = cur.fetchone()[0] or now - timedelta(minutes=5)
            s = scen.get(key)
            if s and s["mode"] == "stale" and datetime.strptime(s["since"], "%Y-%m-%d %H:%M:%S") <= last:
                continue
            missing = []
            t = last + timedelta(minutes=5)
            while t <= now:
                missing.append(t)
                t += timedelta(minutes=5)
            if missing:
                insert_slots(cur, key, cat, missing, s)
        conn.commit()
        if a.once:
            return
        time.sleep(a.interval)


def cmd_scenario(a):
    scen = load_scenarios()
    if a.action == "clear":
        scen.pop(a.key, None) if a.key else scen.clear()
    else:
        since = floor5(utc_now()) - timedelta(minutes=a.minutes_ago)
        scen[a.key] = {"mode": a.action, "since": since.strftime("%Y-%m-%d %H:%M:%S")}
    SCENARIO_FILE.write_text(json.dumps(scen, indent=2))
    print(f"[scenario] {SCENARIO_FILE}: {scen}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("seed"); s.add_argument("--days", type=int, default=8); s.add_argument("--reset", action="store_true")
    s.set_defaults(fn=cmd_seed)
    f = sp.add_parser("follow"); f.add_argument("--interval", type=int, default=15); f.add_argument("--once", action="store_true")
    f.set_defaults(fn=cmd_follow)
    c = sp.add_parser("scenario")
    c.add_argument("action", choices=["zero", "instant_drop", "gradual", "loss", "stale", "clear"])
    c.add_argument("key", nargs="?")
    c.add_argument("--minutes-ago", type=int, default=0, help="début de l'anomalie (défaut: créneau courant)")
    c.set_defaults(fn=cmd_scenario)
    g = sp.add_parser("config"); g.set_defaults(fn=lambda a: write_config(DEMO_DIR / "db_config.json"))
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
