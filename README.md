# AUTODO — supervision réseau, tickets et synchronisation Orange

Application FastAPI mono-processus : lit les compteurs de ≈ 20 bases **DIAGNET (lecture seule)** toutes les 5 minutes,
affiche un tableau de bord temps réel, ouvre/suit/ferme des tickets (base locale), les synchronise avec l'**API Orange
(CALYPSO)** et notifie par e-mail.

## Arborescence (volontairement plate : 2 paquets Python seulement)

```
main.py                 point d'entrée (cycle de vie, middlewares, CSP)
core/                   toute la logique, un module = une responsabilité
  config.py             réglages (environnement), paramètres d'alerte (cache mtime, écriture atomique), temps (UTC ↔ Douala)
  constants.py          db_config.json, libellés, liens ExNet, cache du tableau de bord
  db.py                 SQLAlchemy : moteur local (RW), moteurs DIAGNET (RO), migrations versionnées
  rules.py              règles PURES : détection, fermeture, plan_actions (ex-16 branches), textes — sans E/S
  collector.py          lecture parallèle des bases DIAGNET
  metrics.py            calcul du tableau de bord (Python pur)
  tickets.py            dépôt SQL des tickets, ID atomique, verrouillage optimiste, outbox
  orange.py             client API Orange protégé (débit, concurrence, disjoncteur) + synchronisation outbox
  notify.py             e-mails (SMTP borné, état en base)
  analytics.py          visites (tampon mémoire), journaux, purge de rétention
  monitoring.py         cycle de supervision, tâches périodiques, watchdog, SSE
  auth.py  ratelimit.py  logger.py
routers/                routes HTTP (dashboard.py, tickets.py)
templates/ static/      interface (aucun script inline : CSP stricte)
tests/                  unitaires + sécurité + intégration MariaDB (tests/e2e : Playwright)
demo/                   données simulées, faux API Orange, puits SMTP (voir demo/README.md)
```

