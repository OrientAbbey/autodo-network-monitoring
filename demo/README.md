# Kit de démonstration AUTODO — données simulées + tests Playwright

Tout est **local et simulé** : aucune donnée réelle, aucun appel vers Orange, DIAGNET, ExNet ou un vrai SMTP.

| Fichier | Rôle |
|---|---|
| `seed_demo_data.py` | Génère 19 bases DIAGNET simulées (`SIM_<clé>`) : 8 jours d'historique par pas de 5 min (courbe diurne, bruit, pertes), tables `whole_5min`, `apn_5min`, `dns_5min`, + `tickets_db` et `db_config.json`. Sous-commandes : `seed`, `follow` (flux live), `scenario`, `config` |
| `mock_orange_api.py` | Faux API Orange (OAuth2 + `troubleTicket`), inspectable via `GET /_debug/tickets` |
| `mail_sink.py` | Puits SMTP (127.0.0.1:1025) : les e-mails sont écrits en `.eml` |
| `env.demo` | Variables d'environnement de l'application pour la démo |
| `sandbox_stack.sh` | Démarre MariaDB + services simulés puis exécute une commande (utilisé pour la revue) |
| `screenshots/` | Captures produites par les tests (version corrigée) |

## Anomalies injectables (`scenario`)

| Mode | Effet sur la base choisie | Ticket attendu |
|---|---|---|
| `zero` | trafic nul | traffic, catégorie 1 |
| `instant_drop` | chute instantanée (×0,35) | traffic, catégorie 2 |
| `gradual` | baisses consécutives (×0,75 / pas) | traffic, catégorie 3 |
| `loss` | pertes TCP à 8 % | pertes |
| `stale` | la base n'est plus alimentée | db_down |

## Démo interactive (MySQL/MariaDB local déjà démarré)

```bash
pip install fastapi uvicorn aiosmtpd mysql-connector-python    # + dépendances du projet
export DEMO_DB_ADMIN_USER=root DEMO_DB_ADMIN_PASS=...           # compte admin pour créer les bases simulées
cd demo
python seed_demo_data.py scenario zero douala_camtel            # (optionnel) anomalies initiales
python seed_demo_data.py scenario loss wacs_ic --minutes-ago 30
python seed_demo_data.py seed --reset                           # crée les bases + db_config.json
cp db_config.json <repo>/db_config.json
python seed_demo_data.py follow &                               # flux « live » (un créneau / 5 min)
python -m uvicorn mock_orange_api:app --port 9001 &
python mail_sink.py &
set -a; . ./env.demo; set +a
cd <repo> && python main.py                                     # http://127.0.0.1:8501
# Pendant la démo, déclencher / retirer une anomalie à chaud :
python demo/seed_demo_data.py scenario instant_drop kribi
python demo/seed_demo_data.py scenario clear kribi
```
Mots de passe de démo : configuration `cfgpass`, data-management `dmpass`.

## Démo sans base de données (SQLite + sources simulées)

Aucun MySQL/MariaDB à installer : la base **locale** (tickets, visites, journaux) est un fichier SQLite dans
`DATA_DIR`, et les bases **DIAGNET** ne sont pas interrogées — une fonction **reproductible** (`core/demo_data.py`)
recalcule les mêmes colonnes que `whole_5min` / `apn_5min` / `dns_5min`. Deux cycles relisant un créneau
obtiennent les mêmes chiffres (tickets stables), et les scénarios ci-dessus fonctionnent à l'identique.

```powershell
# Windows : génère db_config.json, charge demo/env.demo.sqlite et démarre l'app
.\demo\run_demo_sqlite.ps1
.\demo\run_demo_sqlite.ps1 zero douala_camtel        # anomalie avant démarrage (optionnel)

# Ou, manuellement (Linux/macOS identique) :
export DB_BACKEND=sqlite DIAGNET_SOURCE=fake DATA_DIR=./data
python -m core.demo_data config db_config.json       # catalogue simulé
python main.py                                        # http://127.0.0.1:8501
python -m core.demo_data scenario instant_drop kribi  # anomalie à chaud
python -m core.demo_data scenario clear kribi
```
Réglages : `DB_BACKEND=sqlite` (base locale dans `DATA_DIR/<LOCAL_DB_NAME>.sqlite`) et
`DIAGNET_SOURCE=fake` (sources en mémoire). Par défaut `DB_BACKEND=sqlite` implique `DIAGNET_SOURCE=fake`.

## Tests Playwright

```bash
pip install -r requirements-dev.txt && playwright install chromium
# pile déjà démarrée (MySQL + faux Orange :9001 + puits SMTP :1025) :
pytest tests/e2e -v            # depuis la racine du dépôt
```
`E2E_ALIGN=0` désactive l'alignement sur la fenêtre de 5 min. Le test `test_second_cycle_is_stable_no_duplicates`
attend le 2ᵉ cycle du scheduler (jusqu'à ≈ 4 min) ; pour l'exclure : `-k "not second_cycle"`.

Les tests E2E se trouvent dans `tests/e2e/` ; le garde-fou « lecture seule » des bases DIAGNET est vérifié par `tests/test_db_integration.py`.
