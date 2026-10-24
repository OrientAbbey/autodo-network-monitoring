# Revue de code complète — AUTODO Network Monitoring

| | |
|---|---|
| **Dépôt** | `OrientAbbey/autodo-network-monitoring` (branche `main`) |
| **Commit analysé** | `87b4160` — « Initial commit: AUTODO unified network monitoring and dashboard » |
| **Date de la revue** | 5 octobre 2026 |
| **Périmètre** | 24 fichiers, ≈ 12 000 lignes (Python ≈ 6 460 · JS ≈ 1 960 · HTML ≈ 1 880 · CSS ≈ 1 400) |
| **Langue du code / UI** | Français + anglais mélangés |

---

## Mise à jour du 24 octobre 2026 — rôles, gouvernance, détecteurs « futurs » et nouvelle interface (branche `feat/roles-d-ux`)

Quatre phases au-dessus de `9ffea7c` : **A** comptes et gouvernance, **B** historique, détecteurs et simulation, **C** interface et temps réel,
**D** nettoyage, E2E migrés, documentation. Ce qui suit est l'issue réelle, y compris ce qui n'est pas fait.

### A. Ce qui a été construit

| Phase | Contenu |
|---|---|
| **A** | comptes nominatifs (scrypt, verrouillage, dernier admin protégé), sessions (hachage du jeton, révocation), rôles, contrôle d'origine (CSRF), journal d'audit, versions de configuration, seuils par base, maintenance et silences, validation / rejet / suppression des tickets, chronologie, notes vers Orange, accusés partagés, e-mails retenus jusqu'à l'approbation |
| **B** | `metrics_5min` + import, D1 saisonnier, D3 prévision/capacité, D5 CUSUM, D4 Isolation Forest et D7 qualité (optionnels), mode ombre + rapport, simulation « et si ? », API admin |
| **C** | `data_version` SSE, ETag/304, présence ; pages `/supervision` et `/admin` ; connexion, préférences, FR/EN, mini-courbes, cartes mobiles, mur d'écran, accessibilité ; actions sur la page des tickets |
| **D** | E2E migrés (aucun test supprimé sans équivalent), code mort retiré, table `logs` archivée, documentation |

### B. Défauts trouvés pendant la refonte (tous corrigés, chacun couvert par un test sauf mention)

