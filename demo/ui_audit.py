#!/usr/bin/env python3
"""Audit mesuré de l'interface (CHANTIER_UI.md §6) : polices, boutons, en-tête, densité, contrastes sur pixels réels, cibles tactiles,
animations continues, colonnes coupées, repérage des alertes dans les lignes, partage des jetons CSS. Sortie : audit.json + captures.

Usage (la pile simulée est lancée par le script d'amorçage ; chemin ABSOLU du script, car il se place dans demo/) :
    TEST_DB_HOST=127.0.0.1 demo/sandbox_stack.sh "$PWD" python3 "$PWD/demo/ui_audit.py" [--out demo/screenshots/audit]
"""
import argparse
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests
from PIL import Image
from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parent.parent
DEMO = REPO / "demo"
BASE = "http://127.0.0.1:8501"
ADMIN, FIRST_PASSWORD, PASSWORD = "admin", "Admin-Initial-1", "Audit-Passw0rd1"

# ----------------------------------------------------------------------------- mesures exécutées dans la page
PAGE_METRICS = """(minTap) => {
  const vis = e => { const r = e.getBoundingClientRect(), cs = getComputedStyle(e); return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  const out = {fonts: {}, buttons: {}, offSystem: [], smallTargets: [], animations: [], clipped: [], header: null, rows: null};
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const t = walker.currentNode, e = t.parentElement;
    if (!t.textContent.trim() || ['SCRIPT', 'STYLE', 'TITLE', 'OPTION'].includes(e.tagName) || !vis(e)) continue;
    const size = Math.round(parseFloat(getComputedStyle(e).fontSize) * 10) / 10; out.fonts[size] = (out.fonts[size] || 0) + 1;
  }
  const interactive = 'button, a[href], input:not([type=hidden]), select, textarea, [role=button], [role=tab], summary';
  document.querySelectorAll(interactive).forEach(e => {
    if (!vis(e) || e.closest('dialog:not([open])')) return;
    const r = e.getBoundingClientRect(), cs = getComputedStyle(e);
    if (r.right <= 0 || r.left >= innerWidth) return;            // hors écran (volet latéral fermé) : pas une cible atteignable
    if (e.matches('button, a.btn-primary, a.btn-secondary, a.btn-danger, a.btn-ghost, .btn')) {
      const sig = [cs.backgroundColor, cs.borderTopColor, cs.borderTopWidth, cs.borderRadius, Math.round(parseFloat(cs.fontSize)), cs.paddingTop + '/' + cs.paddingLeft].join('|');
      out.buttons[sig] = (out.buttons[sig] || 0) + 1;
      if (!e.matches('.btn, .btn-primary, .btn-secondary, .btn-danger, .btn-ghost, .status-strip, .tab-btn, .kit-tab, .menu-item')) out.offSystem.push((e.className || e.tagName).toString().slice(0, 40));
    }
    if (e.type !== 'checkbox' && e.type !== 'radio' && (r.height < minTap || r.width < minTap) && r.top < innerHeight * 3) out.smallTargets.push(((e.textContent || '').trim() || e.getAttribute('aria-label') || e.id || e.tagName).slice(0, 22) + ` ${Math.round(r.width)}x${Math.round(r.height)}`);
  });
  document.querySelectorAll('*').forEach(e => { const cs = getComputedStyle(e); if (cs.animationName !== 'none' && cs.animationIterationCount === 'infinite' && vis(e)) out.animations.push((e.className || e.tagName).toString().slice(0, 30)); });
  document.querySelectorAll('.table-wrapper, .table-scroll').forEach(w => { if (vis(w) && w.scrollWidth > w.clientWidth + 2) out.clipped.push({id: w.id || w.className.toString().slice(0, 24), cue: !!w.closest('[data-scrollable]') || w.hasAttribute('data-scrollable')}); });
  const header = document.querySelector('.app-bar, .header');
  if (header) {
    const bar = header.querySelector('.app-bar__actions, .controls, .header-tools') || header;
    const kids = [...bar.children].filter(vis);
    const hb = header.getBoundingClientRect(), vk = [...header.querySelectorAll('.app-bar__brand, .app-bar__center, .app-bar__toggle, .app-bar__actions, .app-nav')].filter(vis).map(k => k.getBoundingClientRect());
    const overlap = vk.some((a, i) => vk.some((b, j) => j > i && a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1 && b.top < a.bottom - 1));
    out.header = {height: Math.round(hb.height), overflow: overlap || header.scrollWidth > header.clientWidth + 1, controls: kids.length, controlHeights: [...new Set(kids.map(k => Math.round(k.getBoundingClientRect().height)))]};
  }
  const dataRows = [...document.querySelectorAll('tr[data-db]')].filter(vis);
  if (dataRows.length) {
    const heights = dataRows.map(r => Math.round(r.getBoundingClientRect().height)).sort((a, b) => a - b);
    out.rows = {count: dataRows.length, medianHeight: heights[Math.floor(heights.length / 2)], firstSectionVisible: (() => { const f = document.querySelector('#sections-grid > .data-section'); return !!f && f.getBoundingClientRect().bottom <= innerHeight; })(), visibleInViewport: dataRows.filter(r => r.getBoundingClientRect().bottom <= innerHeight && r.getBoundingClientRect().top >= 0).length,
      flagged: [...new Set(dataRows.filter(r => r.dataset.alert && r.dataset.alert !== 'none').map(r => r.dataset.db))]};
  }
  out.docHeight = document.documentElement.scrollHeight; out.docWidth = document.documentElement.scrollWidth; out.viewport = innerWidth;
  return out; }"""

