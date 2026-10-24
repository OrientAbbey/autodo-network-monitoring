# AUTODO — supervision réseau, tickets et synchronisation Orange

Application FastAPI mono-processus : lit les compteurs de ≈ 20 bases **DIAGNET (lecture seule)** toutes les 5 minutes,
affiche un tableau de bord temps réel, ouvre/suit/ferme des tickets (base locale), les synchronise avec l'**API Orange
(CALYPSO)** et notifie par e-mail.

## Arborescence (volontairement plate : 2 paquets Python seulement)

```
main.py                 point d'entrée (cycle de vie, middlewares : CSRF par Origin, en-têtes, CSP)
core/                   toute la logique, un module = une responsabilité
  config.py             réglages (environnement), paramètres d'alerte + seuils PAR BASE (cache mtime, écriture atomique), temps (UTC ↔ Douala)
  constants.py          db_config.json, libellés, liens ExNet, tableau de bord (dict + JSON sérialisé une fois par cycle, version)
  db.py                 SQLAlchemy : moteur local (RW), moteurs DIAGNET (RO), migrations versionnées (1 → 6)
  accounts.py           comptes nominatifs (scrypt), politique de mot de passe, verrouillage, dernier admin protégé
  auth.py               sessions en mémoire (seul le SHA-256 du jeton est gardé), rôles, limiteur par IP, contrôle d'origine
  governance.py         audit, versions de configuration (diff, restauration), maintenance et silences
  rules.py              règles PURES : détection, fermeture, plan_actions, textes — sans E/S
  collector.py          lecture parallèle des bases DIAGNET (+ read_range pour l'import d'historique)
  metrics.py            calcul du tableau de bord (Python pur)
  tickets.py            dépôt SQL des tickets, validation/rejet/suppression, accusés, ID atomique, verrouillage optimiste, outbox
  ticket_log.py         chronologie d'un ticket et file des notes à envoyer à Orange
  orange.py             client API Orange protégé (débit, concurrence, disjoncteur) + outbox + notes
  notify.py             e-mails (SMTP borné, état en base ; rien n'est envoyé pour un ticket en attente de validation)
  history.py            historique local metrics_5min (alimenté à chaque cycle, import DIAGNET, mini-courbes)
  anomaly.py            détecteurs statistiques PURS : D1 saisonnier, D3 Holt-Winters + capacité, D5 CUSUM
  anomaly_ml.py         détecteurs à apprentissage OPTIONNELS : D4 Isolation Forest, D7 qualité d'alerte (scikit-learn)
  shadow.py             mode ombre : exécute les détecteurs sans jamais créer de ticket, rapport vs vrais tickets
  simulation.py         « et si ? » : rejoue une configuration candidate sur l'historique
  analytics.py          visites (tampon mémoire), pics de spectateurs, purge de rétention
  monitoring.py         cycle de supervision, tâches périodiques, watchdog, SSE (publication depuis le thread du cycle)
  ratelimit.py  logger.py
routers/                routes HTTP : dashboard.py (pages, données, SSE, accusés), account.py (connexion), admin.py (supervision + admin), tickets.py
templates/ static/      interface (aucun script inline : CSP stricte) ; static/js/ : scripts classiques à portée globale partagée, chargés dans
                        l'ordre common → i18n → prefs → session → sound → … (voir les <script> de chaque gabarit) ; static/app-ui.css = composants communs
tests/                  unitaires + sécurité + intégration MariaDB ; tests/e2e : Playwright (pile complète simulée)
demo/                   données simulées, faux API Orange, puits SMTP, prototype de détection, captures d'écran avant/après (voir demo/README.md)
ROADMAP.md              état d'avancement et propositions restantes
REVUE_CODE_AUTODO.md    revue de code et journal des correctifs
```

