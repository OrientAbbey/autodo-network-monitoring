# Reprise du chantier UI — ce qui est fait, ce qui reste, et COMMENT le refaire

Écrit après la perte de l'environnement du 06/11/2026 : les lots U0 à U5 avaient été réalisés une première fois mais jamais sauvegardés.
Ils ont été **rejoués** depuis le bundle `FINAL` du 24/10 ; ce document décrit exactement ce qui reste à rejouer, avec les valeurs
mesurées lors de la première réalisation (« oracle ») pour vérifier que le résultat est identique.

## 1. État du dépôt à la remise (branche `feat/ui-refonte`)

| Lot | État | Contenu présent dans le dépôt |
|---|---|---|
| U0 | ☑ | `demo/ui_audit.py`, `CHANTIER_UI.md` |
| U1 | ☑ | `static/css/tokens.css` (+ alias de transition), `styles.css`/`app-ui.css` sans palette propre, tests des jetons (contrastes calculés) |
| U2 | ☑ | boutons canoniques et `.chip-*` dans `app-ui.css`, `static/css/tickets.css`, carte ticket hiérarchisée (`tickets.js`), actions (`ticket-actions.js`) |
| U3 | ◐ | **Fait** : champ `db` des alertes ; `tickets.alerts_summary()` et `resume` dans `/api/dashboard_data` (inclus dans l'ETag) ; menus (`Menus` dans `common.js`) ; barre d'application et styles de la bande d'état/menus dans `app-ui.css` ; en-tête du tableau de bord (`dashboard.html`) ; `alerts.js` (bande d'état, `flagRows`, `jumpToFirst`, panneau d'alertes vide masqué) ; `render.js`, `state.js`, `main.js` (actions `jumpToAlert/openHelp/toggleAppBar/dismissNotice`), `sections.js` (légende en dialogue), `theme-filters.js`, `session.js` (menu de compte), `i18n.js` (clés FR/EN) ; ancien en-tête, cloche et badges retirés de `styles.css`. **Reste** : §2 A. |
| U4 | ☐ | à refaire : §2 B |
| U5 | ☐ | à refaire : §2 C |
| U6 | ◐ | tickets faits (U2) ; reste : §2 A (page-kit, base_app) et dates/largeur du sélecteur (admin) |
| U7 | ☐ | clôture (E2E, alias, doc) : voir `CHANTIER_UI.md` §7 et §11 de la liste des points ouverts ci-dessous |

Contrôles à l'état de remise : `pytest` (hors E2E) **301 verts**, `ruff` propre, `node --check` sur tous les scripts.
Les E2E ne sont **pas** à jour (voir §4).

## 2. Recettes des étapes restantes

### A. Finir U3 (pages tickets, supervision, admin) et le ménage de `app-ui.css`

1. **`templates/tickets.html`** : remplacer le bloc `<div class="header">` (h1, `.header-tools` avec `#prefsBtn` et `#account-widget`, `.filters-toggle-container`) par
   ```html
   <header class="app-bar">
     <div class="app-bar__brand"><h1 class="app-bar__title">AUTODO</h1></div>
     <nav class="app-nav" aria-label="Navigation">
       <a href="/" data-i18n="nav.dashboard">…</a>
       <a href="/tickets/" aria-current="page" data-i18n="header.tickets">…</a>
       <a href="/supervision" data-i18n="nav.supervision">…</a><a href="/admin" data-i18n="nav.admin">…</a></nav>
     <div class="app-bar__actions">
       <button id="prefsBtn" class="btn-secondary btn-icon" type="button" data-action="openPrefs" data-i18n-attr="title:prefs.title;aria-label:prefs.title" title="Préférences" aria-label="Préférences">🎛️</button>
       <div id="account-widget" class="account-widget"></div></div>
   </header>
   <div class="container"><h2 class="page-heading">Network Monitoring Tickets</h2> …
   ```
   La barre est placée **avant** `.container` (pleine largeur, sticky). Le bloc `.filters-toggle-container` (bouton + `.filters-container`) passe dans `.results-info .button-group` ; son bouton devient `btn-secondary` ; supprimer le bouton « Dashboard » (il est dans la navigation). Remplacer les styles en ligne de `#ttrSummary` et `#activeFilters` par les classes `results-ttr` et `results-filters`, et retirer `style="flex:1"` du bouton Clear.
2. **`static/css/tickets.css`** : supprimer `.header`, `h1`, `.header-tools` ; `body { padding: 0 }` ; `.container { max-width:1400px; margin:0 auto; padding: var(--sp-5) var(--sp-4) }` ; ajouter `.page-heading { margin:0 0 var(--sp-4); font-size:var(--fs-lg); font-weight:var(--fw-bold) }`, `.results-ttr { margin-left:var(--sp-4); color:var(--accent); font-weight:var(--fw-semibold) }` ; dans le `@media (max-width:768px)` remplacer `body{padding}` par `.container{padding:var(--sp-3)}` ; `.intervals summary { display:flex; align-items:center; min-height:32px; … }`.
3. **`templates/base_app.html`** (supervision, admin) : même `<header class="app-bar">` (nav avec `aria-current="page"` selon `{% if requires == 'supervision' %}`), puis `<div class="main-container"><h2 class="page-heading" data-i18n="{{ title_key }}">{{ title_default }}</h2><main id="app" class="page-content" aria-live="polite"></main></div>`.
   `app-ui.css` : `.app-page .main-container { max-width:1400px; margin:0 auto; padding:var(--sp-4) }`, `.page-heading` (idem), `.page-content { padding:0 0 var(--sp-6); color:var(--text) }` ; supprimer `.header-tools` et `.page-title`.
4. **`static/js/page-kit.js`** : le tableau utilise `class: 'kit-table'` seul (la classe `detail-table` du tableau de bord grisait des colonnes d'en-tête).
5. **`app-ui.css` — consolidation** : remplacer tous les `var(--ui-*)` par les jetons (`--ui-surface`→`--surface`, `-2`→`--surface-2`, `-text`→`--text`, `-muted`→`--text-muted`, `-border`→`--border`, `-accent`→`--accent`, `-ok`→`--ok`, `-danger`→`--bad`, `-warn`→`--warn`, `-focus`→`--info`) ; **supprimer l'ancienne règle `.chip {background:var(--accent); color:#fff…}`** (elle écrase la pastille d'état) et faire utiliser `class: 'chip chip-muted'` à `supervision.js` ; tailles en `em` → jetons (≤0,8em→`--fs-xs`, 0,85–0,95em→`--fs-sm`, ~1em→`--fs-md`, 1,7em→`--fs-xl`) ; couleurs littérales → `var(--on-accent)` / `var(--on-role)` / `var(--role-*)` (`.role-badge`, `.maintenance-banner`, `.kit-ok`, `.kit-danger`, `.sound-banner`, `.wall-title`).
6. **`styles.css` — fonds colorés** : pour toute règle ayant `background: var(--danger|--success|--bad|--ok|--warn|--accent-fill)` avec un texte `white/#fff/var(--white)`, utiliser `color: var(--on-accent)` (sinon « 1 ouvertes » blanc sur rouge clair = 2,5:1). Sélecteurs concernés : `.alert-popup-header`, `.alert-section-title .badge-open/-closed`, `.section-title .badge-open/-closed`, `.section-type.gi/.camtel`.

### B. U4 — données (valeurs de la première réalisation)

1. **`styles.css`, blocs à réécrire sur les jetons** :
   - `.data-section { background:var(--surface); border:1px solid var(--border); border-radius:var(--radius-md); box-shadow:var(--shadow); margin-bottom:var(--sp-4); overflow:hidden }` ;
   - `.section-title { display:flex; align-items:center; gap:var(--sp-2); margin:0; padding:var(--sp-3) var(--sp-4); background:var(--surface-2); color:var(--text); border-left:4px solid var(--accent); border-bottom:1px solid var(--border); font-size:var(--fs-lg); font-weight:var(--fw-bold); cursor:pointer }` ; `.section-title.alertes-title { font-size:var(--fs-md); border-left-color:var(--border); border-bottom:0 }` ; `.section-title .badge-open/-closed { padding:2px var(--sp-2); border-radius:999px; font-size:var(--fs-xs); font-weight:var(--fw-bold); color:var(--on-accent) }` (fonds `--bad` / `--ok`) ;
   - **panneau d'alertes imbriqué aplati** : `.data-section .data-section { margin:0; border:0; border-top:1px solid var(--border); border-radius:0; box-shadow:none }` ; `[hidden]{display:none!important}` ;
   - tableaux : `.table-wrapper{overflow-x:auto}` ; `.data-table{width:100%;border-collapse:collapse;background:var(--surface)}` ; `th,td { height:var(--row-h); padding:0 var(--sp-2); text-align:center; border-bottom:1px solid var(--border) }` ; `th { height:32px; background:var(--surface-2); color:var(--text-muted); font-size:var(--fs-xs); font-weight:var(--fw-bold); text-transform:uppercase; letter-spacing:.04em }` ; survol `tbody tr:hover > td { background:var(--surface-2) }` ;
   - `.separator-col { border-left:1px solid var(--border)!important }` ; `.location-cell { text-align:left!important; background:var(--surface); color:var(--text); font-size:var(--fs-sm); font-weight:var(--fw-bold); white-space:nowrap }` ;
   - **cellules empilées** (valeur au-dessus, delta dessous : 21 colonnes tiennent dans 1400 px) : `.value { display:block; line-height:1.2; font-size:var(--fs-md); font-weight:var(--fw-semibold); font-variant-numeric:tabular-nums }` ; `.trend { display:block; margin:0; line-height:1.1; font-size:var(--fs-xs); font-weight:400; font-variant-numeric:tabular-nums }` ; `.traffic-cell,.loss-cell,.tcp-cell { font-variant-numeric:tabular-nums }` ; `.total-row > td { background:var(--surface-2); border-top:2px solid var(--border); font-weight:var(--fw-bold) }`.
   - **supprimer** `html { font-size: 90% }` (ramenait 12 px à 10,8 px) et le bloc `.gi-warning-banner` + `@keyframes scroll-left` ; **ramener toutes les tailles littérales** (`rem`/`em`) de `styles.css` sur les 5 jetons : 0,7/0,75/0,8rem→`xs` ; 0,85rem/0,85em/0,9rem/0,95em→`sm` ; 1rem/1,1rem/1,1em→`md` ; 1,2em/1,2rem/1,3rem/1,5em→`lg`.
2. **Avis GI statique** : dans la boucle de sections de `dashboard.html`, remplacer le bloc « Bannière d'avertissement pour GI » par
   `<div class="notice notice-warn" id="giNotice" role="note" data-notice-key="autodo.notice.gi.v1"><span class="notice__icon" aria-hidden="true">⚠</span><p class="notice__text" data-i18n="notice.gi">…</p><button type="button" class="btn-ghost btn-icon" data-action="dismissNotice" data-i18n-attr="aria-label:common.close" aria-label="Fermer">✕</button></div>`
   et ajouter dans `app-ui.css` : `.notice{display:flex;align-items:center;gap:var(--sp-3);margin:0 0 var(--sp-4);padding:var(--sp-2) var(--sp-3);background:var(--surface);border:1px solid var(--border);border-left:4px solid var(--info);border-radius:var(--radius-sm)} .notice-warn{border-left-color:var(--warn)} .notice__icon{font-size:var(--fs-lg);line-height:1;color:var(--warn)} .notice__text{flex:1;margin:0;font-size:var(--fs-sm)}`. (`main.js` gère déjà la fermeture mémorisée ; la clé `notice.gi` est dans `i18n.js`.)
3. **Repérage des lignes** (`app-ui.css`) : `tr[data-alert]:not([data-alert="none"]) > td { background: color-mix(in srgb, var(--bad) 10%, var(--surface)) }` ; `… > .location-cell { box-shadow: inset 4px 0 0 var(--bad) }` ; `.alert-flag { display:inline-block; margin-left:var(--sp-2); color:var(--bad); font-size:var(--fs-sm); line-height:1; vertical-align:middle }` ; `.location-cell .sparkline { display:inline-block; vertical-align:middle; margin:0 0 0 var(--sp-2); width:72px; height:18px }`.
4. `.btn-icon { flex:none; width:var(--control-h); min-width:var(--control-h); padding:0; font-size:var(--fs-md) }`.

### C. U5 — responsive

1. **`static/js/table-scroll.js`** (nouveau, à charger dans `dashboard.html` **avant** `mobile-cards.js`, et à ajouter dans `PAGE_SCRIPTS["dashboard.html"]` de `tests/test_frontend_static.py`) :
   ```js
   const TableScroll = { update() { $$('.table-wrapper').forEach(w => {
       const scrollable = w.offsetParent !== null && w.scrollWidth > w.clientWidth + 2;
       w.toggleAttribute('data-scrollable', scrollable);
       let hint = w.previousElementSibling?.classList.contains('scroll-hint') ? w.previousElementSibling : null;
       if (scrollable && !hint) { hint = el('div', { class: 'scroll-hint', 'aria-hidden': 'true', text: t('table.scroll_hint') }); w.before(hint); }
       if (hint) hint.hidden = !scrollable; }); } };
   ['dashboard-updated', 'resize'].forEach(n => (n === 'resize' ? window : document).addEventListener(n, () => requestAnimationFrame(() => TableScroll.update())));
   document.addEventListener('prefs-changed', e => { if (e.detail.key === 'lang') $$('.scroll-hint').forEach(h => { h.textContent = t('table.scroll_hint'); }); });
   ```
   (la clé `table.scroll_hint` est déjà dans `i18n.js`).
2. **`app-ui.css`** : `.data-table th:first-child, .data-table td.location-cell { position:sticky; left:0; z-index:1 }` ; `th:first-child { background:var(--surface-2); z-index:2 }` ; `td.location-cell { box-shadow:1px 0 0 var(--border) }` ; `.scroll-hint { padding:var(--sp-1) var(--sp-3); font-size:var(--fs-xs); color:var(--text-muted); background:var(--surface-2); border-bottom:1px solid var(--border) }` ; `.table-wrapper[data-scrollable] { box-shadow: inset -12px 0 10px -10px rgba(0,0,0,.35) }` ; mobile (≤640 px) : masquer `.app-bar__meta .last-update/.next-label/.countdown` ; `.mobile-card[data-alert]:not([data-alert="none"]) { border-left:4px solid var(--bad); background:color-mix(in srgb,var(--bad) 8%,var(--surface)) }` ; `.mobile-card .chip { margin-left:var(--sp-2) }`.
3. **`mobile-cards.js`** : ajouter `alert: row.dataset.alert || 'none'` à l'objet carte, `dataset: { db, alert }` sur l'`<article>`, et dans le `<h3>` une pastille `chip chip-bad` « ● <types> » (via `AlertManager.typeLabel`) quand `alert !== 'none'`.

## 3. Oracle : valeurs mesurées lors de la première réalisation (à retrouver)

Après U5, `demo/ui_audit.py` avait donné : polices 6 · en-tête 56 px avec 6 commandes · ligne 44 px · page **2 616 px** · contrastes **0** partout (tableau de bord sombre/clair/768/390, tickets sombre/clair, supervision, admin) · 5 bases sur 5 repérées · 0 animation · 0 tableau coupé sans repère à 768 px · jetons consommés par 3 feuilles sur 3, 5 couleurs littérales hors `tokens.css`. **Écarts restants alors** : barre mobile 113 px (cible ≤ 64), un ✕ de 30 px (probablement `.close-sidebar-btn` hors écran), 11 « styles de boutons » (mesure à redéfinir), 7 lignes visibles sur 900 px (cible à ré-évaluer).

## 4. E2E à mettre à jour (7 échecs constatés, tous liés aux menus)

`test_e2e_playwright::test_icon_buttons_have_accessible_names_and_toggle_state`, `::test_keyboard_only_reaches_the_main_controls` ; `test_e2e_ui::test_language_switch_translates_and_persists`, `::test_palette_and_theme_preferences`, `::test_share_link_applies_language_theme_palette_but_never_sound` (`#prefsBtn` est dans le menu `#moreBtn`) ; `::test_role_gates_on_reserved_pages` (`#nav-*` n'existent plus : utiliser `.app-nav a[href="/supervision"]`) ; `::test_logout_returns_to_visitor_and_leaves_reserved_pages` (`#logoutBtn` est dans le menu `#accountBtn`). Les autres (33 + 29) passaient. À écrire : tests des menus au clavier, de la bande d'état (nominal/alerte, clic), du repérage des lignes, de la légende, de l'avis fermable, de l'indice de défilement, de la barre mobile.

## 5. Autres points ouverts

Règles du mur d'écran (`body.wall .controls` dans `app-ui.css` vise l'ancien conteneur → `.app-bar__actions`) · formats de dates et largeur du sélecteur de période (admin) · `.tab-btn` sans `font-size` (13,3 px) · redéfinir la mesure C2 · retirer les alias de transition de `tokens.css` · contrôle visuel du thème clair et du mur d'écran · README/REVUE · captures avant/après.