TEXT_NODES = """() => { const out = [], w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (w.nextNode()) { const t = w.currentNode, e = t.parentElement, txt = t.textContent.trim();
    if (!txt || ['SCRIPT', 'STYLE', 'TITLE', 'OPTION'].includes(e.tagName) || e.closest('[aria-hidden="true"], .sr-only')) continue;
    const range = document.createRange(); range.selectNodeContents(t); const r = range.getBoundingClientRect(), cs = getComputedStyle(e);
    if (r.width < 1 || r.height < 1 || cs.visibility === 'hidden' || cs.display === 'none' || r.bottom < 0 || r.top > innerHeight || r.right < 0 || r.left > innerWidth) continue;
    out.push({text: txt.slice(0, 34), color: cs.color, size: parseFloat(cs.fontSize), weight: parseInt(cs.fontWeight), x: r.left + r.width / 2, y: r.top + r.height / 2, cls: (e.className.toString() || e.tagName).slice(0, 28)}); }
  return out; }"""


def luminance(rgb):
    c = [v / 255 for v in rgb]
    c = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def contrast(a, b):
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def contrast_failures(page):
    """Textes visibles sous WCAG AA (4,5:1 ; 3:1 pour le grand texte). Le fond est LU sur une seconde capture sans texte, au centre de
    l'élément (dégradés, teintes de ligne et superpositions compris)."""
    texts = page.evaluate(TEXT_NODES)
    page.add_style_tag(content="* { color: transparent !important; -webkit-text-fill-color: transparent !important; text-shadow: none !important; }")
    background = Image.open(io.BytesIO(page.screenshot())).convert("RGB")
    groups = {}
    checked = 0
    for t in texts:
        match = re.match(r"rgba?\(([^)]+)\)", t["color"])
        x, y = int(max(0, min(t["x"], background.width - 1))), int(max(0, min(t["y"], background.height - 1)))
        ratio = contrast([float(v) for v in match.group(1).split(",")[:3]], background.getpixel((x, y)))
        checked += 1
        large = t["size"] >= 24 or (t["size"] >= 18.66 and t["weight"] >= 700)
        if ratio < (3 if large else 4.5):
            g = groups.setdefault(t["cls"], {"worst": ratio, "n": 0, "example": t["text"], "size": t["size"]})
            g["n"] += 1
            g["worst"] = min(g["worst"], ratio)
    worst = sorted(({"class": k, "ratio": round(v["worst"], 2), "count": v["n"], "size": v["size"], "example": v["example"]} for k, v in groups.items()), key=lambda d: d["ratio"])
    return {"checked": checked, "failing": sum(v["n"] for v in groups.values()), "worst": worst[:8]}