## Installation

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements.lock.txt           # versions exactes validées ; requirements.txt = bornes (<=)
cp .env.example .env && $EDITOR .env           # mots de passe obligatoires (fail-closed)
cp db_config.example.json db_config.json       # sources DIAGNET (hôte, base, site…)
cp email_recipients.example.txt email_recipients.txt   # ou EMAIL_RECIPIENTS dans .env
python main.py                                 # crée/migre le schéma, puis http://127.0.0.1:8501
```

* **Un seul worker** : sessions, cache du tableau de bord, ordonnanceur et abonnés SSE sont en mémoire (ne pas lancer
  `--workers N`). Déploiement : `Dockerfile` (utilisateur non-root, volume `/data`) ou `autodo.service` (systemd durci).
* Derrière un reverse proxy TLS : `APP_HOST=127.0.0.1`, `COOKIE_SECURE=true`, `TRUSTED_PROXIES=<ip du proxy>`.
  Désactiver la mise en tampon pour `/events` (le serveur envoie déjà `X-Accel-Buffering: no`).
* **Migration depuis l'ancienne version** : `config.json` (racine) est copié automatiquement dans `DATA_DIR` au premier
  démarrage ; le schéma est migré sans perte (`schema_version`). Supprimer `core/email_recipients.txt` du dépôt et
  renseigner `EMAIL_RECIPIENTS`. **Faire pivoter** les identifiants ExNet (l'auto-login par mot de passe partagé est supprimé).

## Décisions techniques

### D1 — Accès base de données : SQLAlchemy 2.1 (Core) + PyMySQL, pour TOUT le projet

Charge réelle : ≈ 19 sources DIAGNET lues toutes les 5 min (≈ 12 lignes chacune, **latence réseau dominante**), une base
locale (tickets, visites) avec quelques lectures/écritures par seconde au plus. Mesures (MariaDB locale, 16 threads, 1 500
lectures de 12 lignes, 5 000 lignes en lot) :

| Critère | mysql-connector 26.7 (pool natif) | **SQLAlchemy 2.1.3 + PyMySQL 1.2.3** |
|---|---|---|
| Latence d'une lecture (checkout + 12 lignes) | 0,56 ms | **0,52 ms** |
| Débit, 16 threads | 1 627 req/s | 1 551 req/s (≈ identique, bruit de mesure) |
| Pool épuisé (16 threads, pool 6) | **échec immédiat** `PoolError` : 600 erreurs / 960 requêtes sans retry applicatif | **attend** (`pool_timeout`) : 0 erreur |
| Upsert de 5 000 tickets (insert / ON DUPLICATE) | 214 / 167 ms | **121 / 117 ms** |
| Reconnexion après coupure serveur | OK (`pool_reset_session`) | OK, explicite (`pool_pre_ping`, `pool_recycle`) |
| Transactions | `with conn:` ne valide **pas** (cause du bug C-01) | `engine.begin()` : COMMIT/ROLLBACK automatiques |
| Garde-fou lecture seule DIAGNET | à bricoler | événement `connect` (session `READ ONLY` + délai serveur) |
| Dépendance | C-ext ou pur ; versionnage atypique | PyMySQL pur Python (+ `cryptography` pour MySQL 8) |

→ Les performances sont équivalentes à cette échelle ; **les différences décisives sont structurelles** : pool qui
attend plutôt que d'échouer, transactions sans piège, un moteur par base avec sa propre politique, écritures en lot et
`ON DUPLICATE KEY` typés. Core uniquement (pas d'ORM : aucun intérêt pour des lignes plates, et pas de surcoût).
Pilotes asynchrones (asyncmy/aiomysql) écartés : moins maintenus, aucun gain pour ~20 requêtes toutes les 5 min.

Dimensionnement : DIAGNET = 1 moteur par (hôte, port, base), `pool_size=1, max_overflow=1`, `AUTOCOMMIT`, session `READ ONLY`,
`max_execution_time`, `pool_pre_ping`, `pool_recycle=240 s` (les pare-feux coupent les connexions inactives entre deux cycles).
Au plus `COLLECT_MAX_WORKERS` (8) connexions simultanées vers l'ensemble des DIAGNET. Base locale : pool 8 + 8 pour au plus
20 threads d'API (`anyio` borné à 20).

### D2 — Synchrone, threads ou asyncio ?

| Tâche | Choix | Pourquoi |
|---|---|---|
| Collecte DIAGNET | `ThreadPoolExecutor` borné, **parallèle** | E/S bloquantes distantes ; une base lente/morte ne retarde plus les autres |
| Calcul tableau de bord, règles de détection | **séquentiel**, Python pur | quelques dizaines de valeurs : paralléliser coûterait plus qu'il ne rapporte |
| Écriture des tickets | **une transaction par cycle** | atomicité, un seul COMMIT |
| API Orange | **hors du cycle** : job « outbox » (60 s) | la détection ne dépend plus de la latence/panne d'Orange |
| E-mails | exécuteur dédié à 1 thread | un SMTP lent ne bloque pas la supervision |
| Endpoints HTTP | `def` (pool de threads) ou `asyncio.to_thread` ; `async def` seulement pour le SSE | aucune E/S SQL dans la boucle d'événements |
| Visites utilisateurs | tampon mémoire vidé toutes les 10 s | N rafraîchissements = 1 écriture groupée |

asyncio n'est utilisé que là où il apporte quelque chose (flux SSE, concurrence de connexions HTTP entrantes).

### D3 — Polars : retiré

Volume par cycle : ≤ 20 bases × 3 intervalles × 2 relevés ≈ 120 lignes + 6 valeurs par série de détection. À cette taille,
construire un DataFrame coûte plus cher que de boucler sur une liste, et l'import ajoute ≈ 100 ms et ≈ 30 Mo. Les calculs
sont du Python pur (`metrics.py`, `rules.py`) ; les agrégations lourdes (mode sur 7 jours) restent **dans MySQL** où les données
résident. Repasser à polars/SQL analytique ne serait pertinent qu'au-delà de ≈ 10⁵ lignes par cycle.

### D4 — Parallélisation

Parallèle : uniquement la collecte DIAGNET (≈ 19 lectures indépendantes ; mesure en démo : 19 bases en 0,4 s en local).
Charge allégée côté DIAGNET (lecture seule imposée) : 1 connexion/base/cycle (au lieu de 3), `SHOW TABLES` supprimé,
mode 7 jours **mis en cache 30 min**, requête APN uniquement pour les bases « utilisateur ».

### D5 — API Orange : limites de débit et pannes

`TokenBucket` (débit moyen + rafale) devant chaque appel ; `Semaphore` de concurrence (2) ; `Retry-After` honoré (HTTP 429 gèle
tous les appelants) ; disjoncteur (5 échecs → pause 5 min, un appel d'essai) ; délais connexion 5 s / lecture 20 s ; jeton OAuth
renouvelé par un seul thread, invalidé sur 401 ; nouvelles tentatives seulement pour timeouts/5xx/429, **backoff exponentiel
avec gigue stocké en base** (`orange_next_retry_at`) ; abandon après `ORANGE_API_MAX_ATTEMPTS` (état `failed` + alerte interne).
Création **idempotente** autant que l'API le permet : ID AUTODO dans la description, recherche optionnelle par `externalId`
après une réponse perdue (`ORANGE_API_LOOKUP_EXTERNAL_ID`). Statuts terminaux Orange = liste blanche (plus de fermeture sur un statut absent).

### D6 — Base locale : lectures et écritures

Écritures ciblées `UPDATE … SET <champs>, version = version + 1 WHERE id = :id AND version = :v` (verrouillage optimiste) ;
une transaction par cycle ; `INSERT` strict à la création (jamais d'écrasement) ; ID de ticket atomique (table compteur) ;
index `(status, timestamp)`, `(orange_sync_state, orange_next_retry_at)`, unique `external_api_ticket_id` ; une seule lecture
des tickets ouverts par cycle ; alertes du dashboard avec plage sargable + `LIMIT` + cache TTL 3 s ; API Tickets paginée ;
purge de rétention par lots ; migrations versionnées **sans Alembic** (`db.MIGRATIONS`, idempotentes).

### D7 — Sécurité (résumé)

Authentification à portées (`cfg`, `dm`), mots de passe **fail-closed**, comparaison à temps constant, anti brute-force, CSP stricte
(aucun script inline), échappement de toutes les données injectées, `X-Forwarded-For` ignoré hors proxys déclarés, plus aucun
nom d'hôte interne ni mot de passe partagé envoyé au navigateur (`/go/…` redirige côté serveur), TLS Orange vérifié, aucun secret
dans les logs, CORS fermé par défaut. Pas de SSO disponible : les utilisateurs s'authentifient eux-mêmes sur ExNet.

## Exploitation

* `GET /health` (200 si base locale OK) · `GET /api/status` (session requise : cycles, dernière réussite, sources en échec, Orange).
* **Watchdog** : e-mail interne si aucun cycle réussi depuis `WATCHDOG_MAX_AGE_MIN` ; alerte aussi si une source DIAGNET reste
  injoignable `source_unreachable_cycles` cycles (jamais de ticket Orange pour une panne de **supervision**).
* Paramètres d'alerte : interface (⚙️) ou `DATA_DIR/config.json` ; relus à chaque cycle. `alerts_enable_international` /
  `alerts_enable_gi` pilotent la création de tickets, les autres interrupteurs ne règlent que l'affichage.
* Convention horaire : bases DIAGNET en UTC, tickets et affichage en heure de Douala (`core/config.py`, `ZoneInfo`).
* Chart.js 4.5.1 est embarqué dans `static/` (mettre à jour ce fichier si nécessaire).

## Tests

```bash
pip install -r requirements-dev.txt
pytest                                  # unitaires, sécurité, intégration (MariaDB requise ; sinon ignorés)
ruff check . && bandit -q -r core routers main.py -ll
playwright install chromium && pytest tests/e2e   # E2E avec la pile simulée (voir demo/README.md)
```