| # | Défaut | Conséquence si non corrigé |
|---|---|---|
| 1 | Les évènements SSE étaient **nommés** (`event: update`) alors que le navigateur n'écoute que `onmessage` | le rafraîchissement temps réel **ne pouvait pas fonctionner** : les écrans ne se mettaient à jour que par le compte à rebours local |
| 2 | Silences et maintenance évalués à l'heure du *checkpoint* (arrondie jusqu'à 5 min en arrière) | une fenêtre ouverte « maintenant » pouvait ne pas s'appliquer au cycle suivant : un ticket créé pendant la maintenance |
| 3 | Prévision Holt-Winters acceptée sur un historique à moitié vide (trous « comblés ») | courbe attendue sans valeur, présentée comme fiable |
| 4 | Simulation découpée sur des minutes non alignées sur 5 min | résultats différents de la production |
| 5 | Import d'historique : mini-courbes périmées jusqu'à 60 s | l'admin ne voyait pas le résultat de son import |
| 6 | Dialogue des préférences : lien partageable figé, texte non retraduit au changement de langue | lien ne reflétant pas les réglages affichés |
| 7 | Panneau des paramètres : identifiant HTML dupliqué après enregistrement | message de confirmation introuvable (lecteurs d'écran inclus) |
| 8 | `navigator.credentials.preventSilentAccess()` hérité de l'ancien mot de passe partagé | erreur JavaScript non gérée à chaque chargement (Chromium) |
| 9 | `esc` (alerts.js, tickets.js) et `UI` (tickets.js) déclarés en double dans la portée globale partagée | « Identifier has already been declared » : script entier non exécuté. Un test statique détecte désormais ces collisions |
| 10 | *(piège évité en cours de route, pas un défaut préexistant)* le motif et les dernières valeurs d'un ticket changent à chaque cycle | le journal de ticket se serait rempli d'une ligne « mise à jour » toutes les 5 minutes ; seuls les changements de catégorie sont journalisés |

### C. Validation

* `pytest` : **287** tests (comptes, rôles, gouvernance, workflow des tickets, notes Orange, détecteurs, mode ombre, simulation, temps réel, front statique, intégration MariaDB) ; `ruff` propre ; `bandit -ll` : uniquement les B608 documentés (SQL à colonnes fixes, valeurs liées) ; `node --check` sur tous les scripts.
* E2E Playwright (Chromium) sur la pile simulée : **72** tests verts + 1 test lent (`E2E_SLOW=1`, attend un vrai cycle) vert : visiteur, connexion/déconnexion, changement de mot de passe forcé, paramètres (version + audit), seuils par base, maintenance, validation/rejet (le ticket validé **arrive chez Orange**, celui qui attend reste retenu), commentaire/retour/suppression, comptes, sessions révocables, santé, import d'historique, détecteurs, simulation, accusé partagé entre deux navigateurs, son désactivé par défaut, mur d'écran, lien de préférences sans le son, anglais, cartes mobiles, mini-courbes, XSS, absence de violation CSP, **50 spectateurs** (médiane ≈ 135 ms, p95 ≈ 220 ms, 0 erreur, 50 flux SSE).
* Captures avant / après : `demo/screenshots/avant_*.png` (commit `9ffea7c`) et `apres_*.png`, `C*.png`.

### D. Changements de comportement à connaître avant déploiement

1. `CONFIG_PASSWORD`, `DATA_MANAGEMENT_PASSWORD`, `TOKEN_TTL` n'existent plus. Créer `ADMIN_USERNAME` / `ADMIN_PASSWORD` (admin initial, changement forcé), puis **retirer `ADMIN_PASSWORD` du `.env`**.
2. `POST /update_config`, `POST /api/auth`, `/api/data-management` sont supprimés ; `/data-management` redirige vers `/admin`. `POST /api/dashboard_data` devient `GET /api/dashboard_data` (filtres en paramètres d'URL, ETag).
3. Évènements SSE sans nom ; tout client externe qui écoutait `event: update` doit passer à `onmessage` (type `data_version`, `ack`, `config_updated`, `tickets_changed`).
4. Migrations 4, 5 et 6 (nouvelles tables, colonnes de `tickets_tb`, `logs` → `logs_archived`) s'appliquent seules au démarrage ; **sauvegarder la base locale avant la première mise en service**. Il n'existe pas de migration descendante.
5. `config.json` gagne une section `overrides` ; un ancien fichier sans cette section reste lu.
6. Statut de ticket supplémentaire `deleted` (suppression logique) et états de synchronisation `held` (en attente de validation) ; les consommateurs externes de `tickets_tb` doivent les ignorer ou les traiter.
7. Un ticket créé alors que `ticket_validation_required` est actif **n'est ni annoncé par e-mail ni envoyé à Orange** avant l'approbation d'un superviseur.
8. Un seul worker (sessions, cache d'alertes, abonnés SSE, état de l'import en mémoire) : un redémarrage déconnecte tout le monde.

### E. Reste à faire ou à confirmer

* **Format de la note vers l'API Orange réelle** : `OrangeClient.add_note` suppose `{"note":[{text, author, date}]}` en `PUT` + `X-HTTP-Method-Override: PATCH` ; à confirmer et ajuster dans cette seule méthode. Le faux Orange de la démo l'accepte, ce qui ne prouve rien sur l'API réelle.
* **Navigateurs** : seul Chromium est testé ; Chrome stable, Firefox et Safari ne le sont pas (comportement réel de l'autoplay audio notamment).
* **Détecteurs jamais évalués sur des données réelles** : les chiffres du prototype viennent de séries simulées. D1 ignore jours fériés et distinction ouvré / week-end ; D4/D7 exigent des données (≥ 3,5 jours exploitables par base ; ≥ 100 retours étiquetés des deux classes).
* Non réalisés (voir le tableau de `ROADMAP.md` §0) : corrélation entre bases (D6), prédiction du TTR et file « à confirmer », mises à jour différentielles (T3), feux tricolores (U2), centre d'alertes (U4), raccourcis clavier (U8), export CSV/PDF des tickets (U9), sons distincts et escalade (S2/S4), contrastes AA non mesurés, approbation à deux personnes.
* La table `logs_archived` est à supprimer à la main après vérification (`DROP TABLE logs_archived`).
* Traductions : l'aide longue du tableau de bord et les textes produits par le serveur restent en français.

---

## Mise à jour du 8 octobre 2026 — refonte appliquée (bundle Git `revue/refonte-sqlalchemy`)

Les constats de ce rapport ont **tous été traités** (les 🟡 de la première livraison ont été levés ensuite) dans une refonte livrée sous forme de bundle Git (commits thématiques
au-dessus de `87b4160`, patches individuels fournis). Le premier patch (`correctifs_critiques.patch`) est **remplacé** par ce
bundle. Les sections 1 à 9 ci-dessous conservent l'analyse initiale ; cette section en donne l'issue.

### A. Décisions d'architecture demandées

| Sujet | Décision | Justification (détail : `README.md` § Décisions techniques) |
|---|---|---|
| Accès base | **SQLAlchemy 2.1.3 (Core) + PyMySQL 1.2.3**, pour tout le projet ; mysql-connector retiré | À l'échelle réelle (≈ 19 lectures de 12 lignes / 5 min, base locale peu chargée) les performances sont équivalentes (0,52 vs 0,56 ms ; ≈ 1 550–1 700 req/s à 16 threads) ; les différences décisives sont structurelles : le pool natif de mysql-connector **échoue immédiatement** quand il est épuisé (600 erreurs / 960 requêtes, pool de 6, 16 threads) alors que SQLAlchemy attend ; transactions sans piège (`engine.begin()`) ; upsert de 5 000 lignes 121 ms contre 214 ms ; garde-fou lecture seule par événement `connect` |
| Bases DIAGNET (lecture seule) | 1 moteur par (hôte, port, base), `pool 1+1`, `AUTOCOMMIT`, session **`READ ONLY`** + délai d'exécution serveur, `pre_ping`, recyclage 240 s | vérifié par test : même avec un compte disposant de droits d'écriture, `INSERT`/`DELETE` sont refusés |
| Base locale (lecture + écriture) | pool 8+8 ; écritures **ciblées et versionnées**, une transaction par cycle, `INSERT` strict, ID atomique, index, pagination, plages sargables, cache TTL 3 s sur les alertes, tampon mémoire des visites (1 écriture / 10 s), purge de rétention, migrations versionnées **sans Alembic** | voir §D6 du README |
| Synchrone / threads / asyncio | Collecte DIAGNET = `ThreadPoolExecutor` borné (parallèle) ; détection + dashboard = séquentiel Python pur ; Orange et e-mails hors du cycle (outbox / exécuteur dédié) ; API : endpoints `def` ou `asyncio.to_thread`, `async def` seulement pour le SSE | E/S distantes bloquantes ⇒ threads ; asyncio n'apporte rien sans pilote asynchrone fiable |
| Polars | **retiré** | ≈ 120 lignes par cycle : un DataFrame coûte plus qu'une boucle ; les agrégations lourdes restent dans MySQL |
| Parallélisation | uniquement la collecte DIAGNET : 19 bases en ≈ 0,4–0,6 s (démo locale) au lieu de séquentiel avec délais de 60 s | 1 connexion par base et par cycle (au lieu de 3), mode 7 j en cache 30 min, requête APN seulement pour les bases « utilisateur » |
| Limites de débit API externes | `TokenBucket` + concurrence bornée + `Retry-After` + disjoncteur + backoff exponentiel **stocké en base** + abandon borné ; SMTP : connexion unique, plafond d'envois/min | testé avec horloge simulée et faux serveur Orange (429) |
| Dépendances | bornées `>=min,<=version validée` (`requirements.txt`) + `requirements.lock.txt` | |
| Refactor `process_ticket_logic` | `rules.plan_actions` : **4 règles** (au lieu de 16 branches), **équivalence prouvée 16/16** avec l'ancienne implémentation avant sa suppression ; la table des 16 cas est conservée en test | |
| Arborescence | **2 paquets seulement** : `core/` (13 modules plats) et `routers/` ; `clients/`, `repositories/`, `domain/`, `services/`, `notifications/` **non créés** ; + `tests/` et `demo/` | un module = une responsabilité (`rules.py` pur, `tickets.py` dépôt, `orange.py` client+outbox, `notify.py`, `collector.py`, `monitoring.py`…) |

### B. Issue de chaque constat

✅ = corrigé et couvert par un test · 🟡 = corrigé en partie (plus aucun point jaune après la mise à jour du 8 octobre, soir)

| Réf. | Issue | Où / preuve |
|---|---|---|
| C-01 | ✅ | `db.local_tx()` (COMMIT/ROLLBACK) ; `test_writes_are_committed_and_visible…` |
| C-02 | ✅ | `collector` + `monitoring.build_series` ; `test_series_builder_and_detection_see_the_loss_counters` |
| C-03 | ✅ | `rules.plan_actions` + isolation par base ; équivalence 16/16 ; E2E « 2ᵉ cycle sans doublon » |
| C-04 | ✅ | `core/auth.py` (portées, fail-closed, anti brute-force) ; `tests/test_security.py` + E2E |
| C-05 | ✅ | auto-login ExNet **supprimé** (pas de SSO disponible : l'utilisateur s'authentifie sur ExNet) ; liens via `/go/…`. **Action manuelle restante : faire pivoter les identifiants ExNet déjà exposés** |
| C-06 | ✅ | `TemplateResponse(request, …)`, `app.state.templates`, dépendances bornées ; E2E pages 200 |
| C-07 | ✅ | aucun calcul dans la requête (503 tant que le 1ᵉʳ cycle n'est pas fini), E/S via threads, `RLock` |
| C-08 | ✅ | TLS vérifié, aucun secret loggé, plus de CUID/ressource en dur (variables d'environnement) |
| M-01 | ✅ | `orange.format_description` ; test |
| M-02 | ✅ | outbox + backoff en base + limiteur ; création idempotente autant que l'API le permet (recherche `externalId` optionnelle, `ORANGE_API_LOOKUP_EXTERNAL_ID`) |
| M-03 | ✅ | `UPDATE … WHERE version = :v` ; `test_optimistic_locking_detects_stale_writers` |
| M-04 | ✅ | config relue par cycle (cache mtime), bornes, fusion partielle, écriture atomique, hors dépôt (`DATA_DIR`) ; **Sortie Internationale, Traffic Utilisateur et Roaming** masquent la section *et* suspendent la création de tickets (roaming désormais supervisé quand activé) ; TCP Metrics et TDD = affichage seulement, **dit explicitement dans l'interface** (« Sections et alertes actives ») |
| M-05 | ✅ | source injoignable ⇒ alerte interne, **jamais de ticket Orange** ; seuils de déclaration / levée séparés (70 / 35 min) ; `last_update` inconnu traité à part ; `zero_traffic_min_samples` = **2 par défaut** (un « zéro » isolé attend une confirmation au cycle suivant ; 1 rétablit l'ancien comportement) |
| M-06 | ✅ | agrégats sur compteurs bruts ; `test_metrics.py` |
| M-07 | ✅ | NULL-safe + isolation par base ; test |
| M-08 | ✅ | collecte parallèle, 1 connexion/base, `max_instances=1` effectif (tout dans le job) |
| M-09 | ✅ | échappement HTML + **CSP stricte** (scripts inline extraits, handlers `onclick` remplacés) ; E2E « aucune violation CSP » et XSS |
| M-10 | ✅ | table compteur atomique, `INSERT` strict, suffixe à 4 chiffres au-delà de 999 ; 3 tests |
| M-11 | ✅ | `TRUSTED_PROXIES`, UUID, tampon de visites, purge de rétention |
| M-12 | ✅ | routes protégées, erreurs génériques + vrais codes HTTP, CORS fermé, plus d'hôtes internes dans le JSON, stub `dismiss` supprimé |
| M-13 | ✅ | statuts terminaux en liste blanche (statut absent ⇒ aucune fermeture), heure de Douala, motif, restauration rejouée via l'outbox |
| M-14 | ✅ | `ZoneInfo` unique, plus de `+1 h` codé en dur ; migration `logs.access_time` → `DATETIME`. Convention : DIAGNET en UTC, tickets en heure locale (inchangé pour la compatibilité) |
| Mo-01 | ✅ | un seul handler racine ; les logs d'e-mail arrivent enfin dans le fichier ; erreurs en `exception` ; logs sans émojis |
| Mo-02 | ✅ | `/health` réel, `/api/status`, **watchdog** (aucun cycle réussi depuis N min ⇒ e-mail interne). Pas d'export Prometheus |
| Mo-03 | ✅ | SMTP borné, 1 connexion, plafond/min, hors thread de détection, HTML échappé en tables, état en base, destinataires hors dépôt |
| Mo-04 | ✅ | migrations, index, pagination, plages sargables, `VALUES()` déprécié retiré, purge |
| Mo-05 | ✅ | `next_tick` correct (plus de boucle à 1 Hz), tâche qui survit aux exceptions, plafond d'abonnés SSE |
| Mo-06 | ✅ | plus d'import circulaire ; `core/__init__` vide |
| Mo-07 | ✅ | README, Dockerfile non-root, unité systemd durcie, CI, dépendances bornées/verrouillées, mono-worker documenté |
| Mo-08 | ✅ | champ TTR corrigé ; `script.js` (≈ 1 930 lignes) **découpé en 8 scripts par responsabilité** (`static/js/`, 49 à 454 lignes) ; pop-up `role="alertdialog"` avec gestion du focus et `Échap` ; libellés ARIA, `aria-pressed`, `aria-live` ; **débordements mobiles corrigés** (barre de boutons, pop-up) avec test d'overflow ; gzip + revalidation des statiques ; version de Chart.js documentée. Scripts classiques (pas d'ES modules ni de bundler) pour rester compatibles avec la CSP et l'existant |
| Mo-09 | ✅ | code mort supprimé (≈ 1 500 lignes) ; `_process_data_for_interval` **435 → ≈ 110 lignes** pilotées par table (`_compute_metrics`, `_block`, `_entry`, `_tdd_entry`), sortie **strictement identique** à l'ancienne vérifiée par un test « golden » (`tests/test_metrics_golden.py`, 7 bases dont vide, partielle, nulle, injoignable, TDD présent/absent) |
| Mo-10 | ✅ | valeurs métier Orange et réglages en variables d'environnement ; IP DNS de référence (`DNS_REFERENCE_IPS`), largeur de classe du mode (`MODE_BIN_GBPS`) et diviseur de débit (dérivé de `DEBIT_FACTOR`) passés en **paramètres liés** des requêtes |
| Mo-11 | ✅ | **91 tests** pytest + **24 tests** Playwright + `ruff` + `bandit` ; CI fournie |
| Mineurs | ✅ | `any` → `Any`, démarrage refusé si la base est inaccessible, plus de `except:` nus, défauts de configuration uniques, docstrings obsolètes supprimées, typo `CALYPSO` corrigée, références de tâches asyncio conservées ; typage moderne (`dict`, `list`, `X \| None`) appliqué à tout le code par `ruff` |

### C. Validation (sur le code final, et sur un clone neuf du bundle)

| Suite | Résultat |
|---|---|
| `pytest` (règles ×16 combinaisons, limiteur/Orange, sécurité HTTP, intégration MariaDB, métriques + golden) | **98 / 98** |
| Playwright + HTTP (pile simulée : 19 bases, faux Orange, puits SMTP) : pages, CSP, XSS, accessibilité clavier/ARIA, mobile, **Chromium 141 et Google Chrome stable 141**, synchronisation SSE multi-utilisateurs, **50 spectateurs simultanés** (médiane 69-117 ms, p95 169-238 ms, 0 erreur) | **34 / 34**, dont « 2ᵉ cycle sans doublon ni erreur » |
| `ruff check .` | aucune erreur ; `bandit` : seul avertissement B608 (noms de colonnes issus de listes blanches, valeurs toujours liées) |
| Clone du bundle (`git clone`) : pytest + sous-ensemble E2E | ✅ |

### D. Changements de comportement à connaître avant déploiement

1. **Auto-login ExNet supprimé** (la solution sûre exige un SSO ou des comptes nominatifs, indisponibles) ; faire pivoter les identifiants déjà exposés.
2. **Variables d'environnement** : `CONFIG_PASSWORD` / `DATA_MANAGEMENT_PASSWORD` obligatoires (sinon accès refusé) ; `CLIENT_USER_ID` et `ORANGE_RELATED_RESOURCE_ID` n'ont plus de valeur par défaut ; `EMAIL_RECIPIENTS` ou `email_recipients.txt` (non versionné).
3. **Écoute** : `APP_HOST=127.0.0.1` et port **8501** par défaut (avant : `0.0.0.0:501`) — prévoir un reverse proxy TLS ou `APP_HOST=0.0.0.0`.
4. **Configuration d'alerte** déplacée dans `DATA_DIR/config.json` (copie automatique de l'ancien `config.json`) ; `day_over_day_drop_percent` supprimé (règle inexistante).
5. **Orange** : les tickets sont désormais créés par une tâche périodique (60 s par défaut) et non plus pendant le cycle de détection.
6. **Schéma** : migrations automatiques au démarrage (colonnes `version`, `orange_*`, index, `ticket_counters`, `notification_state`, `schema_version`). Sauvegarder `tickets_tb` avant la première mise à jour.
7. **Limites connues** : la réconciliation Orange est testée au niveau intégration, pas en E2E (cron de 15 min) ; seuls Chromium et Chrome sont disponibles dans l'environnement de test (Firefox/WebKit non exécutés) ; le prototype de détection (`ROADMAP.md`) repose sur des données simulées.
8. **`zero_traffic_min_samples=2`** : un trafic nul n'ouvre un ticket qu'après deux relevés consécutifs (≈ 5 min de délai supplémentaire) ; remettre à 1 pour l'ancien comportement.

---

> **Évolutions proposées** (détection ML/statistique avec prototype chiffré, UX/UI, son, administration, 25-50 spectateurs) : voir `ROADMAP.md` dans le dépôt.

## 0. Méthode, limites et légende

**Méthode.** Lecture intégrale du code applicatif (`main.py`, `core/*`, `routers/*`, `templates/*`, `static/script.js`), puis **exécution de tests ciblés** pour confirmer les bugs suspectés plutôt que de les affirmer sur la seule lecture :

- Python 3.12 + **MariaDB 10.11 local** pour les tests base de données ;
- versions réellement résolues aujourd'hui par `pip install -r requirements.txt` (les dépendances sont en `>=`) : **FastAPI 0.142.2, Starlette 1.7.0, Pydantic 2.13.5, polars 1.44.2, mysql-connector-python 26.7.0** ;
- analyse statique : `ruff` (règles F, B, S), `bandit`, `vulture` ;
- `TestClient` FastAPI pour les tests d'authentification et de rendu des pages.

Tout est livré avec la revue :

- `demo/` : **source de données simulée** (19 bases DIAGNET fictives, faux API Orange, puits SMTP, injection d'anomalies à chaud) et **tests Playwright** (Chromium 141) + tests unitaires ;
- `autodo-network-monitoring.bundle` (+ `patches/`) : refonte complète en 10 commits sur `87b4160`, **validée par les tests** (voir la section « Mise à jour » ci-dessus).

**Limites.**

- Pas d'accès aux bases DIAGNET réelles, à l'API Orange, ni à `db_config.json` (gitignoré). La logique de détection est donc évaluée par lecture, et les tests utilisent des **données simulées** (mais passent par le vrai code applicatif).
- Frontend : analysé par lecture (source et sink des XSS identifiés), sans exécution dans un navigateur.
- Le fichier `REVUE_CODE.md` cité dans `.gitignore` **n'est pas présent dans le dépôt cloné** : il n'a donc pas été consulté. Cette revue est indépendante.

**Légende.**
🔴 Critique (perte de données, compromission, service inopérant) · 🟠 Majeur · 🟡 Moyen · 🔵 Mineur
✅ **Reproduit** par exécution · 📖 **Établi par lecture** du code

---

## 1. Synthèse exécutive

### 1.1 Verdict

Le projet est ambitieux et la logique métier est réfléchie : fenêtre de persistance avant fermeture d'un ticket (hystérésis), mode statistique sur 7 jours, matrice de 16 cas trafic/pertes, rafraîchissement par SSE, démarrage/arrêt propres du scheduler, SQL paramétré (aucune injection trouvée), secrets absents du dépôt.

**Mais, dans l'état actuel du code, la chaîne centrale « collecte → détection → ticket → Orange → e-mail » ne peut pas fonctionner de bout en bout, et l'application est exposée.** Cinq défauts, tous reproduits, suffisent à le dire :

1. 🔴 **Les tickets ne sont jamais validés en base** : `save_tickets()` n'appelle pas `commit()` (C-01).
2. 🔴 **Les alertes « pertes » sont aveugles** : les compteurs de pertes lus en base sont remplacés par des valeurs par défaut (0 / 1) (C-02).
3. 🔴 **Le moteur de tickets plante** : un paramètre est appelé `values_to_update` mais défini `values_to_updated`, et une méthode n'a pas de `self` (C-03).
4. 🔴 **Les 3 pages HTML renvoient une erreur 500** avec les versions de FastAPI/Starlette installées aujourd'hui (C-06).
5. 🔴 **Des endpoints sensibles n'ont aucune authentification** (modification des seuils d'alerte, et **diffusion des identifiants ExNet en clair** à n'importe quel visiteur) (C-04, C-05).

> ⚠️ **Interaction à connaître.** Les défauts C-01 et C-03 se masquent l'un l'autre. Tant que C-01 n'est pas corrigé, aucun ticket n'est jamais « ouvert » en base : chaque cycle de 5 minutes **recrée** un ticket (donc un ticket Orange et un e-mail) pour le même incident. Une fois C-01 corrigé, le moteur plante (C-03) dès qu'un ticket ouvert existe. **Il faut corriger C-01, C-02 et C-03 ensemble**, puis valider avec les tests de l'annexe B.

> 💡 Ces défauts ont la signature de **régressions de refactoring** (renommage incomplet d'un paramètre, colonne renommée dans l'ancienne fonction mais pas dans la nouvelle, `commit()` oublié lors d'un passage au pool). L'absence totale de tests automatisés (aucun fichier de test, aucune CI) explique que rien ne l'ait détecté. C'est le levier d'amélioration n° 1 à long terme.

### 1.2 Bilan chiffré

| Sévérité | Nb | Thèmes principaux |
|---|---:|---|
| 🔴 Critique | **8** | Persistance, détection pertes, moteur de tickets, authentification, fuite d'identifiants, rendu des pages, interblocage, TLS/secrets dans les logs |
| 🟠 Majeur | **14** | Description Orange corrompue, retries Orange, mises à jour concurrentes perdues, config périmée, faux positifs, agrégats faux, XSS stocké, collisions d'ID, confiance aveugle aux en-têtes, perf. de collecte |
| 🟡 Moyen | **11** | Logging, auto-supervision, e-mail, schéma SQL, SSE, architecture, packaging, front, code mort, valeurs en dur, tests |
| 🔵 Mineur | **≈ 12** | Style, typage, docstrings obsolètes, avertissements d'analyse statique |

### 1.3 Les 12 correctifs à faire en premier (ordre recommandé)

| # | Action | Réf. | Effort |
|---|---|---|---|
| 1 | **Faire pivoter** les identifiants ExNet (considérés comme compromis si l'app a été joignable) puis fermer `/api/exnet-auth-url` | C-05 | 1 h |
| 2 | Protéger `/update_config` ; rendre les mots de passe **fail-closed** ; séparer les jetons `cfg` / `dm` | C-04 | 3 h |
| 3 | Ajouter `commit()` / transaction dans `save_tickets` (et un gestionnaire de contexte commun) | C-01 | 1 h |
| 4 | Corriger le renommage des colonnes `#tcp_loss_dw` / `#tcp_pkt_dw` | C-02 | 1 h |
| 5 | `values_to_updated` → `values_to_update` + `@staticmethod` + try/except **par base** | C-03 | 1 h |
| 6 | Nouvelle signature `TemplateResponse(request, name, ctx)` + figer les dépendances | C-06 | 1 h |
| 7 | Supprimer l'interblocage du cache (`RLock` / réponse 503) et sortir les I/O bloquants de la boucle asyncio | C-07 | 2 h |
| 8 | Retirer `verify=False` et les secrets des logs (Orange) | C-08 | 2 h |
| 9 | Corriger la description Orange (précédence `*`) | M-01 | 15 min |
| 10 | Brancher le scheduler dans `OrangeAPIClient` et revoir les retries | M-02 | 3 h |
| 11 | Générer les ID de ticket de façon atomique (table compteur) ; `INSERT` strict à la création | M-10 | 3 h |
| 12 | Mettre en place pytest + CI avec les tests du dossier `demo/tests` | §7 | 1 j |

---

## 2. Architecture constatée

```
                          ┌────────────────────────── FastAPI (1 processus, port 501) ──────────────────────────┐
 APScheduler (thread)     │                                                                                      │
  ├─ cron 1-59/5 ────────►│ run_unified_data_collection_and_processing(config figée au démarrage)               │
  │                       │      ├─ DataCollector.fetch_unified_data_for_processing  (séquentiel, N bases)       │
  │                       │      ├─ Thread « DashboardProcessor » ─► precalculate_dashboard_data ─► DASHBOARD_CACHE
  │                       │      └─ Thread « MonitoringProcessor » ─► détection ─► TicketManager ─► tickets_tb  │
  ├─ cron 0,17,32,47 ────►│ monitor_orange_ticket_status   (ferme localement si Orange ≠ InProgress)             │
  └─ cron 12,42 ─────────►│ retry_tickets_missing_orange_ids                                                     │
                          │                                                                                      │
 asyncio task ───────────►│ sse_tick_task ──► /events (SSE) ──► navigateurs                                      │
 Navigateur ─── POST /api/dashboard_data ─► DASHBOARD_CACHE + alertes (SELECT tickets_tb)                        │
                          └──────────────────────────────────────────────────────────────────────────────────────┘
 Externes : N bases MySQL DIAGNET (lecture) · MySQL local « tickets_db » · API Orange CALYPSO (OAuth2) · SMTP · portail ExNet
```

**Contraintes implicites non documentées** : l'application doit tourner avec **un seul worker** (jetons de session, cache du dashboard, scheduler et abonnés SSE sont en mémoire ; plusieurs workers dupliqueraient les jobs et les tickets). À écrire dans un README.

**Points forts à conserver**

- Séparation `core/` (métier) / `routers/` (HTTP) / `templates` + `static`.
- Pools de connexions, SQL **paramétré** partout (les `f-string` SQL signalés par `bandit` ne construisent que des listes de `%s` ou des clauses constantes : faux positifs vérifiés).
- Utilisation de polars pour les métriques vectorisées.
- Machine à états des tickets avec **fenêtre de persistance** avant fermeture, ratios de récupération configurables.
- Arrêt propre (scheduler + SSE) et `.env.example` détaillé, `.gitignore` soigné (`.env`, `db_config.json`, caches de token).
- Échappement Jinja2 actif par défaut ; pas de `eval`, pas de secret versionné.

---

## 3. Constats critiques 🔴

> Chaque constat indique **où**, **ce qui se passe**, la **preuve** (✅ reproduit / 📖 lecture), l'**impact** et la **correction**. Les numéros de ligne se rapportent au commit `87b4160`. Le patch livré (`correctifs_critiques.patch`) applique toutes les corrections de cette section (voir la matrice de couverture au §8.3).

### C-01 — Les tickets ne sont jamais validés en base (`commit()` manquant) ✅

**Où** : `core/ticket_manager.py:777-780` (`save_tickets`), pool créé avec `autocommit=False` + `pool_reset_session=True` (`core/database_manager.py:54-66`).

**Constat.** `save_tickets()` exécute un `executemany(INSERT … ON DUPLICATE KEY UPDATE)` dans `with get_local_db_connection() as conn:` **sans `conn.commit()`**. Avec `mysql-connector-python`, la sortie du `with` sur une connexion poolée appelle `close()` → `reset_session()` : la transaction non validée est **annulée**. La fonction renvoie pourtant `len(params)` (« succès »).

**Preuve ✅.** MariaDB 10.11 + connecteur 26.7.0 : `save_tickets([ticket])` retourne `1`, puis un `SELECT COUNT(*) FROM tickets_tb` depuis une **nouvelle** connexion renvoie `0`. Avec un `commit()` explicite, la ligne apparaît. Sur la démo : après un cycle complet, `tickets_tb` contient **0 ligne** alors que 4 tickets Orange ont été créés et un e-mail envoyé.

**Impact.** Aucun ticket persistant : la page *Tickets*, la section *Alertes* du dashboard et la déduplication (`has_open_ticket`) sont vides ; **chaque cycle de 5 min recrée les mêmes incidents** côté Orange (spam de tickets) et par e-mail. Les tickets déjà créés côté Orange ne sont jamais fermés par AUTODO.

> Si, dans votre production, des tickets sont malgré tout persistés, il faut comprendre pourquoi avant tout (version du connecteur, configuration du pool) : le comportement ci-dessus est celui de `mysql-connector-python` ≥ 9.5 avec cette configuration.

**Correction.** Un gestionnaire de transaction unique, à utiliser partout où l'on écrit :

```python
# core/database_manager.py
from contextlib import contextmanager

@contextmanager
def local_db(commit: bool = True):
    """COMMIT à la sortie normale, ROLLBACK sur exception, connexion toujours rendue au pool."""
    conn = get_local_db_connection()
    try:
        yield conn
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

# core/ticket_manager.py
def save_tickets(tickets):
    ...
    try:
        with local_db() as conn:
            with conn.cursor() as cur:
                cur.executemany(sql, params)
        return len(params)
    except Exception:
        logger.exception("Error saving tickets")   # exception complète, pas un simple message
        return 0
```

Compléments : vérifier la valeur de retour de `save_tickets` à l'appel (`create_ticket` ne la contrôle pas : un échec d'écriture est silencieux) ; ne plus utiliser `with conn:` sur une connexion poolée sans transaction explicite.

---

### C-02 — Les alertes « pertes » sont aveugles (compteurs de pertes remplacés par 0 / 1) ✅

**Où** : `core/database_manager.py` — `fetch_unified_data_for_processing`, lignes ≈ 481-502 (construction de `df_existing`, `join(how="right")`, `select(data_cols)`). L'ancienne fonction `load_all_databases_data_sync` (≈ 231-244, **code mort**) renommait bien `#tcp_loss_dw → tcp_loss_dw`.

**Constat.** Les lignes lues en base portent les colonnes `#tcp_loss_dw` et `#tcp_pkt_dw` (préfixe `#`). La version « unifiée » ne les renomme pas. Le `select` demande `tcp_loss_dw` / `tcp_pkt_dw`, qui n'existent que dans le DataFrame **par défaut** (`0` et `1`) : ce sont donc ces valeurs par défaut qui sont utilisées.

**Preuve ✅.** Avec des lignes simulées à **90 % de pertes** passées dans le vrai chemin `fetch_unified_data_for_processing → calculate_vectorized_metrics`, `loss_percentage` vaut `[0.0, 0.0, 0.0]`. Sur la démo (scénario `loss` à 8 % sur `wacs_ic`), le tableau de bord affiche bien 8 % mais **aucun ticket « pertes » n'est créé** avec le code d'origine ; il l'est avec le correctif.

**Impact.** Plus aucune alerte de pertes (et donc plus de rattachement « pertes » aux tickets de trafic) : l'un des deux piliers de la détection est inopérant, sans aucune erreur dans les logs.

**Correction.** Construire le DataFrame de façon explicite, en distinguant trois cas (base injoignable / créneau manquant / créneau présent) :

```python
def build_monitoring_df(db_rows: dict, alert_timestamps: list) -> pl.DataFrame:
    schema = {"SAUV_td32": pl.Utf8, "tcp_dw": pl.Float64, "udp_dw": pl.Float64, "icmp_dw": pl.Float64,
              "other_dw": pl.Float64, "tcp_loss_dw": pl.Float64, "tcp_pkt_dw": pl.Float64,
              "connection_failed": pl.Boolean, "failure_reason": pl.Utf8}
    failed = not db_rows or all(v is None for v in db_rows.values())      # base injoignable
    records = []
    for ts in alert_timestamps:
        r = None if failed else db_rows.get(ts)
        if failed:
            rec = dict(tcp_dw=0.0, udp_dw=0.0, icmp_dw=0.0, other_dw=0.0, tcp_loss_dw=0.0, tcp_pkt_dw=1.0,
                       connection_failed=True, failure_reason=None)
        elif r and any(r.get(c) is not None for c in ("tcp_dw", "udp_dw", "icmp_dw", "other_dw")):
            rec = {c: float(r.get(c) or 0) for c in ("tcp_dw", "udp_dw", "icmp_dw", "other_dw")}
            rec.update(tcp_loss_dw=float(r.get("#tcp_loss_dw") or 0),          # ← le renommage manquant
                       tcp_pkt_dw=float(r.get("#tcp_pkt_dw") or 0),
                       connection_failed=False, failure_reason=None)
        else:                                                                   # créneau absent : donnée MANQUANTE
            rec = dict(tcp_dw=None, udp_dw=None, icmp_dw=None, other_dw=None, tcp_loss_dw=None, tcp_pkt_dw=None,
                       connection_failed=False, failure_reason="no_data")
        records.append({"SAUV_td32": str(ts), **rec})
    return pl.DataFrame(records, schema=schema)
```

et, côté détection, ne pas évaluer les règles si une valeur de la fenêtre est `None` (`check_traffic_conditions_advanced` / `check_pertes_conditions_advanced` : `return False, "Données manquantes", …`). Supprimer `load_all_databases_data_sync` et `fetch_all_data_concurrent` (code mort). Un test de non-régression est fourni (`test_c02_loss_counters_from_db_are_used`).

---

### C-03 — Le moteur de tickets plante : mauvais nom d'argument et méthode sans `self` ✅

**Où** : `core/ticket_manager.py` — définition `update_ticket_status(…, values_to_updated=None)` (l. 934) ; **16 appels** `values_to_update=` dans `process_ticket_logic` (l. 1218-1398) et 1 dans `process_db_down_logic` (l. 1448) ; `add_pertes_message_to_traffic_ticket` (l. 1123) déclarée dans la classe **sans `self` ni `@staticmethod`** mais appelée via `self.`.

**Preuve ✅.**
```
TicketManager.update_ticket_status() got an unexpected keyword argument 'values_to_update'
TicketManager.add_pertes_message_to_traffic_ticket() takes 3 positional arguments but 4 were given
```
Sur la démo, 2ᵉ cycle (une fois C-01 contourné) : `Traceback … TypeError` et abandon du cycle.

**Impact.** Dès qu'un ticket ouvert existe (cas 2-4, 6-8, 10-12, 14-16 de la matrice) ou qu'un ticket « trafic + pertes » doit être créé, **l'exception remonte jusqu'à `process_monitoring_data` (l. 1533) qui abandonne le cycle entier** : les bases suivantes ne sont pas traitées, rien n'est sauvegardé, aucun e-mail n'est envoyé. Aucun ticket ne peut être mis à jour ni fermé.

**Correction.**
```bash
sed -i 's/values_to_updated/values_to_update/g' core/ticket_manager.py
```
```python
@staticmethod
def add_pertes_message_to_traffic_ticket(ticket: dict, loss_percentage: float, timestamp: str) -> None: ...
```
et **isoler chaque base** pour qu'un incident sur l'une n'arrête plus tout :
```python
for db_key in all_data_monitoring:
    try:
        ...                       # détection + tickets pour db_key
    except Exception:
        logger.exception("Échec du traitement de %s (cycle poursuivi)", db_key)
```
À moyen terme, remplacer les 16 branches de `process_ticket_logic` par une décomposition en règles (voir M-03/§6) et protéger la matrice par un test paramétré sur les 16 cas.

> ⚠️ C-01 et C-03 sont à corriger **ensemble** (voir §1.1).

---

### C-04 — Authentification : endpoints de configuration ouverts, mots de passe « fail-open », jetons confondus ✅

**Où** : `routers/dashboard.py` — `/update_config` (l. 296-406), `/api/auth` (l. 431-456), `_verify_dm_token` (l. 154-170), `data_management` (l. 497-580) ; `core/constants.py` (`TOKENS`).

**Constats (tous reproduits avec `TestClient` / navigateur).**

1. **`POST /update_config` ne vérifie aucune authentification.** Le cookie `cfg` posé par `/api/auth` n'est jamais contrôlé : la fenêtre de mot de passe n'existe que dans le JavaScript. N'importe quel client peut modifier les seuils d'alerte (par ex. `alert_traffic_threshold_percent = 99`) et donc **désactiver de fait la détection**. Démo : `200` sans cookie, seuil modifié dans `config.json`.
2. **Fail-open.** `/api/auth` compare `data.get("password") != os.getenv("CONFIG_PASSWORD")` : si la variable n'est pas définie (`None`) et que le corps ne contient pas de mot de passe (`None`), la comparaison est **vraie** → authentifié. `.env.example` fournit des valeurs vides. Même défaut côté data-management : `if dm_password and password != dm_password` → mot de passe vide ⇒ **tout mot de passe accepté**.
3. **Mise à jour partielle destructrice.** `ConfigParams` applique des valeurs par défaut aux champs omis : un POST partiel **réinitialise** les autres paramètres (démo : `alerts_enable_international_gi` et `traffic_up_threshold_percent` écrasés par leurs défauts, qui diffèrent de `config.json`).
4. **Jeton unique pour deux privilèges.** `constants.TOKENS` est partagé : un jeton `cfg` (mot de passe de configuration) ouvre aussi `/api/data-management` (journaux d'accès, **adresses IP des utilisateurs**). Démo : `200`.
5. Pas de limitation des tentatives (brute-force possible), comparaison non constante (`!=`), jetons jamais purgés (fuite mémoire), cookies sans `Secure`/`SameSite=Strict`, et aucune vérification serveur du jeton `cfg` (la durée de vie ne tient qu'au cookie).
6. `get_current_user` (l. 35-45, `HTTPBearer`) est défini mais **jamais utilisé** : faux sentiment de sécurité.

**Correction.** Module d'authentification unique (livré : `core/auth.py`) :

```python
def check_password(supplied, expected) -> bool:
    if not expected or not isinstance(supplied, str):      # FAIL-CLOSED
        return False
    return hmac.compare_digest(supplied.encode(), expected.encode())

class SessionStore:                                        # jetons à portée (scope) + expiration + purge
    def create(self, scope: str, ttl: int) -> str: ...
    def valid(self, token, scope=None) -> bool: ...

def require_session(scope: str):                           # dépendance FastAPI
    def dep(request: Request):
        if not sessions.valid(request.cookies.get(COOKIE_NAMES[scope]), scope):
            raise HTTPException(401, "Authentication required")
    return Depends(dep)
```
```python
@dashboard_router.post("/update_config")
async def update_config(new_config: ConfigParams, request: Request, _=auth.require_session("cfg")):
    updates = new_config.model_dump(exclude_unset=True)    # fusion PARTIELLE : champs omis conservés
    ...                                                    # écriture atomique : tmp + os.replace sous verrou
```
Compléments : refuser de **démarrer** (ou désactiver les routes) si `CONFIG_PASSWORD` / `DATA_MANAGEMENT_PASSWORD` sont vides ; anti brute-force (5 échecs / 15 min / IP, livré) ; cookies `HttpOnly`, `SameSite=Strict`, `Secure` derrière HTTPS ; journaliser **l'ancienne et la nouvelle valeur** lors d'un changement de seuil (audit) ; à terme, remplacer le mot de passe partagé par un SSO/OIDC.

---

### C-05 — Les identifiants ExNet sont renvoyés en clair à n'importe quel visiteur ✅

**Où** : `routers/dashboard.py:409-428` (`/api/exnet-auth-url`), `static/script.js:905-951`.

**Constat.** L'endpoint, **sans authentification**, accepte un `base_url` arbitraire et renvoie `…/exnet/api.py?action=authenticate&login=<EXNET_USERNAME>&passwd=<EXNET_PASSWORD>…`. Le commentaire du JS (« server-side credentials never exposed to frontend ») est **faux** : le mot de passe transite par le navigateur (visible dans l'onglet Réseau), puis par une requête **GET** (donc dans les journaux du portail, des proxys, l'historique et les en-têtes `Referer`). Le JS déclenche cet appel dès que l'ouverture d'un lien DIAGNET est bloquée par CORS, c'est-à-dire en pratique à chaque clic. De plus, le `raise HTTPException(400)` placé dans le `try` est avalé par `except Exception` et devient un **500** (bug de gestion d'erreur).

**Preuve ✅.** `POST /api/exnet-auth-url` sans cookie → `200 {"auth_url": "https://anything.example/exnet/api.py?…passwd=S3cr3t!…"}`.

**Impact.** Fuite d'un compte de service partagé vers tout poste pouvant joindre l'application. **À considérer comme compromis** si l'application a été accessible : faire pivoter ces identifiants.

**Correction.**

1. **Immédiat** : faire pivoter `EXNET_USERNAME/PASSWORD` ; désactiver l'endpoint par défaut (`EXNET_AUTOLOGIN_ENABLED=false`), exiger une session, restreindre `base_url` aux hôtes de `db_config.json`, ne plus laisser `HTTPException` être capturée :
```python
@dashboard_router.post("/api/exnet-auth-url")
async def get_exnet_auth_url(request: Request, _=auth.require_any_session()):
    if os.getenv("EXNET_AUTOLOGIN_ENABLED", "false").lower() != "true":
        raise HTTPException(403, "Auto-login ExNet désactivé")
    base_url = str((await request.json()).get("base_url", "")).rstrip("/")
    allowed = {f"https://{c['host']}" for c in constants.DB_CONFIGS.values() if c.get("host")}
    if base_url not in allowed:
        raise HTTPException(400, "base_url non autorisée")
    ...
```
2. **Cible** : ne plus transporter de mot de passe côté navigateur — demander au propriétaire d'ExNet un lien SSO / un jeton à usage unique de courte durée, ou des comptes nominatifs.

---

### C-06 — Les 3 pages HTML renvoient une erreur 500 avec les dépendances installées aujourd'hui ✅

**Où** : `routers/dashboard.py:179-181` et `data_management`, `routers/tickets.py:67` ; `requirements.txt` (toutes dépendances en `>=`).

**Constat.** Le code appelle `templates.TemplateResponse("dashboard.html", {"request": request})` (ancienne signature). Avec **Starlette 1.7.0 / FastAPI 0.142.2** (ce que `pip install -r requirements.txt` résout aujourd'hui), cet appel lève `TypeError: unhashable type: 'dict'`.

**Preuve ✅.** `GET /`, `GET /data-management`, `GET /tickets/` → **500** ; la nouvelle signature `TemplateResponse(request, "dashboard.html", ctx)` → 200. (Avec une version plus ancienne figée, les pages fonctionnent : vérifiez ce qui est réellement déployé.)

**Impact.** Plus aucune interface utilisable après une installation propre ou un `pip install -U`.

**Correction.**
```python
return request.app.state.templates.TemplateResponse(request, "dashboard.html")
return request.app.state.templates.TemplateResponse(request, "tickets.html", {"tickets": tickets, "filter_options": filter_options})
```
et, pour que cela ne se reproduise pas : dépendances **bornées et verrouillées** (`requirements.lock.txt` fourni), CI exécutant les tests de pages, et abandon de `from main import templates` (import circulaire) au profit de `app.state.templates` créé dans `main.py` (échec rapide si le dossier manque). `ORJSONResponse` est aussi déprécié dans cette version de FastAPI (à remplacer par une réponse JSON standard).

---

### C-07 — Interblocage du cache du dashboard et E/S bloquantes dans la boucle asyncio ✅

**Où** : `routers/dashboard.py:184-217` (`api_dashboard_data`), `core/background_task.py:1409`, `core/constants.py:236` (`DASHBOARD_CACHE_LOCK = threading.Lock()`).

**Constat.** Le handler (`async def`) prend `DASHBOARD_CACHE_LOCK`, et si le cache est vide appelle `precalculate_dashboard_data()` **qui reprend le même verrou** (non ré-entrant) pour publier le résultat. Dès que `_unified_data` existe mais que le cache est vide (premiers instants après démarrage, ou cache jamais rempli parce que le calcul a échoué — voir M-07), la requête **se bloque indéfiniment dans le thread de la boucle d'événements** : tout le serveur (SSE, pages, API) se fige.

**Preuve ✅.** Reproduction en 10 lignes : le handler ne termine pas en 5 s (`threading.Event.wait(5) == False`).

**Aggravant.** Dans ce même `async def`, `track_user`, `fetch_alerts_from_tickets_tb`, `log_criteria_access` (MySQL synchrone) et le pré-calcul lourd s'exécutent **dans la boucle d'événements** : chaque requête gèle les autres pendant la durée des requêtes SQL.

**Correction.**
```python
# constants.py — correctif minimal
DASHBOARD_CACHE_LOCK = threading.RLock()

# dashboard.py — correctif de fond : jamais de calcul lourd dans la requête
with constants.DASHBOARD_CACHE_LOCK:
    cached = dict(constants.DASHBOARD_CACHE)
if not cached:
    raise HTTPException(503, "Données en cours de préparation", headers={"Retry-After": "10"})
await asyncio.to_thread(track_user, request, response.headers)
cached["alertes"] = await asyncio.to_thread(fetch_alerts_from_tickets_tb, filters)
```
Le pré-calcul publie le nouveau cache par **simple affectation** d'un dictionnaire complet (les lecteurs n'ont alors pas besoin de verrou). Les endpoints qui n'utilisent que du code synchrone doivent être déclarés `def` (exécutés dans le pool de threads) plutôt que `async def`.

---

### C-08 — Orange : TLS non vérifié partout et secrets écrits dans les logs 📖

**Où** : `core/ticket_manager.py` — `verify=False` aux l. 150-156, 268-274, 415-420, 517-523 ; journalisation l. 57-65 et 143-147 ; valeurs en dur l. 44 (`'RJDF7613'`) et 244 (`"PRD124245259"`).

**Constat.**

- `verify=False` sur les appels OAuth2 (envoi de `client_id:client_secret` en Basic) et sur la création/MAJ des tickets : un homme du milieu peut **voler les identifiants** et le jeton. Les `InsecureRequestWarning` saturent par ailleurs la sortie.
- Le log DEBUG écrit `client_secret[:10]` et **`basic_auth_header[:30]`** : 30 caractères de Base64 de `client_id:client_secret` ≈ 22 octets en clair, soit **l'identifiant et potentiellement le début du secret**. Ces logs vont dans `app_unified.log` et peuvent être expédiés vers un collecteur.
- Un identifiant d'employé (`CUID`) est le **défaut codé en dur** si `CLIENT_USER_ID` est absent, et un identifiant de ressource Orange est codé en dur dans la charge utile des tickets.
- Le fichier de cache du jeton (`core/orange_token.json`) est écrit de façon non atomique et avec les droits par défaut.

**Correction.**
```python
ca = os.getenv("ORANGE_API_CA_BUNDLE")                       # CA interne éventuelle
self.verify = ca if ca else (os.getenv("ORANGE_API_VERIFY_TLS", "true").lower() != "false")
requests.post(url, auth=(self.client_id, self.client_secret), data=..., verify=self.verify, timeout=30)
logger.debug("Orange client initialisé (token=%s, tls_verify=%s)", self.token_endpoint, self.verify)   # aucun secret
```
Supprimer les valeurs métier par défaut (exiger `CLIENT_USER_ID` et un identifiant de ressource configurables ; échouer au démarrage si absents) ; écrire le jeton via un fichier temporaire + `os.replace` avec `chmod 600` (ou ne pas le persister) ; **purger les logs existants** et faire pivoter le secret OAuth si ces logs ont été partagés.

---

## 4. Constats majeurs 🟠

### M-01 — La description envoyée à Orange est corrompue (précédence de `*`) ✅
**Où** : `core/ticket_manager.py:558-563` (`_format_ticket_description`).
Python concatène les littéraux adjacents **avant** d'appliquer `*` : `f"AUTODO …\n" f"=" * 50` répète `"AUTODO Network Monitoring Alert\n="` **50 fois**, puis la description 50 fois (puis tronque à 2 500 caractères).
**Preuve ✅** : l'en-tête apparaît 50 fois et la description 50 fois dans la charge utile reçue par le faux API Orange.
**Correction** :
```python
sep = "=" * 50
return "\n".join(["AUTODO Network Monitoring Alert", sep, str(ticket_data["description"]), sep,
                  "Generated by AUTODO Monitoring System", f"Timestamp: {ts}"])[:2500]
```
Ajouter des parenthèses explicites autour de chaque `"=" * 50` partout ailleurs.

### M-02 — Retries Orange : scheduler jamais fourni, politique inadaptée ✅ / 📖
**Où** : `main.py:54` (`OrangeAPIClient()` sans scheduler), `core/ticket_manager.py:74-80, 319-330, 358-385`, `core/background_task.py:300-350`.
- `self.scheduler` vaut `None` → `AttributeError: 'NoneType' object has no attribute 'add_job'` à **chaque échec** de création (reproduit : réponse HTTP 500 simulée). L'exception sort de `create_trouble_ticket`.
- `datetime.now()` (naïf, fuseau du serveur) est passé à `run_date` d'un scheduler en `Africa/Douala` : si le serveur est en UTC, l'échéance est dans le passé → « misfire » et retry perdu.
- Les erreurs **4xx** sont retentées comme les 5xx (inutile) ; un **401** devrait invalider le jeton en cache et rejouer une fois ; les retries sont **en mémoire** (perdus au redémarrage) et coexistent avec le job `retry_tickets_missing_orange_ids` (toutes les 30 min, `retry_count=0`) : tempêtes de tentatives.
- Aucune **idempotence** : une création réussie côté Orange mais dont la réponse est perdue (timeout) est refaite → **doublons** Orange.
- `retry_ticket_creation` ré-enregistre (`save_tickets`) la copie *périmée* du ticket vieille de 15 min (écrasement, voir M-03).
**Correction** : créer le client **après** le scheduler (`OrangeAPIClient(scheduler=scheduler)`), `datetime.now(constants.CAMEROON_TZ)`, ne retenter que timeouts/5xx/429 avec backoff exponentiel, invalider le jeton sur 401. Cible : supprimer les retries en mémoire et ne garder que le job périodique, piloté par des colonnes `orange_attempts` / `orange_next_retry_at` ; inclure l'ID AUTODO dans la requête (champ externe) et **interroger Orange avant de recréer** si l'API le permet.

### M-03 — Mises à jour concurrentes : écrasement des tickets (lost update) 📖
**Où** : `core/background_task.py:210-350, 1418-1534`, `core/ticket_manager.py:737-783`.
Trois jobs (collecte :01/:06…, `monitor_orange_ticket_status` :00/:17/:32/:47, `retry_tickets_missing_orange_ids` :12/:42) plus deux threads lisent des tickets, les modifient en mémoire **pendant parfois plusieurs minutes** (appels Orange de 30 s chacun, séquentiels) puis **réécrivent la ligne entière** (`INSERT … ON DUPLICATE KEY UPDATE` de toutes les colonnes). `process_monitoring_data` re-sauvegarde en plus **tous les tickets ouverts à chaque cycle** (l. 1493). Un ticket fermé ou mis à jour par un autre job peut être rouvert ou écrasé par une copie ancienne.
**Correction** : écritures ciblées et conditionnelles —
```python
def update_ticket_fields(ticket_id: int, fields: dict, expected_status: str | None = None) -> bool:
    sets = ", ".join(f"`{k}` = %s" for k in fields)          # noms de colonnes en liste blanche !
    sql = f"UPDATE tickets_tb SET {sets} WHERE id = %s" + (" AND status = %s" if expected_status else "")
    with local_db() as conn, conn.cursor() as cur:
        cur.execute(sql, (*fields.values(), ticket_id, *([expected_status] if expected_status else [])))
        return cur.rowcount == 1
```
(+ colonne `version` pour du verrouillage optimiste). Ne plus ré-enregistrer des tickets non modifiés ; une seule couche « repository » pour les accès à `tickets_tb`.

### M-04 — Configuration : périmée au démarrage, non validée, défauts divergents ✅ / 📖
**Où** : `main.py:72-84`, `core/background_task.py:52`, `core/utils.py:35-57`, `routers/dashboard.py:296-406`.
- `config` est lu **une fois** au démarrage et passé en argument au job : `traffic_consecutive_drops_count` modifié dans l'interface est **ignoré** pour le calcul des timestamps (jusqu'au redémarrage) alors que `check_traffic_conditions_advanced` relit `load_config()` à chaque appel : les deux se désynchronisent (la règle 3 devient muette si le compteur augmente).
- Les flags `alerts_enable_*` ne sont **lus par aucun code serveur** (recherche dans tout le dépôt) : ils ne pilotent que l'affichage ; `day_over_day_drop_percent` correspond à une règle commentée : paramètre mort, pourtant modifiable.
- Aucune borne (seuils négatifs, ratios > 1…), `extra="allow"`, défauts de `ConfigParams` ≠ `config.json` (ex. `traffic_up_threshold_percent` 10 vs 4, `day_over_day_drop_percent` 50 vs 0, `alerts_enable_international_gi` False vs True) : un POST partiel (C-04) change silencieusement le comportement.
- `config.json` (versionné) est **réécrit par l'application** : dépôt « sale », écrasé au déploiement, écriture non atomique ni verrouillée.
- `load_config()` relit disque + valide Pydantic à chaque appel (≈ 600 fois par cycle via `_get_trend_icon_and_color`).
**Correction** : relire la configuration au début de chaque cycle (ou cache par `mtime`) et la **transmettre** aux fonctions ; `Field(gt=…, le=…)` sur chaque paramètre ; défauts = `config.json` ; fusion partielle (`exclude_unset=True`) ; écriture atomique ; déplacer l'état modifiable hors du dépôt (`DATA_DIR/config.json` ou table SQL) et versionner un `config.default.json` ; supprimer ou implémenter les paramètres morts (`alerts_enable_*`, `day_over_day_drop_percent`).

### M-05 — Faux positifs : un incident de *supervision* est traité comme un incident *réseau* 📖
**Où** : `core/database_manager.py` (`default_result`), `core/background_task.py:375-380, 1401-1452`.
- Si la base DIAGNET est injoignable, `default_result` fournit un trafic de **0** et `connection_failed=True` : la règle « trafic nul » ouvre un ticket **critique** chez Orange pour une panne de l'outil de mesure (le texte du ticket l'indique, mais le ticket part quand même).
- La règle « trafic nul » se déclenche sur **un seul** échantillon à 0 ; la règle « chute instantanée » sur un seul pas de 5 min : sensibles au bruit de collecte.
- `process_db_down_logic` retourne sans rien faire si `last_update` est `None` — c'est précisément le cas d'une base sans donnée.
- Le même paramètre `db_recovery_threshold_minutes` (70) sert à **déclarer** et à **lever** l'alerte : pas d'hystérésis.
**Correction** : séparer les natures d'incident (`monitoring_unreachable` ≠ `traffic` ≠ `db_down`), n'escalader vers Orange qu'après N cycles consécutifs (ex. 3) ; exiger 2 échantillons consécutifs pour la règle « zéro » ; distinguer seuil de déclenchement et seuil de levée ; traiter `last_update is None` comme « inconnu » avec alerte interne (pas Orange).

### M-06 — Les agrégats TCP du dashboard sont faux ✅
**Où** : `core/background_task.py:977-985, 1063-1077, 1087-1092` (`_calculate_totals_optimized`).
La somme utilise `tcp_no_response["start"]` — déjà un **pourcentage** par base — puis calcule `100 × somme_des_pourcentages / sessions`. Les compteurs bruts existent (`tcp_no_response_start`) mais ne sont pas utilisés ; idem pour `tcp_block_wp_up`.
**Preuve ✅** : deux bases à 10 % (100 non-réponses / 1 000 sessions chacune) → agrégat attendu **10,00 %**, obtenu **1,00 %**.
De plus : la latence moyenne divise par `latence_count` calculé sur la *présence* d'un dict (et non d'une valeur numérique), la convention de signe de `taux` diffère entre ligne (`début − fin`) et total (`fin − début`), et le total retourne des chaînes (`f"{v:.2f}"`) au lieu de nombres.
**Correction** : additionner les compteurs bruts (`…_start/_end` bruts), pondérer la latence par `synack`, unifier le signe, renvoyer des nombres et formater côté client.

### M-07 — Une valeur NULL fait échouer tout le calcul du dashboard ✅
**Où** : `core/background_task.py:531-546` (`float(rec.get(p, 0))`).
`rec.get(p, 0)` renvoie `None` si la clé existe avec une valeur NULL (colonne vide, `SUM` de valeurs NULL) → `TypeError: float() argument must be … not 'NoneType'` ✅. L'exception interrompt le thread de pré-calcul : le cache reste **vide**, ce qui expose directement l'interblocage C-07. Un `try/except` **par base** est absent (une seule base défaillante invalide tout le dashboard). `inaccessible_dbs.append` est en outre exécuté une fois par intervalle (doublons).
**Correction** : `def num(rec, k, d=0.0): v = rec.get(k); return float(v) if v is not None else d`, une garde par base, `set()` pour les inaccessibles.

### M-08 — Collecte séquentielle, délais de 60 s, cycles qui se chevauchent 📖
**Où** : `core/database_manager.py` (`fetch_unified_data_for_processing`, pool `connection_timeout=60`, `pool_size=6`), `core/background_task.py:34-112`.
- La docstring parle de collecte concurrente, le code est **séquentiel** : pour *N* bases, 3 connexions (whole, apn, mode) chacune avec `SHOW TABLES` ; une base injoignable coûte jusqu'à **3 × 60 s**. Quelques bases en panne suffisent à dépasser les 5 minutes.
- `max_instances=1` est **sans effet** : la fonction du job rend la main dès le lancement de deux threads *daemon* ; le cycle suivant peut démarrer pendant que le précédent traite encore, et les threads daemon sont **tués brutalement** à l'arrêt (écriture en cours).
- `pool_size=6` × N bases alors qu'une connexion à la fois est utilisée ; `LIKE '%tdd%'` sans index ; requête du mode sur 7 jours dupliquée (deux fois dans le fichier).
**Correction** : `ThreadPoolExecutor(max_workers=8)` avec délai global par base, `connection_timeout=10`, une seule connexion par base et par cycle (fusionner les 3 passes), `pool_size=2` ; exécuter dashboard puis monitoring **dans** le job (ou joindre les threads) pour que `max_instances=1` joue son rôle ; `coalesce=True`, `misfire_grace_time=120`.

### M-09 — XSS stocké dans la page d'administration (et `innerHTML` non échappé partout) ✅
**Où** : `templates/data_management.html:722-730`, `static/script.js:545-581`, `templates/tickets.html:645-690`.
**Chaîne d'attaque** : `get_real_ip()` fait confiance à `X-Forwarded-For` (M-11) → la valeur est stockée telle quelle dans `daily_users.last_ip` (`VARCHAR(45)`) → la page *Data Management* l'insère par `innerHTML`. Une charge utile de 34 caractères (`<img src=x onerror=…>`) tient dans la colonne.
**Preuve ✅ (navigateur)** : avec Playwright, un visiteur envoie `X-Forwarded-For: <img src=x onerror=window.__xss=1>` à `/api/dashboard_data` ; l'administrateur se connecte à *Data Management* → `window.__xss === 1` (script exécuté dans le contexte de la session administrateur).
Même motif (moindre risque) pour `a.title`, `a.message`, `ticket.reason`, `ticket.description` (issus de messages d'erreur de bases/exceptions). Par ailleurs `a.valeurs.map(v => v.toFixed(3))` plante si une valeur est `null` et casse **toute** la liste d'alertes.
**Correction** :
```js
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
tbody.innerHTML = logs.map(l => `<tr><td>${esc(l.date)}</td><td>${esc(l.ip_address)}</td>…`).join('');
```
Préférer `textContent`/`createElement` pour tout nouveau code ; valider l'IP côté serveur (`ipaddress.ip_address`) avant stockage ; ajouter un en-tête **Content-Security-Policy** (`default-src 'self'; object-src 'none'; frame-ancestors 'none'`) — il faudra pour cela sortir le JavaScript inline des templates (`tickets.html`, `data_management.html`).

### M-10 — Collisions d'identifiants de ticket et **écrasement silencieux** ✅
**Où** : `core/utils.py:162-200` (`get_next_ticket_id`), `core/ticket_manager.py:1063-1120, 737-783`.
- L'ID (`YYYYMMDDNNN`) est calculé par `MAX(id)` sur les tickets dont la colonne **`timestamp`** (date de la *donnée*, pas de création) est aujourd'hui. Or `create_ticket_with_description` remplace le timestamp par celui de la valeur d'origine (jusqu'à 2 créneaux plus tôt), et `process_db_down_logic` utilise la **dernière mise à jour de la base** (parfois vieille de plusieurs jours) : un ticket créé aujourd'hui peut ne pas être vu par la requête → **même ID réattribué**.
- Aucune protection de concurrence (lecture puis écriture sans verrou) ; en cas d'erreur la fonction renvoie `…001`.
- Au-delà de 999 tickets/jour, `f"{prefix}{1000:03d}"` produit un ID à 12 chiffres.
- Pire : `save_tickets` utilise `ON DUPLICATE KEY UPDATE` : un ID réutilisé **écrase silencieusement** un ticket existant.
**Preuve ✅** : ticket `…001` existant avec `timestamp` ancien → l'ID suivant généré est de nouveau `…001` ; après `…999`, l'ID suivant est `202610051000`. Sur la démo avec le code d'origine, **4 tickets de 4 bases différentes reçoivent le même ID `20261006001`** (aucun n'est en base, voir C-01).
**Correction** (livrée) : compteur atomique par jour —
```sql
CREATE TABLE ticket_counters (day DATE PRIMARY KEY, n INT NOT NULL);
INSERT INTO ticket_counters (day, n) VALUES (%s, LAST_INSERT_ID(1))
  ON DUPLICATE KEY UPDATE n = LAST_INSERT_ID(n + 1);
SELECT LAST_INSERT_ID();
```
+ amorçage à partir des IDs existants du jour, `INSERT` **strict** (et non upsert) à la création, suffixe sur 4 chiffres si besoin, date du jour en `Africa/Douala` (jamais `CURDATE()` du serveur).

### M-11 — Entrées client de confiance : IP, identifiant de client, concurrence d'`INSERT` ✅
**Où** : `core/utils.py:149-159` (`get_real_ip`), `348-420` (`track_user`).
- `X-Forwarded-For` est pris tel quel, sans proxy de confiance déclaré : **IP forgeable** (journaux d'audit, statistiques d'accès, et vecteur de M-09).
- `X-Client-Id` est fourni par le client sans validation : on peut créer des lignes à volonté dans `daily_users` (croissance de table) ; `user_agent` non borné.
- `SELECT` puis `INSERT` : deux requêtes simultanées du même client produisent une erreur de clé primaire (non gérée).
- Aucune politique de **rétention** : adresses IP et user-agents conservés indéfiniment (donnée personnelle ; prévoir durée de conservation et information des utilisateurs selon la réglementation applicable).
**Correction** (livrée) : `TRUSTED_PROXIES` (n'utiliser `X-Forwarded-For` que si le pair TCP est un proxy déclaré, en remontant la chaîne depuis la droite) ; `uuid.UUID(raw)` ou génération serveur ; `INSERT … ON DUPLICATE KEY UPDATE` atomique ; `user_agent[:255]` ; job de purge (ex. 90 jours).

### M-12 — Divulgation d'informations et durcissement HTTP ✅ / 📖
- **CORS** : `allow_origins=["*"]` avec `allow_credentials=True` (`main.py:213-220`). Starlette répond à une pré-requête depuis `https://evil.example` par `Access-Control-Allow-Origin: https://evil.example` **et** `Allow-Credentials: true` ✅ : n'importe quel site peut appeler l'API avec les cookies de l'utilisateur. Application servie en même origine : **supprimer** le middleware, ou lister des origines explicites.
- `/tickets/status` et `/tickets/test/orange-api` (sans authentification) exposent l'hôte et le nom de la base, des détails d'exception (`str(e)` renvoyé au client), le `client_id` OAuth et les endpoints Orange, et **déclenchent des appels sortants** (consommation du quota de jetons). `/api/config` est public.
- `/api/dismiss_alerte` est un **simulacre** : il répond « supprimée » sans rien faire (et réinjecte l'entrée dans le message) — l'état est en réalité côté navigateur (`localStorage`).
- `/api/dashboard_data` renvoie dans chaque `link` les **noms d'hôtes internes** des bases (`db_host`), alors que `db_config.json` est justement gitignoré pour les protéger.
- Les erreurs sont renvoyées en **HTTP 200** avec `{"error": str(e)}` (`/api/dashboard_data`, `/api/data-management`) : contrat d'API trompeur et fuite d'informations.
- Cookies sans `Secure` ; app liée à `0.0.0.0:501` (port privilégié, nécessite root) sans TLS ; aucun en-tête de sécurité.
**Correction** (livrée pour l'essentiel) : authentifier les routes d'administration/diagnostic, messages d'erreur génériques + `logger.exception`, vrais codes HTTP (4xx/5xx), supprimer ou faire réellement fonctionner `dismiss_alerte`, ne pas exposer les hôtes internes, port ≥ 1024 derrière un reverse proxy TLS.

### M-13 — Synchronisation Orange → AUTODO : fermeture prématurée et horodatage incohérent 📖
**Où** : `core/background_task.py:210-297`.
- Le ticket local est **fermé dès que le statut Orange n'est plus `InProgress`**, y compris si le tableau de statuts est absent/modifié (réponse vide, nouveau libellé) → fermetures à tort.
- `closure_timestamp = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')` : heure du **serveur** (pas Douala) et format avec `T`, différents des autres colonnes ; pas de `closure_reason`.
- Les appels Orange sont séquentiels (30 s × N) pendant que d'autres jobs modifient les mêmes tickets (M-03). Les erreurs sont loguées en **DEBUG** (invisibles en console).
- La fermeture côté AUTODO appelle l'API Orange, mais un échec laisse le ticket fermé localement et **ouvert chez Orange** sans rejeu.
**Correction** : ne fermer que sur un statut explicitement terminal (liste blanche : `Restored`, `Closed`…), horodatage en `CAMEROON_TZ`, `closure_reason`, file de rejeu pour les fermetures Orange échouées.

### M-14 — Gestion du temps : décalage UTC+1 codé en dur, datetimes naïfs 📖
**Où** : `core/background_task.py:1464-1469, 1485` (`offset_by("1h")`, `+ timedelta(hours=1)`), `core/ticket_manager.py` (`datetime.now()`), `core/utils.py` (colonnes `DATETIME` locales vs `TIMESTAMP`).
Les bases sont en UTC et l'application les convertit **à la main** (+1 h) : correct pour `Africa/Douala` (pas d'heure d'été) mais fragile, dupliqué, et incohérent avec les `datetime.now()` naïfs (fuseau du serveur). `ticket.timestamp` est parfois une heure locale, parfois une heure de donnée UTC. Mélange de `DATETIME` (heure locale) et `TIMESTAMP` (converti selon le fuseau de session) dans le schéma.
**Correction** : tout stocker en **UTC** (`datetime.now(timezone.utc)`), convertir **uniquement à l'affichage** avec `ZoneInfo("Africa/Douala")`, un seul module `timeutils`, `SET time_zone = '+00:00'` sur chaque connexion.

---

## 5. Constats moyens 🟡

### Mo-01 — Journalisation : 8 handlers sur le même fichier, logs perdus, erreurs en DEBUG ✅
`core/logger.py` attache un `RotatingFileHandler` **par logger nommé** : 8 handlers écrivent dans `app_unified.log` (vérifié ✅). Plusieurs `RotatingFileHandler` sur un même fichier se désynchronisent à la rotation (fichiers tronqués/perdus, erreurs sous Windows). `core/email_config.py` utilise `logging.getLogger(__name__)` sans handler → seuls les WARNING+ sortent sur stderr, **les logs d'envoi d'e-mails n'arrivent jamais dans le fichier** ✅. De nombreuses erreurs sont tracées en `logger.debug(...)` (monitoring Orange, etc.) et pas en `error`/`exception`. Les logs d'Uvicorn ne sont pas capturés.
**Correction** (livrée) : configurer **une seule fois le logger racine** (fichier + console), `get_logger = logging.getLogger`, niveau via `LOG_LEVEL`, `logger.exception()` dans les `except`, jamais de secret dans un message.

### Mo-02 — Aucune auto-supervision : un outil de monitoring qui ne se surveille pas 📖
`/health` renvoie toujours « healthy » (ni base, ni scheduler). Aucun *heartbeat* : si la collecte s'arrête, personne n'est prévenu. **Correction** : `/health` vérifie la base locale + l'état du scheduler (503 sinon, livré) ; enregistrer l'heure du dernier cycle réussi et alerter (e-mail/Orange « AUTODO silencieux ») au-delà de 15 min ; exposer des métriques (durée du cycle, nb de bases en échec, nb de tickets ouverts).

### Mo-03 — E-mails : blocages, fuite de connexion, mise en forme 📖
`core/email_config.py` : `smtplib.SMTP(host, port)` **sans timeout** (peut bloquer indéfiniment le thread de monitoring), connexion non fermée en cas d'exception (pas de `with`), `starttls()` sans contexte SSL (pas de vérification de certificat), `time.sleep(15)` entre les tentatives **dans le thread de traitement**, aucun `html.escape` des champs injectés dans le HTML, clés lues `'Agent'`/`'Severity'` alors que les tickets utilisent `agent`/`severity` (toujours les valeurs par défaut), CSS `display:flex` non pris en charge par Outlook (rendu Word) → préférer des tables, `return_detailed_results` rend tantôt un `bool` tantôt un `dict`. Le fichier d'état `core/email_notifications.json` est écrit dans le **répertoire du code** (non atomique, non partagé entre threads). `email_recipients.txt` est **versionné avec une adresse nominative** (donnée personnelle) : le déplacer en variable d'environnement/fichier ignoré et fournir un `.example`.
**Correction** : `with smtplib.SMTP(host, port, timeout=15) as s:`, `ssl.create_default_context()`, envoi via une file/un thread dédié, `html.escape`, état dans la base ou dans `DATA_DIR`.

### Mo-04 — Schéma SQL, requêtes, rétention 📖
- Pas de migrations (`CREATE TABLE IF NOT EXISTS` + vérification `SHOW TABLES LIKE 'tickets_tb'` ; `_` est un joker de `LIKE`) → utiliser Alembic ou un `schema_version`.
- `BIGINT(20)` (largeur d'affichage dépréciée), pas d'index unique sur `external_api_ticket_id`, pas d'index `(status, timestamp)` pour les requêtes du dashboard ; `SELECT *` non borné (`fetch_alerts_from_tickets_tb`, `load_tickets(open_only=False)` charge **toute la table** pour la page Tickets, filtrée côté navigateur) → pagination.
- Filtres non sargables : `DATE(timestamp) = CURDATE()`, `YEARWEEK(...)` (utilisent l'heure du serveur MySQL, pas Douala) → intervalles `timestamp >= %s AND timestamp < %s` calculés côté Python.
- `ON DUPLICATE KEY UPDATE col = VALUES(col)` est déprécié en MySQL 8.0.20+ (alias de ligne).
- `daily_users` et `logs` croissent sans purge (voir M-11).
- Le pool local de 6 connexions peut être épuisé par les endpoints `def` (jusqu'à 40 threads) → `PoolError`.

### Mo-05 — Boucle SSE : tourne 1 fois par seconde et meurt à la première exception ✅
`sse_tick_task` (`main.py:103-142`) : le `try` englobe la boucle, donc **la première exception arrête définitivement** les mises à jour planifiées (le `sleep(5)` du `except` n'est suivi d'aucune reprise). Le calcul du prochain tick est faux après chaque publication : sur 10 minutes simulées, **124 réveils** (≈ 1/s) pour 2 publications ✅. Côté `/events` : abonnés non bornés, pas d'authentification ni de limite par IP, `request.is_disconnected()` n'est évalué que toutes les 25 s, en-têtes `Cache-Control: no-cache` et `X-Accel-Buffering: no` absents (les reverse proxys bufferisent le flux), `datetime.now()` naïf.
**Correction** (livrée) : `next_tick = base + timedelta(minutes=((2 - base.minute) % 5) or 5)`, `try/except` **à l'intérieur** du `while`, ou simplement un job APScheduler `cron minute='2-59/5'`.

### Mo-06 — Architecture et couplage 📖
Imports circulaires (`from main import templates`, `from main import app` dans les routes ; `python main.py` charge `main` deux fois) → `request.app.state` ; `core/__init__.py` importe `DataCollector`/`TicketManager` (import lourd, cycles) ; `from .utils import ConfigParams` à l'intérieur de `utils.py` ; globals mutables (`constants.TOKENS`, `DB_CONFIGS`, `LABELS`, `_unified_data`) rechargés pendant que d'autres threads les lisent ; fichiers de 1 500–1 600 lignes (`ticket_manager.py`, `background_task.py`) mêlant client HTTP, persistance, règles métier et notifications. **Cible** : `clients/orange.py`, `repositories/tickets.py`, `domain/rules.py` (fonctions pures testables), `services/monitoring.py`, `notifications/email.py`.

### Mo-07 — Packaging, déploiement, exploitation 📖
Dépendances en `>=` sans borne ni verrou (cause directe de C-06) ; `pydantic-settings` et `python-multipart` **non utilisés** ; Jinja2 requis implicitement ; aucun `README`, `LICENSE`, `Dockerfile`/unit systemd, `pyproject.toml`, CI. Le **mono-worker** obligatoire n'est écrit nulle part (lancer `uvicorn --workers 2` dupliquerait jobs, tickets et sessions). Port 501 privilégié. Pas de procédure de sauvegarde de `tickets_tb`.
**Livré** : `requirements.txt` borné + `requirements.lock.txt`. **À faire** : README (prérequis, variables, démarrage, mono-worker), `Dockerfile` non-root, unité systemd, `pip-audit` en CI.

### Mo-08 — Frontend 📖 / ✅
- `tickets.html:715,721` lit `t.ttr?.seconds` alors que l'API fournit `ttr_seconds` : **le résumé des TTR ne s'affiche jamais**.
- `script.js` (≈ 2 000 lignes) et gros scripts inline dans les templates : pas de modules, pas de CSP possible, état global `window.*`.
- La pop-up modale « Nouvelle Alerte » recouvre toute la page et doit être acquittée avant d'atteindre le bouton ⚙️ (constaté par Playwright : le clic sur « Configuration » est intercepté tant que la pop-up est affichée) : prévoir un acquittement automatique, un toast non bloquant ou un bouton de fermeture accessible au clavier (`Esc`).
- Accessibilité : boutons-icônes sans libellé ARIA, contrastes à vérifier, ordre de tabulation de la pop-up.
- `chart.umd.min.js` vendorisé sans indication de version : documenter/mettre à jour.

### Mo-09 — Code mort et duplication 📖
`load_all_databases_data_sync` (≈ 150 lignes), `fetch_all_data_concurrent`, `_map_field`, `get_current_user`, le simulacre `dismiss_alerte`, la branche `prev_seq` de `_get_trend_icon_and_color` (jamais alimentée), `traffic_yesterday`/`day_over_day_drop_percent` (règle 4 commentée), `core/email_notification_example.py` (script de démonstration dans le paquet), commentaires d'étapes jamais implémentées (« envoyer l'e-mail de fermeture »). `_process_data_for_interval` (~430 lignes) est un copier-coller par catégorie : table de configuration + une fonction.

### Mo-10 — Valeurs métier en dur 📖
Identifiants Orange (CUID, ressource `PRD…`, `ResourceId1`), causes de panne, IP DNS de référence (`8.8.8.0/24`), facteur `37_500_000.0` dupliqué alors que `DEBIT_FACTOR` existe, seuils de 2,5 Gb/s, bornes de catégories. **Correction** : fichier de configuration (`business.toml`) validé au démarrage.

### Mo-11 — Aucun test automatisé ni CI 📖
Aucun test, aucune CI, aucun linter configuré : ni `ruff`, ni `mypy`. `ruff` signale une quinzaine de f-strings sans variable, des `except:` nus, `B904` (`raise … from`), `zip()` sans `strict=` ; `bandit` signale `verify=False`, `0.0.0.0`, SQL construit par f-string (**faux positifs vérifiés** : seules des listes de `%s` ou des clauses constantes sont concaténées). Voir §7.

---

## 6. Constats mineurs et dette technique 🔵

- `Dict[str, any]` (`constants.py`) : `any` est la fonction native, pas `typing.Any`.
- `if not utils.verify_database_setup(): logger.critical("… Exiting.")` **ne quitte pas** (`main.py:42-43`) : l'application démarre quand même sans base.
- `except:` nus (`tickets.py`), `except (ConnectTimeout, ConnectionError, Exception)` redondants, second `except Exception` inatteignable (`create_ticket`, l. 883).
- `has_open_ticket` : `if not tickets: tickets = load_tickets()` traite une liste **vide** comme « non fournie » → requête SQL supplémentaire à chaque appel ; mélange `bool`/`1` dans l'expression de filtre.
- `has_open_ticket`/`load_tickets` appelés pour chaque base (N+1) : charger une fois par cycle.
- Valeurs par défaut incohérentes pour un même paramètre (`alert_pertes_threshold` : 3.0 / 2.0 / 1.0 selon le fichier).
- Docstrings qui référencent des scripts inexistants (`test_external_api.py`, `update_tickets.py`) ; commentaires obsolètes (« synchronisés avec l'API », « concurrent »).
- Mélange français/anglais et emojis dans les messages de log (gênant pour la recherche/alerting).
- `TemplateResponse` ancienne signature, `ORJSONResponse` déprécié (FastAPI 0.142), `ruff` : f-strings sans placeholders.
- `Dict`/`List` de `typing` à moderniser (`dict[...]`, `X | None`), docstrings de type `"""` multi-usage.
- `.gitignore` référence `REVUE_CODE.md` : fichier absent du dépôt cloné.
- Typo `CALYSPSO` / `CALYPSO` dans les libellés.
- `email_notification_example.py` dans `core/` ; `config.json` modifié par l'application (cf. M-04).
- `asyncio.create_task(...)` sans conserver la référence (peut être collecté avant la fin) → ensemble `app.state.bg_tasks` (livré).

**Refactor recommandé de `process_ticket_logic` (16 cas).** Ces 16 branches se ramènent à : (1) décider si une alerte *trafic* et/ou *pertes* est active ; (2) rattacher les pertes au ticket trafic quand les deux coexistent (pas de ticket « pertes » séparé) ; (3) pour chaque type, créer / mettre à jour / tenter de fermer. Écrire d'abord un **test paramétré sur les 16 combinaisons** (état initial × alertes) avec des doubles de `create_ticket` / `update_ticket_status`, puis refactorer derrière ce filet.

---

## 7. Tests, qualité et CI/CD

### 7.1 État actuel
Aucun test, aucune intégration continue. Conséquence : les défauts critiques C-01 à C-03 (régressions de renommage et de transaction) n'ont pas été détectés.

### 7.2 Ce qui est livré (dossier `demo/tests`)
| Suite | Contenu | Outils |
|---|---|---|
| `tests/unit` (11) | non-régression sans base : signature `update_ticket_status`, `add_pertes…`, description Orange, TLS/secrets dans les logs, colonnes de pertes, agrégats TCP, NULL dans le dashboard, verrou ré-entrant, `X-Forwarded-For`, bornes de configuration, mots de passe fail-closed | `pytest` |
| `tests/e2e` (19) | pages, dashboard avec données simulées, SSE, section alertes, page tickets, persistance et unicité des IDs, type de ticket attendu par scénario, charge utile Orange, e-mail, stabilité au 2ᵉ cycle, authentification, CORS, flux UI de configuration, **XSS stocké** | `pytest` + **Playwright (Chromium)** |

### 7.3 CI recommandée (GitHub Actions)

```yaml
name: ci
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-24.04
    services:
      mariadb:
        image: mariadb:10.11
        env: { MARIADB_ALLOW_EMPTY_ROOT_PASSWORD: "1" }
        ports: ["3306:3306"]
        options: >-
          --health-cmd="healthcheck.sh --connect --innodb_initialized" --health-interval=5s --health-retries=20
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12", cache: pip }
      - run: pip install -r requirements.lock.txt pytest playwright requests aiosmtpd ruff bandit pip-audit
      - run: playwright install --with-deps chromium
      - run: ruff check . && bandit -q -r . -x tests,static,templates -ll && pip-audit -r requirements.lock.txt
      - run: AUTODO_REPO=$PWD pytest demo/tests/unit -q
      # pile simulée : MariaDB (service) + faux Orange + puits SMTP, puis E2E
      - run: |
          export DEMO_DB_HOST=127.0.0.1 DEMO_DB_PORT=3306
          python -m uvicorn mock_orange_api:app --app-dir demo --port 9001 & python demo/mail_sink.py &
          AUTODO_REPO=$PWD pytest demo/tests/e2e -q -k "not second_cycle"
```
Ajouter `mypy` (progressivement), `pre-commit` (ruff, détection de secrets), un seuil de couverture sur `core/` (≥ 70 % visé pour les règles de détection et la machine à états des tickets) et un test de charge léger sur `/api/dashboard_data`.

---

## 8. Validation : source de données simulée, Playwright et résultats

### 8.1 Source de données simulée (`demo/`)

- **19 bases DIAGNET fictives** (`SIM_<clé>` : 7 internationales, 3 Gi, 9 roaming) avec les tables réellement lues par l'application (`whole_5min`, `apn_5min`, `dns_5min`) : 8 jours d'historique par pas de 5 min, courbe diurne (pic ≈ 20 h locale), effet week-end, bruit déterministe, métriques TCP/latence/DNS.
- **Flux « live »** (`seed_demo_data.py follow`) : ajoute le créneau de 5 min courant ; **anomalies injectables à chaud** (`zero`, `instant_drop`, `gradual`, `loss`, `stale`) pour dérouler une démonstration.
- **Faux API Orange** (OAuth2 + `troubleTicket`, inspectable) et **puits SMTP** : l'application est exercée de bout en bout sans aucun appel externe.

Scénario utilisé pour la revue : `douala_camtel` = trafic nul · `kribi` = chute instantanée · `oci_sat3` = baisses consécutives · `wacs_ic` = pertes à 8 % · `garoua_camtel` = base figée depuis 90 min. **5 incidents attendus → 5 tickets**.

### 8.2 Résultats (même suite, avant / après correctifs)

| Test | Code d'origine `87b4160` | Avec le patch |
|---|:--:|:--:|
| Pages `/`, `/tickets/`, `/data-management` renvoient 200 (×3) | ❌ 500 | ✅ |
| Dashboard affiche les données simulées | ❌ | ✅ |
| Événement SSE `hello` | ✅ | ✅ |
| Section « Alertes » alimentée depuis la base | ❌ | ✅ |
| Page Tickets liste les cartes | ❌ | ✅ |
| Tickets persistés avec IDs uniques | ❌ **0 ligne** | ✅ 5 |
| 1 ticket par scénario avec le bon type/catégorie | ❌ aucun | ✅ |
| Tickets Orange = tickets locaux, description propre | ❌ 4 au lieu de 5 | ✅ |
| E-mail de notification reçu | ✅ | ✅ |
| 2ᵉ cycle stable, sans doublon ni `Traceback` | ❌ `TypeError` | ✅ |
| `/update_config` exige une authentification | ❌ 200 | ✅ 401 |
| Identifiants ExNet jamais renvoyés | ❌ en clair | ✅ |
| Jeton `cfg` refusé sur data-management | ❌ 200 | ✅ 401 |
| Mots de passe faux / absents refusés | ✅ | ✅ |
| CORS ne reflète pas une origine arbitraire avec cookies | ❌ | ✅ |
| Parcours UI de configuration (mot de passe → tiroir → sauvegarde) | ❌ (page 500) | ✅ |
| **XSS stocké** dans le tableau des accès | ❌ (`window.__xss === 1` ✅ prouvé en isolant le seul correctif de rendu) | ✅ |
| **Total E2E** | **3 réussis / 16 échecs** | **19 / 19** |
| **Total unitaires** | **0 / 11** | **11 / 11** |

> Preuve du XSS dans le code d'origine : comme les pages renvoyaient 500, le test a été rejoué sur une copie du code d'origine où **seul** le correctif `TemplateResponse` était appliqué : l'XSS s'exécute (`assert 1 is None` échoue).

**Observations de la démo sur le code d'origine** (utiles pour la présentation) : les 4 tickets créés dans le même cycle portent **le même ID** `20261006001` (M-10) ; `tickets_tb` reste vide (C-01) ; le ticket « pertes » de `wacs_ic` n'est jamais créé alors que le dashboard affiche 8 % (C-02) ; un seul ticket est sauvegardé (les 4 tickets en mémoire se remplacent mutuellement car ils partagent le même ID), et c'est cette liste qui alimente l'e-mail.

### 8.3 Matrice de couverture du premier patch (historique — remplacé par la refonte, voir « Mise à jour du 8 octobre 2026 »)

| Réf. | Correction | État dans le patch |
|---|---|---|
| C-01 | `local_db()` (commit/rollback), `save_tickets` | ✅ appliquée et testée |
| C-02 | `build_monitoring_df`, garde « donnée manquante » | ✅ |
| C-03 | `values_to_update`, `@staticmethod`, isolation par base | ✅ |
| C-04 | `core/auth.py` (portées, fail-closed, anti brute-force), `update_config` protégé, fusion partielle, écriture atomique | ✅ |
| C-05 | endpoint désactivé par défaut, session requise, liste blanche d'hôtes | ✅ (atténuation) — **rotation des identifiants et SSO restent à faire** |
| C-06 | nouvelle signature `TemplateResponse`, `app.state.templates`, dépendances bornées + lock | ✅ |
| C-07 | `RLock`, 503 si cache vide, `asyncio.to_thread` | ✅ |
| C-08 | TLS vérifié (`ORANGE_API_CA_BUNDLE`), plus de secret dans les logs, plus de CUID par défaut | ✅ |
| M-01 | description Orange | ✅ |
| M-02 | scheduler fourni au client, horloge Douala | 🟡 partiel (politique de retry/idempotence à faire) |
| M-03 | mises à jour ciblées | ❌ non traité |
| M-04 | config relue à chaque cycle, bornes, fusion partielle, écriture atomique | 🟡 partiel (flags `alerts_enable_*` et `config.json` versionné restent) |
| M-05 | faux positifs de supervision | 🟡 garde « donnée manquante » seulement |
| M-06 | agrégats TCP bruts | ✅ |
| M-07 | NULL-safe, isolation par base | ✅ |
| M-08 | collecte concurrente | 🟡 `connect_timeout` 10 s + `DB_POOL_SIZE` ; parallélisation **non faite** |
| M-09 | échappement HTML (3 fichiers) | ✅ — CSP non faite |
| M-10 | ID atomique | ✅ |
| M-11 | proxys de confiance, UUID, upsert atomique | ✅ — purge de rétention non faite |
| M-12 | routes protégées, erreurs génériques, CORS, `dismiss` | ✅ (hôtes internes dans les liens : non traité) |
| M-13 / M-14 | horodatage de fermeture, raison | 🟡 partiel ; UTC+1 codé en dur : non traité |
| Mo-01/02/05 | logging racine, `/health` réel, boucle SSE | ✅ |
| Mo-03 | timeout SMTP, clés `agent`/`severity` | 🟡 partiel |
| Mo-06/07 | imports circulaires, dépendances | ✅ partiel |
| Autres moyens/mineurs | — | ❌ à planifier (§9) |

> ⚠️ Le patch est un **point de départ validé**, pas une livraison finale : relecture, essais sur un environnement de recette avec les vraies bases, et recette fonctionnelle (notamment la désactivation par défaut de l'auto-login ExNet) sont nécessaires.

---

## 9. Plan d'action priorisé

**Phase 0 — aujourd'hui (urgence sécurité)**
1. Faire pivoter les identifiants ExNet et, par précaution, le secret OAuth Orange et les mots de passe `CONFIG_PASSWORD` / `DATA_MANAGEMENT_PASSWORD` ; purger/protéger les logs existants ; vérifier qui a pu joindre l'application.
2. Appliquer C-04/C-05 (authentification, désactivation de l'auto-login) ; restreindre l'accès réseau (reverse proxy, liste d'IP).

**Phase 1 — cette semaine (rétablir le pipeline)**
3. Appliquer le patch (C-01, C-02, C-03, C-06, C-07, C-08, M-01, M-10), figer les dépendances.
4. Rejouer la suite `demo/tests` ; recette sur environnement de test avec de vraies bases en lecture seule.
5. Mettre en place CI (ruff, bandit, pip-audit, tests unitaires + E2E).

**Phase 2 — 2 à 3 semaines (robustesse)**
6. M-03 (mises à jour ciblées / verrouillage optimiste), M-02 (retries/idempotence Orange), M-05 (séparer supervision et réseau), M-08 (collecte concurrente), M-13/M-14 (temps et synchronisation Orange).
7. Auto-supervision (Mo-02), rétention des données (M-11), CSP et front (M-09, Mo-08).

**Phase 3 — 1 à 2 mois (maintenabilité)**
8. Découper `ticket_manager.py` / `background_task.py`, tester la matrice des 16 cas, migrations de schéma, README + Dockerfile + unité systemd, SSO.

---

## Annexe A — Lancer la démonstration

Voir `demo/README.md`. En résumé :

```bash
cd demo
python seed_demo_data.py scenario zero douala_camtel          # anomalies initiales (optionnel)
python seed_demo_data.py seed --reset                         # crée les 19 bases simulées + db_config.json
cp db_config.json ../db_config.json
python seed_demo_data.py follow &                             # flux live
python -m uvicorn mock_orange_api:app --port 9001 & python mail_sink.py &
set -a; . ./env.demo; set +a; (cd .. && python main.py)      # http://127.0.0.1:8501
python seed_demo_data.py scenario instant_drop kribi          # déclencher une anomalie à chaud
```

## Annexe B — Exécuter les tests

```bash
pip install playwright pytest requests aiosmtpd && playwright install chromium
AUTODO_REPO=/chemin/vers/repo pytest demo/tests/unit -v
AUTODO_REPO=/chemin/vers/repo pytest demo/tests/e2e  -v      # ≈ 5 min (attend le 2ᵉ cycle du scheduler)
```

## Annexe C — Récupérer la refonte

```bash
git clone autodo-network-monitoring.bundle autodo && cd autodo     # branche revue/refonte-sqlalchemy
# ou, dans votre dépôt : git fetch ../autodo-network-monitoring.bundle revue/refonte-sqlalchemy:revue/refonte-sqlalchemy
# ou rejouer les commits : git am patches/*.patch   (sur 87b4160)
pip install -r requirements.lock.txt
```

*Fin du rapport.*
