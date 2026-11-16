"""Garde-fou front : aucun nom non défini dans les scripts classiques (portée globale partagée).

Une variable supprimée à tort (régression réelle de novembre 2026 : `end` dans `render.js`) ne fait pas échouer
`node --check` ; elle ne se voit qu'à l'exécution, dans le navigateur. eslint (règle `no-undef`) la détecte en statique.

eslint n'est pas une dépendance Python : le test est ignoré s'il est absent.
Installation : `npm install -g eslint globals`  (ou variable d'environnement ESLINT_BIN vers l'exécutable).
"""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = sorted((ROOT / "static" / "js").glob("*.js")) + [ROOT / "static" / "tickets.js"]
TOP_LEVEL_DECLARATION = re.compile(r"^(?:async\s+)?(?:function\*?|class|const|let|var)\s+([A-Za-z_$][\w$]*)", re.M)


def _eslint_executable() -> str | None:
    candidates = [os.getenv("ESLINT_BIN"), shutil.which("eslint"), "/tmp/lint/node_modules/.bin/eslint"]
    return next((c for c in candidates if c and Path(c).exists()), None)


def _names_shared_between_scripts() -> list[str]:
    """Les scripts se partagent la portée globale : toute déclaration de premier niveau est un global valide pour les autres."""
    return sorted({name for script in SCRIPTS for name in TOP_LEVEL_DECLARATION.findall(script.read_text(encoding="utf-8"))})


@pytest.mark.skipif(_eslint_executable() is None, reason="eslint non installé (npm install -g eslint globals)")
def test_no_undefined_names_in_front_scripts(tmp_path):
    shared_globals = dict.fromkeys(_names_shared_between_scripts(), "readonly")
    config = tmp_path / "eslint.config.js"
    config.write_text(
        "let browser = {};\n"
        "try { browser = require('globals').browser; } catch (error) { /* repli : liste minimale ci-dessous */ }\n"
        "const fallback = Object.fromEntries(['window','document','fetch','localStorage','sessionStorage','location','console','setTimeout',"
        "'clearTimeout','setInterval','clearInterval','URL','URLSearchParams','EventSource','Event','CustomEvent','FormData','navigator',"
        "'requestAnimationFrame','Audio','AudioContext','Blob','MutationObserver','getComputedStyle','matchMedia','Intl','performance',"
        "'HTMLElement','Node','KeyboardEvent','AbortController','history','alert','confirm','prompt','btoa','atob','TextEncoder','TextDecoder','Notification','SpeechSynthesisUtterance','speechSynthesis'].map(n => [n, 'readonly']));\n"
        f"module.exports = [{{ files: ['**/*.js'], languageOptions: {{ sourceType: 'script', ecmaVersion: 2022, "
        f"globals: {{ ...fallback, ...browser, ...{json.dumps(shared_globals)} }} }}, rules: {{ 'no-undef': 'error' }} }}];\n",
        encoding="utf-8",
    )
    result = subprocess.run([_eslint_executable(), "-c", str(config), *map(str, SCRIPTS)], capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, "noms non définis dans le front :\n" + result.stdout[-3000:]
