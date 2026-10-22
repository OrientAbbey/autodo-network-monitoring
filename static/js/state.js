// AUTODO — État global et accès DOM
// Script classique (partage la portée globale avec les autres fichiers de static/js/, chargés dans l'ordre de dashboard.html).

// ====== ÉTAT GLOBAL ======
const STATE = {
    autoUpdateInterval: null,
    nextUpdateTimeout: null,
    countdownInterval: null,
    isAutoUpdateEnabled: true,
    previousAlertes: [],
    currentAlertes: [],
    sections: ['international', 'gi', 'international_gi', 'roaming', 'tdd'],
    refreshInProgress: false,
    isFirstLoad: true,
    shownIds: new Set(),        // alertes déjà présentées dans la pop-up pendant cette session de page
    popupIds: []                // alertes actuellement listées dans la pop-up
};

// ====== ACCÈS DOM ======
const DOM = {
    get alertesBadge() { return document.getElementById('alertes-badge'); },
    get alertesBadgeOpen() { return document.getElementById('alertes-badge-open'); },
    get alertPopup() { return document.getElementById('alert-popup'); },
    get alertPopupMessage() { return document.getElementById('alert-popup-message'); },
    get controls() { return document.querySelector('.controls'); },
    get refreshBtn() { return document.getElementById('refreshBtn'); },
    get autoStatus() { return document.getElementById('autoStatus'); },
    get autoUpdateBtn() { return document.getElementById('autoUpdateBtn'); },
    get lastUpdateValue() { return document.querySelector('.current-time .last-update-value'); },
    get nextLabel() { return document.querySelector('.current-time .next-label'); },
    get countdown() { return document.querySelector('.current-time .countdown'); },
    get tableWrapperInternational() { return document.getElementById('table-wrapper-international'); },
    get tableWrapperGi() { return document.getElementById('table-wrapper-gi'); },
    get tableWrapperInternationalGi() { return document.getElementById('table-wrapper-international_gi'); },
    get tableWrapperTdd() { return document.getElementById('table-wrapper-tdd'); },
    get helpDialog() { return document.getElementById('help-dialog'); },
    get helpIcon() { return document.getElementById('helpIcon'); },
    get sectionsGrid() { return document.getElementById('sections-grid'); },
    getTableWrapperRoaming: (interval) => document.getElementById(`table-wrapper-roaming-${interval}`),
    createCloseBtn() {
        const btn = document.createElement('button');
        btn.innerHTML = '✕';
        btn.className = 'alertes-badge-close';
        return btn;
    }
};
