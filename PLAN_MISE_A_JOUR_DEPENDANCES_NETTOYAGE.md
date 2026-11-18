# Plan — mise à jour des dépendances et nettoyage du projet

**État général :** changements vérifiés localement ; commit à finaliser, E2E complète en attente de la CI  
**Branche de travail :** `chore/update-deps-cleanup` (créée depuis `main`, commit `b0223ae`)  
**Environnement :** `.venv` existant confirmé avec Python **3.12.10** (`.venv\Scripts\python.exe`)  
**Date de l’inventaire des versions :** 2026-11-18

## Objectif et périmètre

Mettre à jour les dépendances Python de manière reproductible, vérifier leur compatibilité et leur sécurité, puis nettoyer uniquement les artefacts générés. Les sources, la configuration locale, les données d’exécution, la suite E2E complète et les fichiers de travail de l’utilisateur sont préservés.

Aucun fichier suivi n’est supprimé sur la seule base de son nom ou d’une apparente inutilité. Le nettoyage se limite aux caches et artefacts reproductibles, après vérification qu’ils sont ignorés par Git et qu’ils ne contiennent pas de travail utilisateur.

## Inventaire initial

- Dépendances d’exécution : `requirements.txt` (13 paquets directs), verrou `requirements.lock.txt`.
- Dépendances de développement : `requirements-dev.txt` (outils de test et de qualité), sans verrou exact auparavant.
- Dépendances ML optionnelles : `requirements-ml.txt`, sans verrou exact auparavant.
- CI : `.github/workflows/ci.yml` installait le verrou d’exécution puis des outils de test **non verrouillés**.
- Conteneur : `Dockerfile` installe `requirements.lock.txt`, qui doit rester le verrou d’exécution minimal.
- Fichiers locaux à préserver : `REVUE_CODE.md` (non suivi), `.env`, `db_config.json`, `email_recipients.txt`, `data/`, le `.venv`.
- Branches de sauvegarde à préserver : `backup/pre-ui-final-20261114`, `backup/pre-ui-final9-20261114`.

## Décisions

1. Conserver le format `requirements*.txt` + verrous `uv pip compile` du projet ; ne pas migrer vers `pyproject`/`uv.lock` dans ce lot.
2. Relever les bornes supérieures vers les dernières versions stables disponibles le 2026-11-18, sans saut de version majeure incompatible :
   - `APScheduler` reste en 3.11.x (l’API 4.x est incompatible avec le code) ;
   - `httpx` reste en 0.28.x (pas de montée 1.x) ;
   - `bandit` reste en 1.9.x (dernière version stable publiée).
3. Produire trois verrous distincts, tous compilés en mode **universel** (`--universal`) pour que la CI Ubuntu et le poste Windows installent exactement les mêmes versions ; le verrou dev inclut aussi Pillow, importé par `demo/ui_audit.py` :
   - `requirements.lock.txt` : exécution, utilisé par le `Dockerfile` ;
   - `requirements-dev.lock.txt` : exécution + tests + qualité, utilisé par la CI ;
   - `requirements-ml.lock.txt` : exécution + détecteurs D4/D7 optionnels.
4. La CI installe `requirements-dev.lock.txt` et audite les trois verrous avec `pip-audit`. La suite E2E reste exécutée intégralement.
5. Le nettoyage supprime les caches Python/Ruff/Pytest générés. Les fichiers de configuration locale, bases SQLite, journaux et fichiers de revue non suivis sont conservés.

## Versions retenues

