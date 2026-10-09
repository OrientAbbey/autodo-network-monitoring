// AUTODO — Initialisation, exposition globale, actions (CSP)
// Script classique (partage la portée globale avec les autres fichiers de static/js/, chargés dans l'ordre de dashboard.html).

// ====== INITIALISATION ======
document.addEventListener('DOMContentLoaded', () => {
    if (navigator.credentials?.preventSilentAccess) {
        navigator.credentials.preventSilentAccess();
    }

    // Initialize theme
    initTheme();

    // Initialize audio context (ready for alerts)
    initAudioContext();

    requestAnimationFrame(() => {
        TabManager.initializeTabs();
        AutoUpdateManager.initializeAutoUpdate();
        UIUtils.insertHelpIcon();
        ConfigPanel.init();
        GridManager.init();

        // Initialize filter sidebar event listeners
        const filterBtn = document.getElementById('filterBtn');
        const closeSidebarBtn = document.querySelector('.close-sidebar-btn');
        const filterOverlay = document.querySelector('.filter-sidebar-overlay');
        const applyFiltersBtn = document.getElementById('applyFiltersBtn');
        const resetFiltersBtn = document.getElementById('resetFiltersBtn');

        if (filterBtn) {
            filterBtn.addEventListener('click', function () {
                toggleFilterSidebar();
            });
        }

        if (closeSidebarBtn) {
            closeSidebarBtn.addEventListener('click', function () {
                toggleFilterSidebar();
            });
        }

        if (filterOverlay) {
            filterOverlay.addEventListener('click', function () {
                toggleFilterSidebar();
            });
        }

        if (applyFiltersBtn) {
            applyFiltersBtn.addEventListener('click', function () {
                applyFilters();
            });
        }

        if (resetFiltersBtn) {
            resetFiltersBtn.addEventListener('click', function () {
                resetFilters();
            });
        }

        const filterInputs = document.querySelectorAll('#filterSidebar input[type="radio"], #filterSidebar input[type="checkbox"]');
        filterInputs.forEach(input => {
            input.addEventListener('change', (e) => {
                try {
                    // Handle type filter logic
                    if (input.name === 'typeFilter') {
                        handleTypeFilterChange(input);
                    }

                    // Show indicator that filters have been changed
                    showFilterStatus();
                } catch (error) {
                    console.error('Error in filter change handler:', error);
                }
            });
        });

        refreshDashboardData();
    });

    window.addEventListener('error', (event) => {
        console.error('[Global] Erreur:', event.error);
        Toasts.show(`Erreur: ${event.error?.message || 'Erreur inconnue'}`, { type: 'error' });
    }, { passive: true });
});

// ====== EXPOSITION GLOBALE ======
Object.assign(window, {
    refreshPage: refreshDashboardData,
    toggleSection: SectionManager.toggleSection,
    showTab: TabManager.showTab,
    toggleAutoUpdate: AutoUpdateManager.toggleAutoUpdate.bind(AutoUpdateManager),
    toggleTheme: toggleTheme,
    acknowledgeAlert: AlertManager.closeAlertPopup,
    AlertManager,
    lastDashboardData: null,
    handleTypeFilterChange: handleTypeFilterChange
});

// Intercepter updateDashboardDOM pour sauvegarder les données
const origUpdateDashboardDOM = updateDashboardDOM;
updateDashboardDOM = function (data) {
    window.lastDashboardData = data;
    origUpdateDashboardDOM(data);
};

// ====== STYLES POUR CURSEUR ======
(() => {
    const style = document.createElement('style');
    style.innerHTML = `.clickable-row, .clickable-cell { cursor: pointer !important; }`;
    document.head.appendChild(style);
})();

// ====== ACTIONS (remplacent les attributs onclick= inline, incompatibles avec la CSP) ======
const ACTIONS = {
    acknowledgeAlert: () => acknowledgeAlert(),
    toggleAutoUpdate: () => toggleAutoUpdate(),
    toggleTheme: () => toggleTheme(),
    playAlertSound: () => playAlertSound(),
    stopAlertSound: () => stopAlertSound(),
    clearDismissedAlerts: () => AlertManager.clearDismissedAlerts(),
    toggleSection: (el) => toggleSection(el),
    showTab: (el, e) => showTab(el.dataset.tab, el.dataset.interval, e),
    dismissAlerte: (el) => AlertManager.dismissAlerte(el.dataset.id),
};
document.addEventListener('click', (e) => {
    const el = e.target.closest('[data-action]');
    const fn = el && ACTIONS[el.dataset.action];
    if (fn) fn(el, e);
});
document.addEventListener('submit', (e) => { if (e.target.hasAttribute('data-no-submit')) e.preventDefault(); });
// Échap acquitte la pop-up d'alerte (accessibilité clavier)
document.addEventListener('keydown', (e) => {
    const pop = document.getElementById('alert-popup');
    if (e.key === 'Escape' && pop && getComputedStyle(pop).display !== 'none') acknowledgeAlert();
});
