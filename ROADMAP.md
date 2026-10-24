# AUTODO — propositions d'évolution

Document de travail : fonctionnalités nouvelles, améliorations de l'existant, UX/UI/son, détection intelligente.
Les chiffres de charge viennent de mesures réelles sur la pile de démonstration (voir `tests/e2e/test_e2e_load.py`) ;
les résultats de détection viennent d'un prototype sur **données simulées** (`demo/anomaly_prototype.py`) — à rejouer sur
l'historique réel avant toute décision. Les estimations d'effort (S ≤ 3 j, M ≤ 2 sem., L > 2 sem.) sont indicatives.

## 0. État d'avancement (mis à jour après les phases A à D)

Légende : ✅ réalisé et testé · 🟡 partiel (précisé) · ⬜ non réalisé. Les sections 1 à 9 ci-dessous gardent leur texte d'origine (raisonnement,
estimations) ; c'est ce tableau qui fait foi pour l'état du code.

| Réf. | État | Précision |
|---|---|---|
| A1 rôles et comptes | ✅ | comptes nominatifs ; hachage **scrypt** (bibliothèque standard) et non argon2 ; rôles `supervision` / `admin` ; pas de rôle « opérateur » distinct (le visiteur acquitte) |
| A2 audit · A3 versions de configuration | ✅ | audit consultable + export CSV ; versions avec diff et restauration ; **pas** d'approbation à deux personnes |
| A4 simulation « et si ? » | ✅ | réservée à l'admin ; trafic et pertes uniquement (pas DB_DOWN) ; indicative |
| A5 maintenance · A6/D8 seuils par base | ✅ | silence automatique après rejet en plus |
| A7 santé · A8 sessions | 🟡 | santé complète sauf **taille des tables** ; sessions listées et révocables |
| A9 qualité des alertes | 🟡 | retours « réel / faux positif » + rapport du mode ombre ; **pas** de tableau MTTD / TTR médian |
| D1, D5 | ✅ | mode ombre + rapport vs tickets ; D1 ignore jours fériés et distinction ouvré/week-end |
| D2 historique local | ✅ | `metrics_5min`, import DIAGNET, rétention 400 j ; **pas** d'agrégats 1 h / 1 j matérialisés |
| D3 prévision et capacité | 🟡 | Holt-Winters 24 h et jours avant saturation, **à la demande** (page Admin) ; la bande « attendu » des courbes vient de D1, pas de D3 |
| D4 Isolation Forest · D7 qualité | 🟡 | entraînement, score en mode ombre ; **pas** de prédiction du TTR, de file « à confirmer », de réentraînement planifié ni de suivi de dérive ; **aucune** donnée réelle évaluée |
| D6 corrélation entre bases | ⬜ | nécessite la topologie (décision 3) |
| T1, T2, T4, T5 | ✅ | `data_version` SSE avec gigue, ETag/304, mur d'écran, présence et pics |
| T3 mises à jour différentielles · T7 multi-workers | ⬜ | |
| T6 préférences par spectateur | 🟡 | langue, thème, palette, son, voix, notifications + lien partageable ; **sections repliées non mémorisées** |
| U1 · U3 · U6 · U10 | ✅ | cartes mobiles (pas de navigation basse) ; mini-courbes ; chronologie ; FR/EN (hors aide longue et textes serveur) |
| U5 prise en charge | 🟡 | commentaires datés et accusé partagé ; **pas** de « je m'en occupe » |
| U7 accessibilité | 🟡 | `aria-live`, focus de la pop-up, clavier sur les sections, palette daltonisme ; **contrastes AA non mesurés**, navigation clavier des tableaux non faite |
| U2 feux tricolores · U4 centre d'alertes · U8 raccourcis · U9 export CSV/PDF des tickets | ⬜ | seul l'export CSV de l'audit existe |
| S1, S3, S5, S6 | ✅ | son désactivé par défaut ; accusé partagé ; voix ; bandeau d'autoplay, notifications système, titre clignotant |
| S2 sons distincts · S4 escalade · S7 équivalents non sonores | ⬜ | (le clignotement du titre est un début de S7) |
| S8 heures calmes | 🟡 | une base en maintenance ne produit ni ticket ni alerte donc pas de sonnerie ; pas de réglage horaire |
| §8 Orange : notes de mise à jour | ✅ | **format de la note à confirmer** avec l'API réelle |
| §8 autres (Teams/SMS, Prometheus, HA, rotation des secrets) | ⬜ | |