## Installation

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements.lock.txt           # versions exactes validées ; requirements.txt = bornes (<=)
cp .env.example .env && $EDITOR .env           # ADMIN_USERNAME / ADMIN_PASSWORD : admin INITIAL (changement forcé à la 1re connexion)
cp db_config.example.json db_config.json       # sources DIAGNET (hôte, base, site…)
cp email_recipients.example.txt email_recipients.txt   # ou EMAIL_RECIPIENTS dans .env
pip install -r requirements-ml.txt             # OPTIONNEL : détecteurs D4 (Isolation Forest) et D7 (qualité d'alerte)
python main.py                                 # crée/migre le schéma, crée l'admin initial, puis http://127.0.0.1:8501
```

* **Un seul worker** : sessions, cache du tableau de bord, ordonnanceur et abonnés SSE sont en mémoire (ne pas lancer
  `--workers N`). Déploiement : `Dockerfile` (utilisateur non-root, volume `/data`) ou `autodo.service` (systemd durci).
* Derrière un reverse proxy TLS : `APP_HOST=127.0.0.1`, `COOKIE_SECURE=true`, `TRUSTED_PROXIES=<ip du proxy>`.
  Désactiver la mise en tampon pour `/events` (le serveur envoie déjà `X-Accel-Buffering: no`).
* **Migration depuis l'ancienne version** : `config.json` (racine) est copié automatiquement dans `DATA_DIR` au premier
  démarrage ; le schéma est migré sans perte (`schema_version`). Supprimer `core/email_recipients.txt` du dépôt et
  renseigner `EMAIL_RECIPIENTS`. **Faire pivoter** les identifiants ExNet (l'auto-login par mot de passe partagé est supprimé).
* **Passage aux comptes nominatifs** : `CONFIG_PASSWORD` et `DATA_MANAGEMENT_PASSWORD` n'existent plus (ni `/update_config`, `/api/auth`,
  `/data-management`, qui redirige vers `/admin`). Au premier démarrage sans aucun compte, `ADMIN_USERNAME` / `ADMIN_PASSWORD` créent
  l'administrateur initial ; **retirer ADMIN_PASSWORD du `.env` ensuite** (il n'est plus lu tant qu'un compte existe). La table `logs`
  est renommée `logs_archived` (migration 6, aucune donnée détruite ; à supprimer à la main quand vous l'aurez vérifiée).

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

**Comptes nominatifs** (rôles `supervision` et `admin`, le visiteur n'a pas de compte) : mots de passe hachés `scrypt` (bibliothèque standard,
sel aléatoire), politique ≥ 10 caractères avec majuscule, minuscule et chiffre, **5 échecs ⇒ compte verrouillé 15 min**, un seul message
d'erreur et un hachage factice pour un identifiant inconnu (on ne distingue ni les comptes existants ni les temps de réponse), limiteur
par IP en plus, dernier administrateur actif **non désactivable**. Sessions en mémoire (**seul le SHA-256 du jeton est conservé**), cookie
`HttpOnly` + `SameSite=Strict` (+ `Secure` derrière TLS), inactivité 120 min et durée absolue 12 h, révocables par l'admin ; un changement de
rôle ou une désactivation s'applique **aux sessions ouvertes**. Défense CSRF supplémentaire : toute requête qui modifie l'état dont l'en-tête
`Origin` n'est pas notre hôte est refusée (403). CSP stricte (aucun script inline, `unsafe-eval` interdit), DOM construit sans `innerHTML`
pour les nouvelles pages, `X-Forwarded-For` ignoré hors proxys déclarés, plus aucun nom d'hôte interne ni mot de passe partagé envoyé au
navigateur (`/go/…` redirige côté serveur), TLS Orange vérifié, aucun secret dans les logs, CORS fermé par défaut. Pas de SSO disponible :
les utilisateurs s'authentifient eux-mêmes sur ExNet.

### D8 — Rôles, gouvernance et validation humaine

| Rôle | Peut |
|---|---|
| visiteur (sans compte) | consulter le tableau de bord, les tickets et leur chronologie, **acquitter** une alerte (accusé partagé), régler ses préférences |
| `supervision` | + `/supervision` : paramètres globaux, **seuils par base**, fenêtres de **maintenance**, historique et **restauration** de la configuration, **valider / rejeter / supprimer** des tickets, commenter (avec envoi optionnel à Orange), retour « réel / faux positif » |
| `admin` | + `/admin` : comptes, sessions, journal d'audit (CSV), usage, santé, **détecteurs** (import d'historique, entraînement, rapport du mode ombre), analyses, **simulation « et si ? »** |

Chaque modification de configuration crée une **version** (écarts avant → après) ; restaurer crée une *nouvelle* version. Chaque action
sensible est inscrite dans `audit_log` (qui, IP, quoi, détail). Option `ticket_validation_required` : un ticket créé par le cycle attend un
superviseur — **rien n'est envoyé à Orange ni par e-mail avant validation** ; rejeter ou supprimer ouvre automatiquement un **silence** de la
base (`rejection_silence_minutes`, 120 par défaut) pour que la même alerte ne revienne pas aussitôt. Pendant une fenêtre de maintenance,
aucun ticket n'est créé ni mis à jour pour la base concernée (évalué à l'heure **réelle**, pas à celle du checkpoint).

### D9 — Temps réel

Les évènements SSE sont des messages **sans nom** (`data: {...}`), seul format reçu par `EventSource.onmessage` (seul `hello` est nommé).
Le cycle (thread du planificateur) publie `data_version` dans la boucle asyncio ; chaque navigateur recharge après une **gigue de 0 à 3 s**
(`ack` : < 0,3 s). `GET /api/dashboard_data` sert un JSON **sérialisé une fois par cycle** avec `ETag` (304 si rien n'a changé). Mesuré :
50 spectateurs × 12 requêtes + 50 flux SSE → médiane ≈ 135 ms, p95 ≈ 220 ms, 0 erreur.

### D10 — Historique et détecteurs « futurs » (aucun ne crée de ticket)

`metrics_5min` (rétention `HISTORY_RETENTION_DAYS`, 400 j) est alimentée par le cycle ; l'admin importe les jours passés depuis DIAGNET
(lecture seule). **D1** référence saisonnière (médiane/MAD du même créneau sur 7 jours, ≥ 3 jours requis), **D3** prévision Holt-Winters
24 h + jours avant saturation d'un lien, **D5** ruptures de niveau (CUSUM), **D4** Isolation Forest par base et **D7** qualité d'alerte
(validation temporelle 80/20, jamais le TTR comme variable, ≥ `MIN_LABELED_TICKETS` retours des deux classes) — D4/D7 exigent
`requirements-ml.txt`, sinon l'API répond 501. En **mode ombre** ils tournent à chaque cycle ; `shadow_report` compare leurs épisodes aux vrais
tickets (fenêtre −30 / +60 min) : épisodes confirmés, sans ticket, tickets couverts, avance médiane. **Ces chiffres sont à lire sur vos
données réelles avant toute décision** : les résultats du prototype (`ROADMAP.md` §3.1) viennent de séries simulées.

### D11 — Interface

Préférences **dans le navigateur** (langue FR/EN, thème, palette adaptée au daltonisme, son, voix, notifications) partageables par lien
(`#prefs=…`, jamais le son). **Son désactivé par défaut** ; mur d'écran `/?wall=1[&sound=1][&rotate=30]` (une section à la fois, sans
contrôles, silencieux sauf `sound=1`). Mini-courbes 24 h avec bande « attendu », cartes par base sous 640 px, accessibilité (`aria-live`,
focus piégé dans la pop-up, titres de section au clavier). Traductions : tout sauf l'aide longue du tableau de bord et les textes produits
par le serveur (motifs d'alerte, audit).

## Exploitation

* `GET /health` (200 si base locale OK) · `GET /api/status` (rôle supervision : cycles, dernière réussite, sources en échec, Orange ; la page `/admin` → Santé est plus complète).
* **Watchdog** : e-mail interne si aucun cycle réussi depuis `WATCHDOG_MAX_AGE_MIN` ; alerte aussi si une source DIAGNET reste
  injoignable `source_unreachable_cycles` cycles (jamais de ticket Orange pour une panne de **supervision**).
* Paramètres d'alerte : page `/supervision` (⚙️) ou `DATA_DIR/config.json` ; relus à chaque cycle ; `config.json` contient aussi les seuils par base (`overrides`). `alerts_enable_international` /
  `alerts_enable_gi` pilotent la création de tickets, les autres interrupteurs ne règlent que l'affichage.
* Convention horaire : bases DIAGNET en UTC, tickets et affichage en heure de Douala (`core/config.py`, `ZoneInfo`).
* Chart.js 4.5.1 est embarqué dans `static/` (mettre à jour ce fichier si nécessaire).
* Capacité mesurée (démo, une machine) : 50 spectateurs + 50 flux SSE → médiane ≈ 135 ms, p95 ≈ 220 ms, 0 erreur ; JSON du tableau de bord 113 Ko brut / 19 Ko gzip.
* **Note vers Orange** : `OrangeClient.add_note` suppose un corps `{"note":[{text, author, date}]}` en `PUT` + `X-HTTP-Method-Override: PATCH` — **format à confirmer avec l'API réelle**, l'adaptation se fait dans cette seule méthode.
* **Mettre en service les détecteurs** : `/admin` → Détecteurs → *Importer* 30 jours → *Entraîner* (D4) → laisser tourner 2 à 4 semaines → lire le rapport du mode ombre ; D7 après ≥ 100 retours « réel / faux positif » donnés sur la page des tickets.
* Un seul worker : sessions, accusés en cache, abonnés SSE, état de l'import d'historique sont en mémoire ; un redémarrage déconnecte tout le monde.
* Interrupteurs « Sections et alertes actives » : Sortie Internationale, Traffic Utilisateur et Roaming masquent la section **et** suspendent la
  création de tickets de leur catégorie ; TCP Metrics et TDD ne règlent que l'affichage. `zero_traffic_min_samples=2` (défaut) : un « zéro » isolé
  attend une confirmation au cycle suivant.

## Tests

```bash
pip install -r requirements-dev.txt
pytest                                  # unitaires, sécurité, intégration, front statique (MariaDB requise ; sinon ignorés)
ruff check . && bandit -q -r core routers main.py -ll      # seuls les B608 documentés (SQL à colonnes fixes) sont attendus
playwright install chromium
cd tests/e2e && E2E_ALIGN=0 pytest .    # E2E navigateur sur la pile simulée (voir demo/README.md) ; ~4 min
E2E_SLOW=1 pytest test_e2e_playwright.py -k real_cycle     # attend un vrai cycle (≤ 5 min) : data_version publié depuis le thread du cycle
```

Les fichiers E2E partagent une pile et un état : ils s'exécutent dans l'ordre alphabétique (charge → pipeline → interface) ; les tickets
`99xxxx` insérés par les tests d'interface sont supprimés à la fin du module. Chromium est testé ; Chrome stable, Firefox et Safari ne le sont pas.
