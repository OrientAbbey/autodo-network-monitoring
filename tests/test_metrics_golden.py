"""Test « golden » : la sortie de ``metrics._process_data_for_interval`` doit rester STRICTEMENT identique
à celle de l'implémentation d'origine (copier-coller par catégorie) après sa refonte pilotée par table.
Régénérer ``tests/golden_dashboard.json`` uniquement si le format du tableau de bord change volontairement."""
import json
import random
from pathlib import Path

from core import constants, metrics

GOLDEN = Path(__file__).with_name("golden_dashboard.json")
COLS = ("tcp_dw", "udp_dw", "icmp_dw", "other_dw", "#tcp_loss_dw", "#tcp_pkt_dw", "#tcp_no_response_to_syn", "#tcp_block_wp_up",
        "#tcp_synack_core", "Irt_tcp_core_td22", "#tcp_sessions", "dns_malformed_ul", "dns_queries_woresp", "dns_queries")
CPS = {"5min": {"start_str": "s5", "end_str": "e5"}, "1day": {"start_str": "s1d", "end_str": "e1d"},
       "1week": {"start_str": "s1w", "end_str": "e1w"}}


def _row(rnd, kind="normal"):
    if kind == "empty":
        return {c: None for c in COLS}
    r = {c: float(rnd.randint(0, 4_000_000_000 if c.endswith("_dw") else 90_000)) for c in COLS}
    r["#tcp_pkt_dw"] = float(rnd.randint(1, 900_000))
    r["#tcp_loss_dw"] = float(rnd.randint(0, 40_000))
    if kind == "partial":
        r["udp_dw"] = None
        r["dns_queries"] = None
    if kind == "zero":
        r = {c: 0.0 for c in COLS}
    return r


def sample_inputs():
    rnd = random.Random(2026)
    keys = {"kribi": "normal", "wacs_ic": "partial", "douala_gi": "normal", "garoua_gi": "normal", "yaounde_gi": "zero",
            "douala_gi_roaming_out_up": "normal", "empty_db": "empty"}
    data = {k: {ts: _row(rnd, kind) for p in CPS.values() for ts in (p["start_str"], p["end_str"])} for k, kind in keys.items()}
    data["dead"] = {ts: None for p in CPS.values() for ts in (p["start_str"], p["end_str"])}
    tdd = {"douala_gi": {ts: _row(rnd) for p in CPS.values() for ts in (p["start_str"], p["end_str"])},
           "yaounde_gi": {ts: _row(rnd, "zero") for p in CPS.values() for ts in (p["start_str"], p["end_str"])}}   # garoua_gi : pas de TDD
    return data, tdd


def run_all():
    labels = {"international": {"kribi": "KRIBI", "wacs_ic": "WACS-IC", "empty_db": "E", "dead": "D"},
              "utilisateur": {"douala_gi": "Douala_gi", "garoua_gi": "Garoua_gi", "yaounde_gi": "Yaounde_gi"},
              "roaming": {"douala_gi_roaming_out_up": "R"}}
    old = constants.LABELS
    constants.LABELS = labels
    try:
        data, tdd = sample_inputs()
        out = {}
        for interval in CPS:
            recap, tdd_recap, roaming, inacc = {i: {} for i in CPS}, {i: {} for i in CPS}, {i: {} for i in CPS}, []
            metrics._process_data_for_interval(data, interval, CPS, recap, tdd_recap, roaming, inacc, tdd)
            out[interval] = {"recap": recap[interval], "tdd": tdd_recap[interval], "roaming": roaming[interval], "inaccessible": inacc}
        return json.loads(json.dumps(out))
    finally:
        constants.LABELS = old


def test_interval_processing_matches_the_golden_output():
    assert run_all() == json.loads(GOLDEN.read_text())
