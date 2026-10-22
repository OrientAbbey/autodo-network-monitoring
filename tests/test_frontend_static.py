"""Contrôles statiques du front : syntaxe JS, traductions, CSP (aucun inline), collisions de noms globaux, rendu des pages."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core.config import ConfigParams

ROOT = Path(__file__).resolve().parent.parent
JS_DIR = ROOT / "static" / "js"
TEMPLATES = ROOT / "templates"
NEW_FILES = ["common", "prefs", "session", "sound", "a11y", "ui", "wall", "sparklines", "mobile-cards", "page-kit", "supervision", "admin",
             "ticket-actions", "param-labels"]
PAGE_SCRIPTS = {
    "dashboard.html": ["common", "i18n", "prefs", "session", "sound", "a11y", "ui", "state", "alerts", "live", "sections", "theme-filters", "render", "sparklines", "mobile-cards", "wall", "main"],
    "base_app.html": ["common", "i18n", "prefs", "session", "sound", "a11y", "ui", "param-labels", "page-kit"],
    "tickets.html": ["common", "i18n", "prefs", "session", "sound", "a11y", "ui", "page-kit", "ticket-actions"],
}


def script_tags(template: str) -> list[str]:
    return [Path(src).stem for src in re.findall(r'<script src="/static/(?:js/)?([^"]+)"', (TEMPLATES / template).read_text(encoding="utf-8"))]


@pytest.mark.skipif(shutil.which("node") is None, reason="node absent")
def test_every_javascript_file_parses():
    files = sorted(JS_DIR.glob("*.js")) + [ROOT / "static" / "tickets.js"]
    for path in files:
        result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, f"{path.name}: {result.stderr[:300]}"


def used_translation_keys() -> set[str]:
    keys: set[str] = set()
    for path in [*JS_DIR.glob("*.js"), ROOT / "static" / "tickets.js"]:
        if path.name != "i18n.js":
            keys |= set(re.findall(r"""\bt\(\s*['"`]([a-z_0-9.]+)['"`]""", path.read_text(encoding="utf-8")))
    for path in TEMPLATES.glob("*.html"):
        source = path.read_text(encoding="utf-8")
        keys |= set(re.findall(r'data-i18n="([a-z_0-9.]+)"', source))
        for attribute in re.findall(r'data-i18n-attr="([^"]+)"', source):
            keys |= {pair.split(":")[1] for pair in attribute.split(";")}
        keys |= set(re.findall(r"\('[a-z_]+', '([a-z]+\.[a-z_0-9]+)', ", source))                       # tuples Jinja des filtres
    keys |= {f"usage.{p}" for p in ("today", "7days", "1month", "3months")} | {"page.supervision", "page.admin"}
    keys |= {f"group.{g}" for g in ("traffic", "losses", "tcp", "timing", "recovery", "sections", "governance")}
    keys |= {f"sect.{s}" for s in ("international", "gi", "international_gi", "roaming", "tdd")} | {"role.supervision", "role.admin"}
    from core.ticket_log import KINDS
    return keys | {f"ev.{kind}" for kind in KINDS}


def dictionary(language: str) -> set[str]:
    source = (JS_DIR / "i18n.js").read_text(encoding="utf-8")
    block = source[source.index(f"    {language}: {{"):]
    block = block[:block.index("\n    }")] if language == "fr" else block[:block.index("\n    }\n};")]
    return set(re.findall(r"'([a-z_0-9]+\.[a-z_0-9]+)':", block))


def test_every_used_translation_key_exists_in_both_languages():
    used = used_translation_keys()
    for language in ("fr", "en"):
        assert not (used - dictionary(language)), f"clés absentes en {language}: {sorted(used - dictionary(language))}"


def test_french_and_english_dictionaries_have_the_same_keys():
    assert dictionary("fr") == dictionary("en")


def test_every_config_parameter_has_a_label_in_both_languages():
    source = (JS_DIR / "param-labels.js").read_text(encoding="utf-8")
    declared = set(re.findall(r"^    ([a-z_0-9]+): \{ group:", source, re.M))
    assert set(ConfigParams.model_fields) <= declared, sorted(set(ConfigParams.model_fields) - declared)
    for name in declared:
        block = source[source.index(f"    {name}: {{"):][:900]
        assert "fr:" in block and "en:" in block