def stylesheet_stats():
    """Partage des jetons et couleurs littérales hors tokens.css (analyse statique des sources)."""
    hex_re = re.compile(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b")
    sources = {p.name: p.read_text(encoding="utf-8") for p in sorted((REPO / "static").rglob("*.css"))}
    tickets = (REPO / "templates" / "tickets.html").read_text(encoding="utf-8")
    inline = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", tickets, re.S))
    if inline.strip():
        sources["tickets.html <style>"] = inline
    tokens_file = next((v for k, v in sources.items() if k == "tokens.css"), "")
    token_names = set(re.findall(r"(--[a-z0-9-]+)\s*:", tokens_file))
    consumers = {k: bool(token_names) and any(f"var({n}" in v for n in token_names) for k, v in sources.items() if k != "tokens.css"}
    outside = {k: len({h.lower() for h in hex_re.findall(v)}) for k, v in sources.items() if k != "tokens.css"}
    return {"files": list(sources), "consumers_of_tokens": consumers, "files_sharing_tokens": f"{sum(consumers.values())}/{len(consumers)}",
            "hex_literals_outside_tokens": len(set().union(*[{h.lower() for h in hex_re.findall(v)} for k, v in sources.items() if k != "tokens.css"])), "hex_by_file": outside}


# ----------------------------------------------------------------------------- pile de démonstration
def seed():
    def run(*a):
        subprocess.run([sys.executable, str(DEMO / "seed_demo_data.py"), *a], check=True, cwd=DEMO, capture_output=True)
    for args in (("clear",), ("zero", "douala_camtel", "--minutes-ago", "5"), ("instant_drop", "kribi"), ("gradual", "oci_sat3", "--minutes-ago", "25"),
                 ("loss", "wacs_ic", "--minutes-ago", "30"), ("stale", "garoua_camtel", "--minutes-ago", "90")):
        run("scenario", *args)
    run("seed", "--reset")
    (REPO / "db_config.json").write_text((DEMO / "db_config.json").read_text())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(DEMO / "screenshots" / "audit"))
    out = Path(parser.parse_args().out)
    out.mkdir(parents=True, exist_ok=True)
    seed()
    subprocess.run(["mysql", "-h127.0.0.1", "-uu", "-pp", "tickets_db", "-e", "DELETE FROM users; DELETE FROM alert_acks;"], capture_output=True)
    follow = subprocess.Popen([sys.executable, str(DEMO / "seed_demo_data.py"), "follow", "--interval", "10"], cwd=DEMO)
    app = subprocess.Popen([sys.executable, "main.py"], cwd=REPO, env={**os.environ, "DATA_DIR": tempfile.mkdtemp()}, stdout=open("/tmp/ui_audit_app.out", "w"), stderr=subprocess.STDOUT)
    report = {"pages": {}, "contrast": {}}
    try:
        for _ in range(60):
            try:
                if requests.get(BASE + "/health", timeout=2).status_code == 200:
                    break
            except requests.RequestException:
                pass
            time.sleep(1)
        time.sleep(40)                                               # 1er cycle terminé
        api = requests.Session()
        api.post(BASE + "/api/login", json={"username": ADMIN, "password": FIRST_PASSWORD})
        api.post(BASE + "/api/me/password", json={"current_password": FIRST_PASSWORD, "new_password": PASSWORD})
        api.post(BASE + "/api/admin/history/backfill", json={"days": 6})
        time.sleep(8)
        alerts = requests.get(BASE + "/api/dashboard_data?status=open&period=month").json()["alertes"]
        open_alerts = [a for kind in ("traffic", "pertes") for a in alerts.get(kind, [])]
        report["open_alerts"] = len(open_alerts)
        report["open_alert_bases"] = sorted({a.get("db") for a in open_alerts if a.get("db")})
        ids = [a["id"] for a in open_alerts]
        with sync_playwright() as p:
            browser = p.chromium.launch()

            def visit(label, path, width, height, wait_selector, theme="dark", login=False, mobile=False, full=False):
                ctx = browser.new_context(viewport={"width": width, "height": height}, is_mobile=mobile, has_touch=mobile)
                ctx.add_init_script(f"localStorage.setItem('autodo.prefs', JSON.stringify({{theme: '{theme}', lang: 'fr'}}))")
                page = ctx.new_page()
                page.request.post(BASE + "/api/ack", data={"ticket_ids": ids})
                if login:
                    page.request.post(BASE + "/api/login", data={"username": ADMIN, "password": PASSWORD})
                page.goto(BASE + path)
                page.wait_for_selector(wait_selector, state="attached", timeout=30000)
                page.wait_for_timeout(2200)
                page.screenshot(path=str(out / f"{label}.png"), full_page=full)
                report["pages"][label] = page.evaluate(PAGE_METRICS, 44 if mobile else 32)
                report["contrast"][label] = contrast_failures(page)
                ctx.close()

            visit("dashboard_1440_dark", "/", 1440, 900, "tr[data-db]", full=True)
            visit("dashboard_1440_light", "/", 1440, 900, "tr[data-db]", theme="light")
            visit("dashboard_768_dark", "/", 768, 1024, "tr[data-db]")
            visit("dashboard_1024_dark", "/", 1024, 768, "tr[data-db]")
            visit("dashboard_390_dark", "/", 390, 844, "tr[data-db]", mobile=True)
            visit("tickets_1440_dark", "/tickets/", 1440, 900, ".ticket-card", login=True)
            visit("tickets_1440_light", "/tickets/", 1440, 900, ".ticket-card", theme="light", login=True)
            visit("supervision_1440_dark", "/supervision", 1440, 900, "#tab-params", login=True)
            visit("admin_1440_dark", "/admin", 1440, 900, "#tab-usage", login=True)
            visit("tickets_390_dark", "/tickets/", 390, 844, ".ticket-card", login=True, mobile=True)
            visit("supervision_390_dark", "/supervision", 390, 844, "#tab-params", login=True, mobile=True)
            visit("admin_390_dark", "/admin", 390, 844, "#tab-usage", login=True, mobile=True)
            browser.close()
    finally:
        app.terminate()
        follow.terminate()
        (REPO / "db_config.json").unlink(missing_ok=True)

    d = report["pages"]["dashboard_1440_dark"]
    summary = {
        "C1_fonts_dashboard": len(d["fonts"]), "C2_button_styles_dashboard": len(d["buttons"]), "C2_buttons_off_system_dashboard": sorted(set(d["offSystem"])), "C2_button_styles_tickets": len(report["pages"]["tickets_1440_dark"]["buttons"]),
        "C3_header_controls": d["header"]["controls"] if d["header"] else None, "C3_header_height": d["header"]["height"] if d["header"] else None,
        "C3_header_overflow": {k: v["header"]["overflow"] for k, v in report["pages"].items() if v["header"]},
        "C3_header_height_mobile": (report["pages"]["dashboard_390_dark"]["header"] or {}).get("height"),
        "C4_row_height": d["rows"]["medianHeight"] if d["rows"] else None, "C4_first_section_fully_visible_900": d["rows"]["firstSectionVisible"] if d["rows"] else None, "C4_rows_visible": d["rows"]["visibleInViewport"] if d["rows"] else None,
        "C5_page_height": d["docHeight"],
        "C6_contrast_failures": {k: f"{v['failing']}/{v['checked']}" for k, v in report["contrast"].items()},
        "C7_alert_bases_flagged": f"{len(d['rows']['flagged']) if d['rows'] else 0}/{len(report['open_alert_bases'])}",
        "C8_continuous_animations": {k: len(v["animations"]) for k, v in report["pages"].items() if v["animations"]},
        "C9_small_targets_390": len(report["pages"]["dashboard_390_dark"]["smallTargets"]),
        "C9_small_targets_1440_dashboard": len(d["smallTargets"]), "C9_small_targets_1440_tickets": len(report["pages"]["tickets_1440_dark"]["smallTargets"]),
        "C10_clipped_without_cue_768": [c for c in report["pages"]["dashboard_768_dark"]["clipped"] if not c["cue"]],
        "C11": stylesheet_stats(),
    }
    report["summary"] = summary
    (out / "audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