**Décisions de la section 10** : (1) *comptes nominatifs locaux dès maintenant* → retenu et réalisé ; (4) le son est **par poste** (préférence du
navigateur) ou `?wall=1&sound=1` ; (5) rétention de l'historique : 400 jours par défaut, réglable (`HISTORY_RETENTION_DAYS`).
Restent ouvertes : (2) nombre de tickets historiques étiquetables, (3) topologie câble → sites.

## 1. Contraintes qui structurent les choix

| Contrainte | Conséquence |
|---|---|
| Bases DIAGNET en **lecture seule** | tout apprentissage/historique doit être **copié localement** (on lit, on n'écrit jamais chez DIAGNET) |
| **25 à 50 spectateurs permanents** sur le tableau de bord | lectures très majoritaires, pics simultanés après chaque cycle (tous rechargent à `hh:02`, `hh:07`…) ; sons/alertes à ne pas multiplier par 50 |
| **Seuls les administrateurs** gèrent la configuration et la page *Management* | séparer clairement rôles (spectateur / opérateur / admin), tracer chaque changement, pouvoir l'annuler |
| Pas de SSO pour l'instant | comptes locaux nominatifs (mots de passe hachés) comme étape intermédiaire, interface d'authentification remplaçable |
| Détection actuelle = règles à seuils globaux | bonne sur les pannes franches, aveugle aux dégradations lentes et aux écarts « normaux pour ce site à cette heure » |

**Mesures actuelles (démo, machine unique)** : 50 spectateurs × 12 requêtes + 50 flux SSE ouverts → médiane **69-117 ms**, p95 **169-238 ms** (selon l'exécution),
**0 erreur**, écritures SQL groupées (50 clients = 50 lignes, pas 600 écritures). Charge utile du tableau de bord : **113 Ko brut,
19 Ko compressé** (gzip activé dans cette version). La charge n'est donc pas le problème aujourd'hui ; le gain à viser est
l'**expérience** (clarté, bruit sonore, détection plus fine) et la **gouvernance** (qui change quoi).

## 2. Principe directeur : des couches, pas un remplacement

```
 règles à seuils (filet de sécurité, explicables, inchangées)
   + référence saisonnière robuste (« attendu ce créneau ») → écarts discrets
   + modèle multivarié non supervisé (Isolation Forest…)    → combinaisons inhabituelles
   + modèle supervisé sur l'historique de tickets            → qualité/priorité de l'alerte, TTR
        ↓
   politique de décision (fusion des scores) → ticket / avertissement / simple annotation
```
Règles d'adoption : (1) **mode « ombre »** 2 à 4 semaines (les nouveaux détecteurs écrivent leurs verdicts sans créer de ticket) ;
(2) comparaison automatique avec les tickets réels (rappel, faux positifs, délai de détection) ; (3) **toute alerte porte une
explication lisible** (« −23 % vs mardi 18:00, z = −4,1 ») ; (4) bascule par base et par type, réversible ; (5) l'humain valide les
cas douteux (retour d'information « vrai / faux incident » stocké pour l'apprentissage).

## 3. Détection intelligente

### 3.1 Résultats du prototype (séries simulées, 10 anomalies par type, fenêtre d'évaluation de 14 jours)

| Détecteur | chute brutale (−65 %) | chute de 20 % | dérive lente (−4 %/pas) | écrêtage à l'heure de pointe | trafic nul | faux positifs / jour |
|---|---|---|---|---|---|---|
| **Règles actuelles** | 10/10, délai 0 | **1/10** | **0/10** | 6/6 | 10/10 | 0,14 |
| Référence saisonnière robuste (médiane/MAD sur 7 jours) | 10/10, 1,2 pas | **8/10**, 3,2 pas | **10/10**, 6,7 pas | 1/6 | 10/10 | 0,00 |
| Isolation Forest (5 variables) | 10/10, 1,0 pas | **9/10**, 1,2 pas | **10/10**, 4,1 pas | 3/6 | 10/10 | 0,00 |

Lecture : les règles sont excellentes pour les pannes franches mais **laissent passer les baisses de 20 % et les dérives lentes** ;
les méthodes statistiques/ML les voient, au prix d'un délai de quelques pas de 5 minutes, et sont **complémentaires** (les règles
détectent mieux l'écrêtage brusque). Réserves : données simulées (bruit gaussien de 5 %, pas de jours fériés ni de pannes
d'équipement voisines), Isolation Forest entraîné et évalué sur le même générateur, `contamination` réglée à la main. Sur données
réelles, attendez-vous à plus de faux positifs : c'est précisément ce que le mode « ombre » mesure.

### 3.2 Propositions

| # | Proposition | Apport | Données requises | Effort | Risque |
|---|---|---|---|---|---|
| D1 | **Référence saisonnière** par base (médiane/MAD du même créneau, jour ouvré/week-end, jours fériés) | détecte les écarts « anormaux pour cette heure » ; remplace le seuil global de 40 % par un seuil **dynamique** par site/heure | 4-8 semaines d'historique | M | faible (explicable) |
| D2 | **Historique local `metrics_5min`** (copie des séries lues, rétention 400 j, agrégats 1 h / 1 j) | prérequis de D1, D3-D6 ; graphes de tendance | espace disque modeste (≈ 20 bases × 288 pts/j) | S | faible |
| D3 | **Prévision** (Holt-Winters/ETS ou régression à retards LightGBM) → bande « attendu ± intervalle » affichée sur les courbes | alerte « sous la bande depuis N pas » ; **planification de capacité** (tendance 30-90 j, date de saturation des liens) | D2 | M | moyen (qualité selon saisonnalité) |
| D4 | **Détection multivariée** (Isolation Forest / LOF / autoencodeur léger) sur trafic, pertes, latence, TCP no-response, DNS | repère des combinaisons inhabituelles même si chaque métrique reste « dans les seuils » | D2 | M | moyen (réglage, dérive) |
| D5 | **Détection de ruptures** (CUSUM, PELT) | niveau de trafic qui change durablement (nouvel équipement, bascule de routage) | D2 | S | faible |
| D6 | **Corrélation d'alertes** entre bases/sites (même heure, même fournisseur/câble) | **un seul ticket parent** au lieu d'une rafale ; suppression des tempêtes ; piste de cause commune | topologie (câble → sites) fournie par l'équipe | M | moyen (qualité de la topologie) |
| D7 | **Modèle supervisé sur la base de tickets** (random forest / gradient boosting) | (a) *score de qualité d'alerte* : probabilité que l'alerte soit un vrai incident → moins de faux tickets chez Orange ; (b) **prédiction du TTR** ; (c) suggestion de catégorie/priorité | **base de tickets garnie et étiquetée** : viser ≥ 1 000 incidents (≥ 100 faux positifs identifiés) | L | élevé si étiquettes de mauvaise qualité |
| D8 | **Seuils par base** (surcharges de configuration par site) | adapter sensibilité aux sites petits/bruités | aucune | S | faible |

**D7 — précautions** : étiquettes = résolution réelle (motif de clôture, durée, retour opérateur « faux positif ») et non le simple fait
qu'un ticket existe (biais : le système actuel n'a créé que ce que les règles ont vu) ; découpage **temporel** pour la validation
(jamais aléatoire) ; fuite d'information (ne pas utiliser des champs connus après coup comme le TTR pour prédire la qualité) ;
déséquilibre de classes (pondération, métriques précision/rappel plutôt que l'exactitude) ; modèle versionné, réentraînement
planifié, surveillance de la dérive, retour arrière en un clic ; le modèle **n'ouvre ni ne ferme jamais seul** un ticket critique :
il module la priorité et alimente une file « à confirmer ».

**Où cela se range dans le code** : un module `core/anomaly.py` (fonctions pures + un registre de détecteurs), un champ `detector`
et `explanation` sur les verdicts, la table `metrics_5min` et `detector_verdicts` (mode ombre) via une migration de plus dans `db.MIGRATIONS`.
Les dépendances ML (scikit-learn, éventuellement LightGBM) restent **optionnelles** (extra `requirements-ml.txt`) : l'application
fonctionne sans.

## 4. Tableau de bord pour 25-50 spectateurs permanents

État mesuré : voir §1. Propositions, par ordre de valeur :

| # | Proposition | Détail | Effort |
|---|---|---|---|
| T1 | **Pousser une « version de données » par SSE** puis laisser chaque client récupérer le JSON (déjà compressé) ; évite le rafraîchissement par compte à rebours côté navigateur et les décalages entre écrans | supprime l'écart d'heure entre spectateurs ; rafale unique à `hh:02` lissée par une **gigue aléatoire de 0-3 s** côté client | S |
| T2 | **Mise en cache du JSON sérialisé** par version + `ETag` (réponse 304 quand rien n'a changé) | sérialisation faite une fois par cycle au lieu d'une fois par requête ; sans effet visible à 50 clients, utile au-delà de 200 | S |
| T3 | **Mises à jour différentielles** (seules les cellules modifiées) + **surbrillance** des valeurs qui viennent de changer | les opérateurs voient *ce qui a bougé* d'un coup d'œil ; moins de repeint du DOM | M |
| T4 | **Mode « mur d'écran » (NOC)** : `?wall=1`, grandes polices, rotation automatique des sections, aucun contrôle d'administration | écran partagé en salle d'exploitation, distinct des postes individuels | S |
| T5 | **Présence et mesure d'usage** : nombre de spectateurs connectés (déjà dérivable des flux SSE), pic de concurrence dans la page *Management* | dimensionnement factuel (quand passer à plusieurs workers + Redis) | S |
| T6 | **Préférences par spectateur** (thème, sections repliées, son) stockées dans le navigateur, partageables par URL | plus de configuration globale polluée par les goûts de chacun | S |
| T7 | **Passage à l'échelle** si > 200 spectateurs : plusieurs workers (état de sessions et abonnés dans Redis ou la base), un seul « leader » ordonnanceur via verrou en base | sans objet à 50 ; à documenter comme plan B | M |

## 5. UX / UI

| # | Proposition | Détail |
|---|---|---|
| U1 | **Responsive réel** | corrigé dans cette version : barre de boutons et pop-up ne débordent plus sur mobile (test automatisé). Reste : tableaux denses en cartes sur petit écran, navigation basse |
| U2 | **Vue d'ensemble « feux tricolores »** | une grille de pastilles par site/lien (vert/orange/rouge) en tête de page : l'état global en 2 secondes avant le détail chiffré |
| U3 | **Mini-courbes (sparklines) 24 h** dans chaque cellule + bande « attendu » (D3) | voir la tendance sans ouvrir ExNet |
| U4 | **Centre d'alertes** | liste unifiée, tri par gravité/ancienneté, regroupement par incident corrélé (D6), filtres mémorisés dans l'URL |
| U5 | **Prise en charge et notes** | « je m'en occupe » + commentaires datés sur un ticket (nécessite une identité : comptes locaux, §7) ; l'accusé de réception **partagé** coupe la sonnerie chez tous |
| U6 | **Chronologie d'un ticket** | création, mises à jour de valeurs, avertissements de pertes, tentatives Orange, fermeture, TTR — aujourd'hui éclatés dans les champs |
| U7 | **Accessibilité** | déjà : rôles ARIA, focus de la pop-up, `Échap`, libellés. À ajouter : palette adaptée au daltonisme, **icône + texte en plus de la couleur**, contrastes AA, navigation clavier des tableaux, annonces `aria-live` des nouvelles alertes |
| U8 | **Recherche globale et raccourcis clavier** (`/` rechercher, `g t` tickets, `?` aide) | |
| U9 | **Export** CSV/PDF des tickets et d'un rapport hebdomadaire (disponibilité, TTR, top des incidents) | |
| U10 | **Français / anglais** et fuseau d'affichage choisi | les libellés sont aujourd'hui mélangés |

## 6. Sonore

État actuel : une sonnerie en boucle à l'arrivée d'une alerte + une note de cloche ; arrêt à l'acquittement **local**. Avec 25-50 écrans
ouverts en permanence, c'est le principal risque de nuisance et de désensibilisation.

| # | Proposition | Détail |
|---|---|---|
| S1 | **Son réservé aux écrans désignés** | le son n'est actif que sur les clients qui l'ont activé explicitement (case « Activer le son » mémorisée, ou `?sound=1` sur le mur d'écran) ; les autres postes restent visuels |
| S2 | **Sons distincts par gravité/type** | trafic nul ≠ chute ≠ pertes ≠ base figée ≠ source injoignable ; une seule tonalité courte pour l'information, répétition seulement pour le critique |
| S3 | **Acquittement partagé** | une personne acquitte ⇒ la sonnerie s'arrête partout (événement SSE), avec nom/heure dans l'historique |
| S4 | **Escalade progressive** | volume croissant ou répétition espacée tant que non acquitté, plafonnée ; silence automatique à la clôture |
| S5 | **Annonce vocale** (Web Speech API) « Alerte trafic, Douala CAMTEL, chute de 62 % » | utile sur le mur d'écran ; langue configurable |
| S6 | **Règles navigateur** | demande de geste utilisateur pour autoriser l'audio (politique d'autoplay), bouton de test déjà présent, **notifications système** et clignotement du titre d'onglet quand la page est en arrière-plan |
| S7 | **Équivalents non sonores** | flash visuel, vibration mobile, e-mail/Teams — pour les personnes malentendantes et les écrans muets |
| S8 | **Heures calmes / maintenance** | pas de sonnerie pendant une fenêtre de maintenance déclarée (§7) |

## 7. Administration et page *Management* (admins uniquement)

| # | Proposition | Détail |
|---|---|---|
| A1 | **Rôles** : spectateur (aucun compte) · opérateur (acquitter, commenter) · admin (configuration, management) | comptes locaux nominatifs, mots de passe hachés (argon2), session à durée courte, verrouillage ; même interface qu'un futur SSO |
| A2 | **Journal d'audit enrichi** | qui, quand, **ancienne et nouvelle valeur** (déjà dans les logs, à rendre consultable) ; export |
| A3 | **Historique de configuration avec diff et retour arrière** | chaque enregistrement crée une version ; bouton « restaurer » ; approbation à deux personnes pour les seuils sensibles (optionnel) |
| A4 | **Simulation « et si ? » avant enregistrement** | rejoue la nouvelle configuration sur les 7 derniers jours (D2) : « ce seuil aurait produit 14 alertes de plus dont 11 non suivies de ticket » — réduit le risque de dérégler la supervision |
| A5 | **Fenêtres de maintenance** | période + bases concernées : alertes annotées/supprimées, tickets non créés, retour à la normale automatique |
| A6 | **Seuils par base** (D8) et **activation par catégorie** (existant) dans un éditeur unique | |
| A7 | **Santé du système dans *Management*** | état des sources (dernière lecture, durée), file Orange (en attente, échecs, disjoncteur), cycles, watchdog, usage (spectateurs, pic), taille des tables, rétention |
| A8 | **Gestion des sessions** | liste des sessions administrateur, révocation |
| A9 | **Qualité des alertes** | tableau « vrai/faux incident » alimenté par les retours opérateurs (nourrit D7) et indicateurs : précision, délai de détection (MTTD), TTR médian, taux de tickets sans action |

## 8. Intégrations et exploitation

* **Notifications multi-canaux** : Teams/Slack/webhook et SMS en plus de l'e-mail, avec routage par site et astreinte (calendrier), accusé de réception.
* **Orange** : mise à jour du ticket distant quand les valeurs évoluent (commentaires), pas seulement création/restauration ; rapprochement périodique déjà en place.
* **Observabilité** : export Prometheus (`/metrics` : durée de cycle, sources en échec, file Orange, abonnés SSE), tableau Grafana fourni ; traces OpenTelemetry.
* **Haute disponibilité** (si exigée) : deux instances, verrou d'élection en base pour l'ordonnanceur, base locale répliquée ; sauvegardes automatiques de `tickets_tb`.
* **Sécurité** : rotation planifiée des secrets, SSO/OIDC dès disponible, journalisation d'accès par rôle, test d'intrusion avant mise en production élargie.

## 9. Feuille de route proposée

| Phase | Contenu | Résultat attendu | Effort |
|---|---|---|---|
| **0 — Fondations** (≈ 2 sem.) | D2 historique local, T1/T2/T5, A7 (santé dans *Management*), S1/S3 (son ciblé + acquittement partagé), U2 (feux tricolores) | mesures disponibles, moins de bruit sonore, vue d'ensemble | M |
| **1 — Détection ombre** (≈ 4-6 sem.) | D1 référence saisonnière + D8 seuils par base + D5 ruptures en **mode ombre**, tableau de comparaison avec les tickets réels | chiffres réels de rappel / faux positifs / délai | M |
| **2 — Gouvernance** (≈ 3-4 sem.) | A1 rôles et comptes, A2/A3 audit + versions de configuration, A4 simulation, A5 maintenance, U5 prise en charge | changements de configuration sûrs et tracés | M-L |
| **3 — Bascule graduelle** | graduation des détecteurs validés (par base), D6 corrélation, D3 prévision et bandes sur les courbes (U3) | moins de tickets en rafale, détection des dérives | M-L |
| **4 — Apprentissage supervisé** (quand ≥ 1 000 tickets étiquetés) | D7 qualité d'alerte, prédiction du TTR, file « à confirmer » ; MLOps léger | moins de faux tickets chez Orange, priorisation | L |
| **5 — Confort et ouverture** | U4/U6/U8/U9/U10, S2/S4/S5, Prometheus/Grafana, notifications Teams/SMS | usage quotidien plus fluide, intégration NOC | M-L |

**Indicateurs de succès** (à mesurer dès la phase 0) : faux positifs par jour et par base ; délai moyen de détection (MTTD) ; TTR médian ;
part de tickets sans action opérateur ; temps entre alerte et acquittement ; nombre de spectateurs simultanés (pic) ; incidents de
supervision (source injoignable, cycle manqué).

## 10. Décisions à prendre

1. Souhaitez-vous des **comptes nominatifs locaux** dès maintenant (A1), ou attendre un SSO ? (conditionne U5, S3, A2, A3)
2. Combien de **tickets historiques étiquetés** existent aujourd'hui, et un retour « vrai/faux incident » est-il réalisable par les opérateurs ? (conditionne D7)
3. Existe-t-il une **topologie** (câble → sites → clients) exploitable pour la corrélation (D6) ?
4. Quels écrans sont de vrais **postes d'exploitation** (son activé) et lesquels sont de simples consultations ?
5. Quelle **durée d'historique local** (D2) la politique de données autorise-t-elle ?