@pytest.mark.parametrize("template", sorted(p.name for p in TEMPLATES.glob("*.html")))
def test_templates_have_no_inline_handlers_or_inline_scripts(template):
    html = (TEMPLATES / template).read_text(encoding="utf-8")
    assert not re.search(r"\son[a-z]+\s*=", html), "gestionnaire inline (bloqué par la CSP)"
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html), "script inline (bloqué par la CSP)"
    assert "javascript:" not in html


def test_new_scripts_never_assign_innerhtml_nor_use_eval():
    for name in NEW_FILES:
        source = (JS_DIR / f"{name}.js").read_text(encoding="utf-8")
        assert not re.search(r"\.(innerHTML|outerHTML)\s*=", source), name
        assert "insertAdjacentHTML" not in source and "eval(" not in source and "document.write" not in source, name


@pytest.mark.parametrize("template", list(PAGE_SCRIPTS))
def test_script_order_and_expected_scripts(template):
    tags = script_tags(template)
    expected = PAGE_SCRIPTS[template]
    assert tags[:len(expected)] == expected, tags
    for first, second in zip(expected, expected[1:], strict=False):
        assert tags.index(first) < tags.index(second)


def top_level_names(path: Path, indent: int = 0) -> list[str]:
    pattern = re.compile(rf"^ {{{indent}}}(?:const|let|var|class|function\*?|async function)\s+([A-Za-z_$][\w$]*)", re.M)
    return pattern.findall(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("template", list(PAGE_SCRIPTS))
def test_scripts_of_one_page_do_not_redeclare_each_others_globals(template):
    """Des scripts classiques partagent la portée globale : redéclarer `esc`, `UI`… fait planter le 2ᵉ script (« already been declared »)."""
    owner: dict[str, str] = {}
    scripts = script_tags(template) + (["tickets"] if template == "tickets.html" else [])
    for script in dict.fromkeys(scripts):
        path = ROOT / "static" / "tickets.js" if script == "tickets" else JS_DIR / f"{script}.js"
        if not path.exists():
            continue
        for name in top_level_names(path, indent=8 if script == "tickets" else 0):
            assert name not in owner, f"« {name} » déclaré dans {owner[name]}.js ET {script}.js"
            owner[name] = script


@pytest.fixture
def client(clean_state):
    import main
    return TestClient(main.app)


def test_pages_render_with_their_scripts(client):
    for url, marker in (("/", "main.js"), ("/supervision", "supervision.js"), ("/admin", "admin.js"), ("/tickets/", "ticket-actions.js")):
        response = client.get(url)
        assert response.status_code == 200 and marker in response.text, url
    assert 'data-requires="supervision"' in client.get("/supervision").text and 'data-requires="admin"' in client.get("/admin").text


def test_dashboard_template_dropped_the_old_config_drawer_and_dm_link(client):
    html = client.get("/").text
    for obsolete in ("config-drawer", "openConfigBtn", "data-management", "config-panel.js", "theme-filters-audio.js"):
        assert obsolete not in html, obsolete
    for expected in ('id="account-widget"', 'id="presence"', 'id="maintenance-banner"', 'id="mobile-cards"', 'href="/supervision"', 'data-i18n="sect.international"'):
        assert expected in html, expected


def test_removed_files_are_really_gone():
    for gone in ("static/js/config-panel.js", "static/data_management.js", "templates/data_management.html", "static/js/theme-filters-audio.js"):
        assert not (ROOT / gone).exists(), gone


def test_static_assets_are_served_with_the_strict_csp(client):
    response = client.get("/static/js/common.js")
    assert response.status_code == 200 and "script-src 'self'" in response.headers["content-security-policy"]
    assert client.get("/static/app-ui.css").status_code == 200


def test_public_maintenance_endpoint_exposes_only_what_a_viewer_needs(client):
    from datetime import timedelta

    from core import governance
    from core.config import now_local
    assert client.get("/api/maintenance/active").json() == {"windows": []}
    now = now_local().replace(tzinfo=None)
    governance.add_window("kribi", now - timedelta(minutes=1), now + timedelta(hours=1), "câblage", "secret-user")
    windows = client.get("/api/maintenance/active").json()["windows"]
    assert [(w["db_key"], w["reason"]) for w in windows] == [("kribi", "câblage")] and "created_by" not in windows[0]