| Paquet | Avant | Après | Remarque |
|---|---:|---:|---|
| fastapi | 0.142.2 | 0.163.2 | dernière stable |
| starlette | 1.7.0 | 1.10.0 | compatible avec FastAPI 0.163.2 |
| uvicorn | 0.54.0 | 0.54.0 | déjà à jour |
| anyio | 4.15.1 | 4.16.0 | correctif |
| jinja2 | 3.1.6 | 3.1.6 | déjà à jour |
| SQLAlchemy | 2.1.3 | 2.1.9 | correctifs 2.1.x |
| PyMySQL | 1.2.3 | 1.2.4 | correctifs |
| cryptography | 50.0.0 | 50.0.2 | correctifs sécurité |
| requests | 2.33.1 | 2.34.2 | correctifs |
| orjson | 3.12.0 | 3.13.0 | correctifs |
| python-dotenv | 1.2.2 | 1.2.2 | déjà à jour |
| APScheduler | 3.11.3 | 3.11.3 | majeure 4.x exclue |
| pydantic | 2.13.5 | 2.13.5 | déjà à jour |
| pytest | 9.1.1 | 9.2.0 | outils de test |
| playwright | 1.56.0 | 1.65.1 | outils E2E |
| Pillow | absent du manifeste | 13.0.0 | requis par `demo/ui_audit.py` |
| ruff | 0.16.10 | 0.17.4 | qualité |
| pip-audit | 2.10.1 | 2.10.1 | déjà à jour |
| bandit | 1.9.4 | 1.9.4 | déjà à jour |
| scikit-learn | 1.8.0 | 1.8.0 | déjà à jour, borne minimale ajoutée |
| joblib | 1.5.3 | 1.5.3 | déjà à jour, borne minimale ajoutée |

## Progression

| # | Étape | Résultat attendu | État |
|---|---|---|---|
| 1 | Vérifier l’état Git, les manifests, les verrous, les outils et les exigences CI. | Inventaire de référence, fichiers locaux identifiés, branche créée. | Terminé |
| 2 | Vérifier l’environnement virtuel Python 3.12. | `.venv` avec Python 3.12.10, aucune installation globale. | Terminé |
| 3 | Examiner les dépendances, usages, compatibilités et avis de sécurité. | Versions candidates et contraintes justifiées. | Terminé |
| 4 | Mettre à jour les bornes et régénérer les verrous avec `uv`. | Trois verrous exacts cohérents pour Python 3.12. | Terminé |
| 5 | Installer les verrous et exécuter les contrôles. | Tests, Ruff, Bandit, audit et E2E conformes. | Contrôles locaux terminés ; E2E CI en attente |
| 6 | Nettoyer les caches et artefacts générés. | Nettoyage réversible, sans perte de travail utilisateur. | Terminé |
| 7 | Relire le diff final et mettre à jour ce plan. | Résumé des changements, résultats et limites. | En cours |

## Vérifications

- [x] `.venv` Python 3.12.10.
- [x] `requirements.lock.txt` régénéré (30 paquets résolus).
- [x] `requirements-dev.lock.txt` créé (69 paquets résolus, dont Pillow pour l’audit UI).
- [x] `requirements-ml.lock.txt` créé (35 paquets résolus).
- [x] Trois verrous compilés avec marqueurs de plateforme universels (`uv pip compile --python-version 3.12 --universal`).
- [x] Les trois verrous contiennent intégralement les versions de production ; le verrou ML ajoute seulement ses dépendances optionnelles.
- [x] `requirements-dev.lock.txt` installé dans `.venv`.
- [x] Import de l’application réussi : FastAPI 0.163.2, Starlette 1.10.0, SQLAlchemy 2.1.9, Pydantic 2.13.5.
- [x] `ruff check .` : succès.
- [x] `bandit -q -r core routers main.py -ll --skip B608` : succès.
- [x] `pytest` après la dernière mise à jour du verrou dev : **137 réussis, 169 ignorés, 1 avertissement** (55 s ; MariaDB et E2E indisponibles localement, ML reste optionnel).
- [x] `pip-audit` des trois verrous : **aucune vulnérabilité connue**.
- [x] Smoke test de l’application avec lifespan complet : santé 200, page 200, connexion 200, données du tableau de bord 200.
- [ ] E2E complète : le workflow CI conserve son exécution sans filtre ; résultat final attendu d’un runner avec MariaDB et Chromium.
- [x] Nettoyage final des caches (6 dossiers `__pycache__`, `.ruff_cache` ; puis 5 dossiers régénérés par les tests).
- [x] Relecture du diff final et mise à jour du plan.
- [x] Commit sur `chore/update-deps-cleanup` (`75795f6`). `REVUE_CODE.md` reste non suivi et préservé.

