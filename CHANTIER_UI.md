# Chantier « Refonte UI » — suivi

Document **vivant** : chaque lot terminé met à jour les cases ☑, le tableau des critères (§6) et le journal (§10).
Branche : `feat/ui-refonte` (créée depuis `feat/roles-d-ux`). **Règle de travail : un bundle est enregistré dans `outputs` après chaque lot.**

## 1. Objet

L'interface fonctionne mais n'est pas cohérente. Cause racine : **trois systèmes de style sans variable commune** (`styles.css` pour le
tableau de bord, `<style>` intégré à `tickets.html`, `app-ui.css` pour les nouvelles pages), soit 57 couleurs hexadécimales littérales,
deux oranges différents, trois familles de boutons, 13 tailles de police sur le tableau de bord, et un thème clair obtenu en **inversant le
sens des variables** (`--white` devient gris foncé) — d'où le bleu pur et les contrastes cassés.

**Dans le périmètre** : style, structure et lisibilité des 4 pages (tableau de bord, tickets, supervision, admin), thèmes sombre et clair,
mobile/tablette, accessibilité visuelle.
**Hors périmètre** : règles de détection, API (hors champs d'affichage), modèle de données, traductions (sauf nouveaux libellés), nouvelles fonctions métier.
**Contrat à ne pas casser** : identifiants et classes utilisés par le JS et les tests E2E (`#configLink`, `#prefsBtn`, `tr[data-db]`,
`.section-title`, `.ticket-card`, `#alert-popup`…), CSP stricte (aucun style ou script inline nouveau), 289 tests unitaires + 72 E2E verts au départ.

## 2. État de départ (mesuré le 26/10/2026 sur la pile simulée, Chromium)

Outil : `demo/ui_audit.py` (§8) ; valeurs relevées par l'outil lui-même. Contrastes mesurés sur les **pixels réels** (WCAG AA : 4,5:1 texte, 3:1 grand texte).

| Mesure | Départ |
|---|---|
| Tailles de police distinctes (tableau de bord) | 13 |
| Styles de boutons distincts (tableau de bord / tickets) | 8 / 6 |
| Commandes visibles dans l'en-tête (1440 px) · hauteur d'en-tête | 10 (hauteurs 29 à 40 px) · 64 px ; mobile (390 px) : 210 px |
| Hauteur d'une ligne de données · lignes visibles sur 900 px | 73 px (roaming 89) · 7 |
| Hauteur de la page · part des lignes de données dans les sections | 4 103 px · 59 % |
| Textes sous AA : sombre / clair / tickets (sombre, clair) / supervision / admin | 21/157 · 55/151 · 8/140, 8/140 · 0/42 · 0/61 |
| Pire contraste (clair) | deltas verts 1,76:1 (×48), « Last update » 2,41:1 |
| Cibles < 44 px au tactile (390 px) · < 32 px au bureau (tableau de bord / tickets) | 11 · 3 / 28 |
| Alertes ouvertes visibles **dans la ligne** de la base concernée | non (panneau replié en bas de section) |
| Bannière rouge défilante infinie (20 s) | oui |
| Tablette 768 px : tableaux coupés sans repère | oui (4 tableaux) |
| Feuilles de style partageant des variables · couleurs hex littérales | 0 sur 3 · 57 |

## 3. Principes

1. **Un seul vocabulaire** : mêmes jetons (couleurs, tailles, espacements), mêmes composants sur les 4 pages.
2. **L'état d'abord** : une personne doit savoir en 2 secondes si le réseau va bien, et quelles bases sont touchées — sans ouvrir un panneau.
3. **Données avant décor** : moins de cadres, de barres pleines et de bordures ; lignes denses, chiffres alignés (`tabular-nums`).
4. **Une couleur = un sens** : l'orange est la marque et l'action principale ; rouge = alerte ; vert = rétabli ; jamais décoratifs.
5. **Rien ne bouge sans demande** : pas de défilement automatique ; respect de `prefers-reduced-motion`.
6. **Lisible partout** : AA dans les deux thèmes, cibles ≥ 44 px au tactile, aucune information masquée sans indice visuel.

## 4. Spécification des fondations (lots U1 et U2)

### 4.1 Jetons (`static/css/tokens.css`, chargé en premier sur toutes les pages)

| Rôle | Sombre | Clair |
|---|---|---|
| `--bg` fond de page | `#161616` | `#f4f5f7` |
| `--surface` panneaux | `#202020` | `#ffffff` |
| `--surface-2` survol, en-têtes de tableau | `#2a2a2a` | `#eceff2` |
| `--border` | `#3a3a3a` | `#cfd5db` |
| `--text` | `#f3f3f3` | `#1b1f23` |
| `--text-muted` | `#b0b0b0` | `#4a5560` |
| `--accent` (marque, texte/liens) | `#ff6600` | `#b53d0a` |
| `--accent-fill` (fond de bouton principal) · `--on-accent` | `#ff6600` · `#161616` | `#b53d0a` · `#ffffff` |
| `--ok` · `--bad` · `--warn` | `#3ddc84` · `#ff6b6b` · `#ffb020` | `#0a7437` · `#c0121f` · `#8a5a00` |

Contrastes visés (texte normal) : `--text`, `--text-muted`, `--accent`, `--ok`, `--bad`, `--warn` sur `--bg`, `--surface` et `--surface-2` ≥ 4,5:1 dans les deux thèmes
(**vérifié par un test** : il a fait corriger trois couleurs limites, `#c2410c` 4,49:1, `#0a7d3e` 4,5:1 et un bleu daltonien 4,49:1).
Le texte blanc sur orange vif (2,94:1) est **abandonné** : `--on-accent` sombre en thème sombre, blanc sur orange brûlé en thème clair.

**Échelle typographique** (famille système inchangée) : `--fs-xs` 12 px · `--fs-sm` 14 · `--fs-md` 16 · `--fs-lg` 20 · `--fs-xl` 28 (mur d'écran).
Graisses 400 / 600 / 700. Les valeurs de tableau passent de 21 px gras à `--fs-md` semi-gras en `tabular-nums` (fin de Courier).
La règle historique `html { font-size: 90 % }` est **supprimée** (elle ramenait 12 px à 10,8 px).
**Espacement** : 4 · 8 · 12 · 16 · 24 · 32 px. **Rayons** : 6 px (contrôles) · 10 px (panneaux). **Ombre** : une seule, sur les panneaux flottants.
**Hauteurs interactives** : 36 px (bureau) · 44 px (tactile, `@media (pointer: coarse)`).

### 4.2 Composants

| Composant | Règle |
|---|---|
| Bouton | classes existantes conservées : `.btn-primary` (accent), `.btn-secondary` (surface-2), `.btn-danger` (bad), `.btn-ghost` (nouveau, sans fond), modificateur `.btn-icon` (carré, `aria-label` obligatoire). Une seule définition, utilisée partout. `.btn-orange` et `.btn-purple` (tickets) disparaissent. |
| Pastille d'état | `.chip` + `.chip-ok / -bad / -warn / -muted` : toujours **icône + texte** (jamais la couleur seule). |
| Lien | couleur `--accent`, souligné au survol/focus ; jamais le bleu navigateur. |
| Panneau | `--surface`, bordure 1 px `--border`, rayon 10 px ; **pas** de bordure orange de 2 px. |
| Tableau | en-tête `--surface-2` en `--text-muted` 12 px majuscules ; lignes 44 px ; survol `--surface-2` ; aucune couleur d'accent sur le texte courant. |
| Focus | anneau 3 px visible, déjà en place (`:focus-visible`), conservé. |

## 5. Spécification par écran

**Tableau de bord**
- *Barre d'application* (≤ 56 px) : marque « AUTODO » et dernière mise à jour à gauche ; au centre la **bande d'état** (`✔ Réseau nominal` ou `● 5 alerte(s) · 4 base(s)`, cliquable → première base en alerte) ; à droite au plus 6 commandes : présence, *Mise à jour auto* (état explicite), *Filtres*, *Tickets* (principal), menu **⋯** (thème, préférences, légende, supervision), menu **compte**.
- *Sections* : plus de barre orange pleine ni de bordure orange ; titre sur `--surface-2` avec filet d'accent à gauche. Cadres imbriqués aplatis.
- *Lignes* : 44 px ; pastille d'état dans la première colonne et teinte de ligne (`data-alert` = types d'alerte) pour toute base en alerte ouverte ; delta sous la valeur, en `--fs-xs` ; flèche + signe (pas la couleur seule).
- *Panneau d'alertes* : masqué quand la section n'a aucune alerte à montrer.
- *Avis GI* : statique, fermable (mémorisé), ton « avertissement ». Plus d'animation.
- *Tablette* : première colonne figée et indice « faire défiler ». *Mobile* : barre ≤ 64 px (marque + état + menu), cartes avec pastille d'état, cibles 44 px.

**Tickets** : CSS extrait vers `static/css/tickets.css` sur les jetons ; barre d'application identique ; carte hiérarchisée (motif en tête, faits en grille, séries de valeurs repliables) ; actions en boutons compacts.
**Supervision / Admin** : barre d'application avec navigation (`aria-current`) ; classe `kit-table` seule (fin de la réutilisation de `detail-table`) ; sélecteurs à largeur raisonnable ; dates au format unique `AAAA-MM-JJ HH:MM:SS`.

## 6. Critères d'acceptation (mesurés par `demo/ui_audit.py`)

| # | Critère | Départ | Cible | Actuel |
|---|---|---|---|---|
| C1 | Tailles de police distinctes (tableau de bord) | 13 | ≤ 7 | **4** ✔ |
| C2 | Styles de boutons distincts | 8 | ≤ 4 | 9 signatures ; **0 bouton hors système** (mesure redéfinie, voir D4) ✔ |
| C3 | Commandes visibles en en-tête (1440 px) · hauteur · hauteur mobile | 10 · 64 px · 210 px | ≤ 6 · ≤ 56 px · ≤ 64 px | **6** · **56 px** · **61 px** ✔ |
| C4 | Hauteur de ligne · lignes visibles sur 900 px | 73 px · 7 | ≤ 44 px · ≥ 14 | **44 px** · 7 lignes (le 1er tableau n'en a que 7 : **section entièrement visible** sur 900 px) ✔ (cible redéfinie, D4) |
| C5 | Hauteur de la page du tableau de bord | 4 103 px | ≤ 2 700 px (−35 %) | **2 653 px** (−35,3 %) ✔ |
| C6 | Textes sous AA : tableau de bord sombre, clair, 768 px · tickets sombre, clair | 21 · 55 · 22 · 8 · 8 | 0 partout | **0 · 0 · 0 · 0 · 0** (9 vues, 0/38 à 0/178) ✔ |
| C7 | Bases en alerte ouverte repérables dans leur ligne | 0 % | 100 % | **100 %** (5/5) ✔ |
| C8 | Contenu animé en continu | 1 | 0 | **0** ✔ |
| C9 | Cibles < 44 px à 390 px · < 32 px à 1440 px (tableau de bord, tickets) | 11 · 3, 28 | 0 | **0 · 0, 0** ✔ |
| C10 | Tableaux coupés sans repère à 768 px | 4 | 0 | **0** ✔ |
| C11 | Feuilles consommant les jetons · hex littéraux hors `tokens.css` | 0/3 · 57 | 3/3 · ≤ 15 | **3/3 · 0** ✔ |
| C12 | Tests : unitaires + E2E | 289 + 72 verts | tous verts | **301 + 81** verts (E2E : 35 + 43 + 3, 1 ignoré) ✔ |

## 7. Plan de travail

Légende : ☐ à faire · ◐ en cours · ☑ fait. Un lot = un commit **et un bundle** ; chaque lot se termine par l'audit (§8) et les tests.

- ☑ **U0 Outillage** — `demo/ui_audit.py` (mesures + captures reproductibles) ; base §2 figée.
- ☑ **U1 Jetons** — `tokens.css` ; anciens noms (`--dark-*`, `--white`, `--orange-*`, `--gray-*`) rebranchés sur les jetons ; fin de l'inversion du thème clair ; suppression du bleu ; `tickets.html` et `app-ui.css` consomment les jetons.
- ☑ **U2 Composants** — boutons, pastilles, liens, panneaux en une seule définition ; suppression des doublons ; page tickets sur jetons (`tickets.css`).
- ☑ **U3 En-tête et état global** — barre d'application, bande d'état (résumé serveur), menus, légende en dialogue.
- ☑ **U4 Données** — champ `db` dans les alertes ; pastille et teinte de ligne ; densité 44 px ; cellules empilées ; sections aplaties ; panneaux vides masqués ; avis statique.
- ☑ **U5 Responsive** — première colonne figée et indice de défilement ; barre mobile ; cartes avec état ; cibles 44 px.
- ☑ **U6 Autres pages** — supervision/admin : `kit-table`, largeurs, format des dates, `aria-current`.
- ☑ **U7 Clôture** — AA dans les deux thèmes, mouvements réduits, mise à jour des E2E, captures avant/après, README/REVUE, bundle.

## 8. Méthode de validation

1. `TEST_DB_HOST=127.0.0.1 demo/sandbox_stack.sh "$PWD" python3 "$PWD/demo/ui_audit.py" --out <dossier>` (**chemin absolu** : le script d'amorçage se place dans `demo/`) → `audit.json` + captures (mêmes mesures que §2 et §6).
2. `pytest` (unitaires, dont les tests statiques du front), `ruff`, `node --check` ; E2E complets avant clôture de chaque lot touchant le DOM (par fichier, en appels < 5 min).
3. Captures comparées à l'état précédent (1440, 1024, 768, 390 ; thèmes sombre et clair).
4. **Sauvegarde** : `git bundle create /mnt/user-data/outputs/… main revue/refonte-sqlalchemy feat/roles-d-ux feat/ui-refonte` après chaque lot. **Retour arrière** : un lot = un commit, `git revert` suffit.

## 9. Décisions, hypothèses, risques

- **D1** Orange conservé comme marque ; en texte sur fond clair on utilise l'orange brûlé `#b53d0a` (le `#ff6600` n'atteint pas 4,5:1 sur blanc).
- **D2** Aucun framework ni nouvelle police ; migration **incrémentale** par alias, **retirés en U7** (plus aucun ancien nom de variable dans le CSS ni le JS).
- **D3** Les identifiants/classes du contrat (§1) ne changent pas ; les nouveaux éléments ajoutent des classes. Exceptions assumées en U3 : cloche et badge flottants supprimés (remplacés par la bande d'état) ; thème, préférences, légende et lien Supervision passent dans le menu ⋯, le compte dans son menu.
- **D4** Mesures redéfinies en U7 : C2 compte les *boutons hors système* (ni `.btn-*`, ni composant nommé : bande d'état, onglets, éléments de menu) plutôt que les signatures de style ; C4 vérifie que la 1re section tient en entier sur 900 px, la cible « ≥ 14 lignes » étant inatteignable avec des tableaux de 7 lignes. Un contrôle de **débordement de la barre** (C3) a été ajouté après qu'une capture à 768 px a montré un chevauchement que les autres mesures ne voyaient pas.
- **D5** Les liens Supervision/Admin sont toujours affichés dans la navigation ; la page elle-même affiche « Connexion requise » ou « Droits insuffisants ». Masquer selon le rôle reste possible (non fait).
- **H1** Usage principal : écran de bureau et mur d'écran ; mobile en consultation. À confirmer.
- **R1** Régressions CSS dans les états peu testés (onglets roaming, barre de filtres, mur d'écran, thème clair) → captures systématiques de ces états.
- **R2** Les E2E dépendent de sélecteurs : toute modification de structure se fait avec mise à jour des tests dans le même commit.

## 10. Journal

| Date | Lot | Résumé |
|---|---|---|
| 2026-10-26 | — | Audit mesuré (pile simulée) ; diagnostic ; document créé. |
| 2026-10-26 | U0 | `demo/ui_audit.py` ajouté ; mesures de départ figées (§2, §6) ; branche `feat/ui-refonte`. |
| 2026-10-28 | U1 | `static/css/tokens.css` (sombre, clair, daltonisme, alias de transition) ; `styles.css`/`app-ui.css`/tickets n'ont plus de palette propre ; ~20 rustines du thème clair supprimées. Un test calcule les contrastes des jetons et a fait corriger 3 couleurs limites. |
| 2026-10-28 | U2 | Boutons : une définition (`.btn-primary/-secondary/-danger/-ghost`, `.btn-icon`, `.chip-*`) dans `app-ui.css`, anciennes retirées de `styles.css` ; CSS de la page tickets extrait vers `static/css/tickets.css` (≈ 450 → 125 lignes, ~60 % de code mort) ; carte ticket hiérarchisée (motif en tête, séries repliables). Tickets : boutons 6 → 3 styles. |
| 2026-11-06 | — | **Incident** : l'environnement de travail a été réinitialisé alors que U0 à U5 n'avaient jamais été sauvegardés dans un bundle. Reconstruction depuis le bundle `FINAL` du 24/10 en rejouant les modifications, les mesures d'audit déjà relevées servant de contrôle. **Règle ajoutée : un bundle par lot.** |
| 2026-11-06 | U3 (rejeu partiel) | Rejoués et vérifiés (301 tests, ruff, `node --check`) : U1, U2, et de U3 le champ `db`, le résumé serveur `resume` (dans l'ETag), les menus, la barre du tableau de bord, la bande d'état, la légende en dialogue, le menu de compte. **Restent à rejouer** : pages tickets/supervision/admin (U3), U4, U5, avec les recettes exactes de `CHANTIER_UI_REPRISE.md` et les valeurs d'audit de la première réalisation (page 2 616 px, contrastes 0, en-tête 56 px/6 commandes). |
| 2026-11-12 | U3 → U5 | Rejeu complet : barre et navigation sur toutes les pages, `app-ui.css` sur jetons, données (sections aplaties, lignes 44 px, valeur/delta empilés, 5 tailles de texte, avis GI statique, lignes en alerte repérées), colonne « Site » figée, indice de défilement, cartes mobiles avec état. Bundles `U3`, `U4`, `U5`. |
| 2026-11-12 | U6 | Corrections issues de l'audit : transitions `all` qui faussaient les contrastes, repère de défilement recalculé après les mini-courbes, barre mobile 113 → 61 px, ✕ 30 → 44 px. **Défaut trouvé sur capture** : à 768 px la barre débordait (point de rupture à 640 px) → repli sous 1100 px, contrôle de débordement ajouté à l'audit et aux E2E. Mur d'écran : valeurs agrandies. Dates admin uniformes. |
| 2026-11-12 | U7 | Alias de `tokens.css` retirés ; E2E : 7 tests migrés, 7 nouveaux (menus au clavier, bande d'état, légende, avis, indice de défilement, barre mobile, débordement de la barre à 3 largeurs). Mesures finales au §6. |
| 2026-11-13 | Nettoyage | Code mort retiré (Python : 6 éléments ; JS : `SectionVisibility`, helpers de glisser-déposer, getters et gestionnaires orphelins ; CSS : 19 classes), code commenté et `console.log` supprimés ; tendance de `metrics.py` réécrite (161 → ~80 lignes, constantes nommées) ; variables et helpers renommés ; docstrings vérifiées. **Une régression** (variables `end`/`endp` du roaming supprimées à tort) a été attrapée par les E2E et corrigée. E2E : 35 + 46 verts. |
| 2026-11-13 | Nettoyage (suite) | `main.js` : les actions appellent directement les gestionnaires (plus d'`Object.assign(window, …)` ni de wrapper `updateDashboardDOM`) ; règle du curseur des lignes cliquables déplacée dans `app-ui.css` ; garde-fou `tests/test_js_undefined_names.py` (eslint `no-undef`, ignoré si eslint absent) ; 17 docstrings supplémentaires, relues contre le code. Outil de renommage automatique : une f-string multiligne a été abîmée (détectée par ruff F821, réparée) — recherche d'autres corruptions : aucune. E2E : 81 verts. |
| 2026-11-13 | Contrôle mobile | L'audit visite désormais tickets, supervision et admin à 390 px (connecté) : 12 vues, 0 contraste en échec, 0 débordement de barre. Défaut vu sur capture : « Admin » coupé dans la navigation → liens resserrés sous 640 px. |
| 2026-11-14 | Test du 2ᵉ cycle | `test_second_cycle_is_stable_no_duplicates` n'échoue plus selon l'horloge : l'attente couvre le pire cas du planificateur (5 min 30 s, constante nommée) au lieu de 240 s. Code de production inchangé. |
