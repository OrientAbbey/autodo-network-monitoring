// AUTODO — Libellés des paramètres de configuration (FR/EN), regroupés par thème. Utilisés par /supervision et /admin.
// Clé = nom du champ dans ConfigParams (core/config.py). Un test vérifie qu'aucun paramètre n'est oublié.

const PARAM_GROUPS = ['traffic', 'losses', 'tcp', 'timing', 'recovery', 'sections', 'governance'];

const PARAM_META = {
    alert_traffic_threshold_percent: { group: 'traffic', unit: '%', fr: 'Baisse instantanée critique', en: 'Critical instant drop',
        help: { fr: 'Alerte si le trafic chute d\'au moins ce pourcentage entre deux relevés consécutifs.', en: 'Alert when traffic falls by at least this percentage between two consecutive samples.' } },
    traffic_consecutive_drops_count: { group: 'traffic', unit: '', fr: 'Nombre de baisses consécutives', en: 'Consecutive drops',
        help: { fr: 'Nombre de relevés successifs en baisse avant d\'alerter.', en: 'Number of successive decreasing samples before alerting.' } },
    traffic_consecutive_drops_min_percent: { group: 'traffic', unit: '%', fr: 'Première baisse minimale', en: 'Minimum first drop',
        help: { fr: 'Ampleur minimale de la première baisse d\'une série de baisses consécutives.', en: 'Minimum size of the first drop in a series of consecutive drops.' } },
    traffic_up_threshold_percent: { group: 'traffic', unit: '%', fr: 'Hausse importante', en: 'Significant increase',
        help: { fr: 'Hausse brutale du trafic considérée comme anormale.', en: 'Sudden traffic increase considered abnormal.' } },
    zero_traffic_min_samples: { group: 'traffic', unit: '', fr: 'Relevés à zéro avant alerte', en: 'Zero samples before alert',
        help: { fr: 'Nombre de relevés à trafic nul qui confirment une coupure.', en: 'Number of zero-traffic samples that confirm an outage.' } },
    alert_pertes_threshold: { group: 'losses', unit: '%', fr: 'Seuil de pertes', en: 'Loss threshold',
        help: { fr: 'Taux de pertes TCP au-delà duquel on alerte.', en: 'TCP loss rate above which an alert is raised.' } },
    pertes_consecutive_count: { group: 'losses', unit: '', fr: 'Relevés de pertes consécutifs', en: 'Consecutive loss samples',
        help: { fr: 'Nombre de relevés successifs au-dessus du seuil avant d\'alerter.', en: 'Number of successive samples above the threshold before alerting.' } },
    alert_tcp_no_response_threshold: { group: 'tcp', unit: '%', fr: 'TCP sans réponse', en: 'TCP no response',
        help: { fr: 'Part de SYN sans réponse au-delà de laquelle on alerte.', en: 'Share of unanswered SYN above which an alert is raised.' } },
    alert_tcp_block_wp_up_threshold: { group: 'tcp', unit: '%', fr: 'TCP « block WP up »', en: 'TCP block WP up',
        help: { fr: 'Part de sessions bloquées côté montant.', en: 'Share of sessions blocked on the uplink.' } },
    alert_latence_threshold_ms: { group: 'tcp', unit: 'ms', fr: 'Latence maximale', en: 'Maximum latency',
        help: { fr: 'Latence TCP moyenne au-delà de laquelle on alerte.', en: 'Average TCP latency above which an alert is raised.' } },
    alert_persistence_duration_minutes: { group: 'timing', unit: 'min', fr: 'Persistance avant fermeture', en: 'Persistence before closing',
        help: { fr: 'Durée pendant laquelle les critères de fermeture doivent rester vrais avant de fermer le ticket.', en: 'How long closing criteria must hold before the ticket is closed.' } },
    db_down_threshold_minutes: { group: 'timing', unit: 'min', fr: 'Base muette depuis (DB_DOWN)', en: 'Silent database for (DB_DOWN)',
        help: { fr: 'Durée sans nouveau relevé qui déclenche un ticket DB_DOWN.', en: 'Time without a new sample that raises a DB_DOWN ticket.' } },
    source_unreachable_cycles: { group: 'timing', unit: '', fr: 'Cycles d\'injoignabilité tolérés', en: 'Unreachable cycles tolerated',
        help: { fr: 'Nombre de cycles consécutifs où une source est injoignable avant de la signaler.', en: 'Consecutive cycles a source may be unreachable before being reported.' } },
    traffic_recovery_ratio: { group: 'recovery', unit: '', fr: 'Ratio de reprise du trafic', en: 'Traffic recovery ratio',
        help: { fr: 'Le trafic doit retrouver ce ratio de sa valeur d\'avant incident pour fermer le ticket (0 à 1).', en: 'Traffic must regain this ratio of its pre-incident value to close the ticket (0 to 1).' } },
    mode7days_recovery_ratio: { group: 'recovery', unit: '', fr: 'Ratio de reprise (mode 7 jours)', en: 'Recovery ratio (7-day mode)',
        help: { fr: 'Ratio de reprise quand la comparaison se fait avec la semaine précédente.', en: 'Recovery ratio when compared with the previous week.' } },
    db_recovery_threshold_minutes: { group: 'recovery', unit: 'min', fr: 'Reprise d\'une base muette', en: 'Silent database recovery',
        help: { fr: 'Durée de relevés frais avant de fermer un ticket DB_DOWN.', en: 'Duration of fresh samples before closing a DB_DOWN ticket.' } },
    alerts_enable_international: { group: 'sections', unit: '', fr: 'Sortie internationale', en: 'International exit', help: { fr: 'Active les alertes de cette section.', en: 'Enables alerts for this section.' } },
    alerts_enable_gi: { group: 'sections', unit: '', fr: 'Trafic utilisateur (Gi)', en: 'User traffic (Gi)', help: { fr: 'Active les alertes de cette section.', en: 'Enables alerts for this section.' } },
    alerts_enable_international_gi: { group: 'sections', unit: '', fr: 'International + GI (TCP)', en: 'International + GI (TCP)', help: { fr: 'Active les alertes de cette section.', en: 'Enables alerts for this section.' } },
    alerts_enable_roaming: { group: 'sections', unit: '', fr: 'Roaming', en: 'Roaming', help: { fr: 'Active les alertes de cette section.', en: 'Enables alerts for this section.' } },
    alerts_enable_tdd: { group: 'sections', unit: '', fr: 'TDD', en: 'TDD', help: { fr: 'Active les alertes de cette section.', en: 'Enables alerts for this section.' } },
    ticket_validation_required: { group: 'governance', unit: '', fr: 'Validation humaine des créations de tickets', en: 'Human approval of ticket creation',
        help: { fr: 'Si activé, un superviseur doit valider chaque ticket avant qu\'il parte chez Orange et par e-mail.', en: 'When on, a supervisor must approve each ticket before it goes to Orange and by e-mail.' } },
    rejection_silence_minutes: { group: 'governance', unit: 'min', fr: 'Silence après rejet / suppression', en: 'Silence after reject / delete',
        help: { fr: 'La base reste muette ce temps après le rejet ou la suppression d\'un ticket (0 = désactivé).', en: 'The database stays silent for this long after a ticket is rejected or deleted (0 = off).' } },
    orange_note_min_change_percent: { group: 'governance', unit: '%', fr: 'Variation minimale pour une note Orange', en: 'Minimum change for an Orange note',
        help: { fr: 'Une note de mise à jour est envoyée au ticket distant si la valeur varie d\'au moins ce pourcentage.', en: 'An update note is sent to the remote ticket when the value changes by at least this percentage.' } },
    orange_note_min_interval_minutes: { group: 'governance', unit: 'min', fr: 'Intervalle minimal entre notes', en: 'Minimum interval between notes',
        help: { fr: 'Délai minimal entre deux notes envoyées au même ticket.', en: 'Minimum delay between two notes sent to the same ticket.' } },
    shadow_detectors: { group: 'governance', unit: '', fr: 'Détecteurs expérimentaux (mode ombre)', en: 'Experimental detectors (shadow mode)',
        help: { fr: 'Exécute les détecteurs « futurs » à chaque cycle sans jamais créer de ticket.', en: 'Runs the "future" detectors every cycle without ever creating a ticket.' } }
};

function paramLabel(name) {
    const meta = PARAM_META[name];
    return meta ? meta[Prefs.get('lang')] || meta.fr : name;
}

function paramUnit(name) {
    return PARAM_META[name]?.unit || '';
}

function paramHelp(name) {
    const meta = PARAM_META[name];
    return meta ? meta.help[Prefs.get('lang')] || meta.help.fr : '';
}

/** « overrides.kribi.alert_pertes_threshold » → « kribi : Seuil de pertes » ; un nom simple → son libellé. */
function describeParam(name) {
    const match = /^overrides\.([^.]+)\.(.+)$/.exec(name);
    return match ? `${match[1]} : ${paramLabel(match[2])}` : paramLabel(name);
}