## Nettoyage effectué

| Élément | Décision | Justification |
|---|---|---|
| `__pycache__/` (6 dossiers) | **Supprimés** | Bytecode régénérable, ignoré par Git |
| `.ruff_cache/` | **Supprimé** | Cache de l’outil, ignoré par Git |
| `.pytest_cache/` | Absent | Le projet utilise `-p no:cacheprovider` |
| `app_unified.log` | **Conservé** | Journal d’exécution local ignoré, potentiellement utile au diagnostic |
| `.venv/` | **Conservé** | Environnement Python 3.12 demandé |
| `.env`, `db_config.json`, `email_recipients.txt` | **Conservés** | Configuration/secrets locaux ignorés |
| `data/` | **Conservé** | Donnée d’exécution locale (`tickets_db.sqlite`) |
| `REVUE_CODE.md`, `REVUE_CODE_AUTODO.md` | **Conservés** | Revue utilisateur et revue historique suivie |
| `.gitignore` | **Nettoyé** | Doublons de `__pycache__/` et `.pytest_cache/` retirés |

## Journal d’exécution

- **2026-11-18 :** plan créé avant toute modification. Dépôt sur `main` (`b0223ae`), branche `chore/update-deps-cleanup` créée. `REVUE_CODE.md` non suivi à préserver ; branches de sauvegarde présentes.
- **2026-11-18 :** `.venv` existant réutilisé, Python 3.12.10 confirmé.
- **2026-11-18 :** suite de référence exécutée avant mise à jour : 137 réussis, 169 ignorés, 1 avertissement (14 min 10 s).
- **2026-11-18 :** versions candidates relevées et compatibilités vérifiées ; bornes de `requirements.txt`, `requirements-dev.txt` et `requirements-ml.txt` mises à jour.
- **2026-11-18 :** trois verrous régénérés avec `uv pip compile --python-version 3.12 --universal`, pour garder les marqueurs de plateforme portables entre Windows et Ubuntu.
- **2026-11-18 :** dépendances de développement installées ; import de `main` validé avec FastAPI 0.163.2 / Starlette 1.10.0.
- **2026-11-18 :** dépendance Pillow 13.0.0 ajoutée au manifeste dev après découverte de son import direct dans `demo/ui_audit.py` ; le verrou dev compte désormais 69 paquets.
- **2026-11-18 :** CI rendue reproductible : installation du verrou de développement, audit des trois verrous, conservation des tests et de l’E2E. Documentation d’installation alignée sur les nouveaux verrous ; le test E2E reste complet et sans condition de contournement.
- **2026-11-18 :** `ruff check .`, Bandit, `pip check` et `pip-audit` réussis ; aucune vulnérabilité connue dans les trois verrous.
- **2026-11-18 :** `pytest` après toutes les mises à jour : **137 réussis, 169 ignorés, 1 avertissement en 55 s**.
- **2026-11-18 :** smoke test de bout en bout du cycle de vie et des routes principales : `/health`, `/`, `/api/login`, `/api/dashboard_data` répondent tous 200.
- **2026-11-18 :** nettoyage des 6 dossiers `__pycache__` et de `.ruff_cache` ; fichiers locaux, données, journaux et revues conservés.
- **2026-11-18 :** suppression des doublons `__pycache__/` et `.pytest_cache/` dans `.gitignore` (déjà couverts dans la section Python).
- **2026-11-18 :** relecture du diff et second nettoyage des caches régénérés par les tests ; verrous relus et cohérents entre eux.
