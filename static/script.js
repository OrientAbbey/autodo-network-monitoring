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
    theme: localStorage.getItem('theme') || 'dark',
    isFirstLoad: true,

    // ====== NOTIFICATION SOUND ======
    soundOscillators: [],
    soundInterval: null,
    audioContext: null
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


// ====== PANNEAU DE CONFIGURATION ======
const ConfigPanel = {
    init() {
        const openBtn = document.getElementById('openConfigBtn');
        const drawer = document.getElementById('config-drawer');
        const closeBtn = document.getElementById('close-drawer');
        const form = document.getElementById('config-form');

        if (!drawer || !form) return;

        const openDrawer = async () => {
            // Check token validity first
            try {
                const response = await fetch('/api/auth', {
                    method: 'GET',
                    credentials: 'same-origin'
                });

                if (response.ok) {
                    // Token is valid, open config drawer directly
                    this.loadConfig();
                    document.getElementById('config-drawer').style.display = 'block';
                    return;
                }
            } catch (error) {
                console.log('Token verification failed, showing password prompt');
            }

            // Token invalid or expired, show password prompt
            this.showPasswordPrompt();
        };
        const closeDrawer = () => { drawer.style.display = 'none'; };

        openBtn?.addEventListener('click', openDrawer);
        closeBtn?.addEventListener('click', closeDrawer);

        document.addEventListener('mousedown', (e) => {
            if (drawer?.style.display === 'none') return;
            const isClickInside = drawer.contains(e.target);
            const isOpenButton = openBtn && (e.target === openBtn || openBtn.contains(e.target));
            if (!isClickInside && !isOpenButton) closeDrawer();
        });

        // Réagir immédiatement aux changements des cases "Afficher alertes"
        const attachVisibilityListeners = () => {
            const ids = ['alerts_enable_international', 'alerts_enable_gi', 'alerts_enable_international_gi', 'alerts_enable_roaming', 'alerts_enable_tdd'];
            ids.forEach(id => {
                const el = document.getElementById(id);
                if (el) {
                    el.addEventListener('change', () => SectionVisibility.applyFromCheckboxes());
                }
            });
        };

        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            const getVal = (id) => {
                const el = document.getElementById(id);
                return el ? el.value : '';
            };
            const getChecked = (id, def = true) => {
                const el = document.getElementById(id);
                return el ? !!el.checked : def;
            };
            const payload = {
                alert_traffic_threshold_percent: parseFloat(getVal('alert_traffic_threshold_percent') || '0'),
                alert_pertes_threshold: parseFloat(getVal('alert_pertes_threshold') || '0'),
                traffic_up_threshold_percent: parseFloat(getVal('traffic_up_threshold_percent') || '0'),
                traffic_consecutive_drops_count: parseInt(getVal('traffic_consecutive_drops_count') || '3'),
                traffic_consecutive_drops_min_percent: parseFloat(getVal('traffic_consecutive_drops_min_percent') || '0'),
                alert_persistence_duration_minutes: parseInt(getVal('alert_persistence_duration_minutes') || '0'),
                pertes_consecutive_count: parseInt(getVal('pertes_consecutive_count') || '1'),
                traffic_recovery_ratio: parseFloat(getVal('traffic_recovery_ratio') || '0'),
                mode7days_recovery_ratio: parseFloat(getVal('mode7days_recovery_ratio') || '0'),
                db_recovery_threshold_minutes: parseInt(getVal('db_recovery_threshold_minutes') || '0'),
                // Nouveaux paramètres TCP
                alert_tcp_no_response_threshold: parseFloat(getVal('alert_tcp_no_response_threshold') || '100'),
                alert_tcp_block_wp_up_threshold: parseFloat(getVal('alert_tcp_block_wp_up_threshold') || '50'),
                alert_latence_threshold_ms: parseFloat(getVal('alert_latence_threshold_ms') || '200'),
                // Préférences d'affichage des alertes
                alerts_enable_international: getChecked('alerts_enable_international', true),
                alerts_enable_gi: getChecked('alerts_enable_gi', true),
                alerts_enable_international_gi: getChecked('alerts_enable_international_gi', false),
                alerts_enable_roaming: getChecked('alerts_enable_roaming', false),
                alerts_enable_tdd: getChecked('alerts_enable_tdd', false),
            };

            if (await this.saveConfig(payload)) closeDrawer();
        });

        this.loadConfig();
        attachVisibilityListeners();
    },


    showPasswordPrompt() {
        // Create password dialog
        const dialog = document.createElement('div');
        dialog.id = 'password-dialog';
        dialog.style.cssText = `position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.8);display:flex;justify-content:center;align-items:center;z-index:10000;`;

        dialog.innerHTML = `
            <div style="background:var(--dark-secondary);border:2px solid var(--orange-primary);border-radius:8px;padding:30px;min-width:300px;text-align:center;color:var(--white);">
                <h3 style="margin-bottom:20px;color:var(--orange-primary);">🔐 Accès aux Paramètres</h3>
                <p style="margin-bottom:20px;">Entrez le mot de passe pour accéder aux paramètres d'alertes:</p>
                <form id="password-form" style="display:flex;flex-direction:column;">
                    <input type="password" id="config-password" autocomplete="off" placeholder="Mot de passe" style="width:100%;padding:10px;margin-bottom:20px;border:1px solid var(--dark-light);border-radius:4px;background:var(--dark-light);color:var(--white);font-size:14px;">
                <div>
                    <button type="button" id="password-cancel" style="background:var(--gray-medium);color:white;border:none;padding:10px 20px;margin-right:10px;border-radius:4px;cursor:pointer;">Annuler</button>
                    <!-- mettre type="submit" pour que Enter déclenche bien le submit du form -->
                    <button type="submit" id="password-submit" style="background:var(--orange-primary);color:white;border:none;padding:10px 20px;border-radius:4px;cursor:pointer;">Valider</button>
                </div>
                </form>
            </div>`;

        document.body.appendChild(dialog);

        const passwordInput = document.getElementById('config-password');
        const submitBtn = document.getElementById('password-submit');
        const cancelBtn = document.getElementById('password-cancel');
        const form = document.getElementById('password-form');

        const closeDialog = () => document.body.removeChild(dialog);

        const checkPassword = async () => {
            try {
                const r = await fetch("/api/auth", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ password: passwordInput.value })
                });
                if (!r.ok) throw 0;
                closeDialog();
                // Now open the config drawer
                this.loadConfig();
                document.getElementById('config-drawer').style.display = 'block';
            } catch {
                passwordInput.style.borderColor = '#ff3333';
                passwordInput.value = '';
                passwordInput.placeholder = 'Mot de passe incorrect';
                setTimeout(() => {
                    passwordInput.style.borderColor = 'var(--dark-light)';
                    passwordInput.placeholder = 'Mot de passe';
                }, 2000);
            }
        };

        // Submit via click (button) still works
        submitBtn.addEventListener('click', (e) => {
            e.preventDefault();
            checkPassword();
        });

        cancelBtn.addEventListener('click', closeDialog);

        // Ecouter le `submit` du formulaire pour gérer Enter proprement
        form.addEventListener('submit', (e) => {
            e.preventDefault(); // important pour empêcher un reload
            checkPassword();
        });

        // Focus the password input
        passwordInput.focus();
    },


    async loadConfig() {
        try {
            const res = await fetch('/api/config');
            if (!res.ok) return;
            const payload = await res.json();
            const cfg = payload.data || payload;

            [
                'alert_traffic_threshold_percent',
                'alert_pertes_threshold',
                'traffic_up_threshold_percent',
                'traffic_consecutive_drops_count',
                'traffic_consecutive_drops_min_percent',
                'alert_persistence_duration_minutes',
                'pertes_consecutive_count',
                'traffic_recovery_ratio',
                'mode7days_recovery_ratio',
                'db_recovery_threshold_minutes',
                'alert_tcp_no_response_threshold',
                'alert_tcp_block_wp_up_threshold',
                'alert_latence_threshold_ms'
            ].forEach(k => {
                const el = document.getElementById(k);
                if (el && cfg[k] != null) el.value = cfg[k];
            });

            // Charger les cases à cocher "Afficher alertes"
            const setChecked = (id, val) => {
                const el = document.getElementById(id);
                if (el) el.checked = !!val;
            };
            setChecked('alerts_enable_international', cfg.alerts_enable_international ?? true);
            setChecked('alerts_enable_gi', cfg.alerts_enable_gi ?? true);
            setChecked('alerts_enable_international_gi', cfg.alerts_enable_international_gi ?? false);
            setChecked('alerts_enable_roaming', cfg.alerts_enable_roaming ?? false);
            setChecked('alerts_enable_tdd', cfg.alerts_enable_tdd ?? false);

            // Appliquer la visibilité des sections selon la config
            SectionVisibility.applyFromConfig(cfg);
        } catch (e) {
            console.error('Erreur chargement config', e);
        }
    },

    async saveConfig(payload) {
        try {
            const res = await fetch('/update_config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            if (!res.ok) throw new Error('Échec sauvegarde');

            Toasts.show('Configuration sauvegardée avec succès', { type: 'success' });
            // Appliquer immédiatement la visibilité après sauvegarde
            SectionVisibility.applyFromConfig(payload);
            return true;
        } catch (e) {
            console.error('Erreur sauvegarde config', e);
            Toasts.show('Erreur lors de la sauvegarde de la configuration', { type: 'error' });
            return false;
        }
    }
};


// ====== GESTIONNAIRE DES ALERTES ======
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const AlertManager = {
    updateBadge(allAlertes) {
        document.querySelectorAll('#alertes-badge-open, #alertes-badge').forEach(e => e.remove());

        const openAlertes = allAlertes.filter(a => a.status === 'open');
        const totalOpenAlertes = openAlertes.length;
        const badgeColor = totalOpenAlertes > 0 ? 'var(--danger)' : 'var(--success)';

        let badge = document.createElement('div');
        badge.id = 'alertes-badge';
        badge.className = 'alertes-badge';
        badge.style.background = badgeColor;
        badge.style.display = 'none';

        let openBtn = document.createElement('div');
        openBtn.id = 'alertes-badge-open';
        openBtn.className = 'alertes-badge-open';
        openBtn.innerHTML = '🔔';
        openBtn.title = 'Afficher le badge alertes';
        openBtn.style.background = badgeColor;
        openBtn.style.display = 'flex';

        badge.innerHTML = `<span>🚨 ${openAlertes.filter(a => a.type === 'TRAFFIC').length} traffic</span> <span style='margin-left:10px;'>⚠️ ${openAlertes.filter(a => a.type === 'PERTES').length} pertes</span>`;

        const closeBtn = DOM.createCloseBtn();
        closeBtn.onclick = (e) => {
            badge.style.display = 'none';
            openBtn.style.display = 'flex';
            e.stopPropagation();
        };
        badge.appendChild(closeBtn);

        openBtn.onclick = () => {
            badge.style.display = 'flex';
            openBtn.style.display = 'none';
        };

        // Badge count on bell
        const badgeCount = document.createElement('span');
        badgeCount.className = 'badge-count';
        badgeCount.textContent = totalOpenAlertes;
        badgeCount.style.display = totalOpenAlertes > 0 ? 'block' : 'none';
        badgeCount.style.background = badgeColor;
        openBtn.appendChild(badgeCount);

        // === Insertion dans l'entête (zone .controls) au lieu du body ===
        const controls = DOM.controls;
        if (controls) {
            controls.appendChild(badge);
            controls.appendChild(openBtn);
        } else {
            // Fallback au body si .controls introuvable
            document.body.appendChild(badge);
            document.body.appendChild(openBtn);
        }
    },

    checkNewAlertes(allAlertes) {

        const openAlertes = allAlertes.filter(a => a.status === 'open');
        STATE.currentAlertes = openAlertes;

        // On first load, play sound for all open alerts
        if (STATE.isFirstLoad) {
            if (openAlertes.length > 0) {
                // Show popup for the first open alert (sound will start automatically)
                this.showAlertPopup(openAlertes[0]);
                // this.showAlertToast(openAlertes[0]);
            }
            STATE.isFirstLoad = false;
        } else {
            // On subsequent loads, only check for truly new alerts
            const newAlertes = openAlertes.filter(alerte =>
                !STATE.previousAlertes.some(prev => prev.id === alerte.id)
            );
            // Show popup for new alerts
            if (newAlertes.length > 0) {
                // Show popup for the first new alert (sound will start automatically)
                this.showAlertPopup(newAlertes[0]);
            }
            // newAlertes.forEach(alerte => this.showAlertToast(alerte));
        }

        this.updateSectionAlertes(allAlertes);
        STATE.previousAlertes = [...openAlertes];
    },

    showAlertToast(alerte) {
        // Affiche uniquement le titre/message, un seul toast d'alerte à la fois
        const container = Toasts.ensureContainer();
        if (!container) return;
        // Supprime tout toast d'alerte déjà affiché
        container.querySelectorAll('.toast.alert').forEach(t => t.remove());
        const toast = document.createElement('div');
        toast.className = 'toast alert';
        toast.innerHTML = `
            <div style="margin-bottom: 10px; font-weight:bold;">${alerte.title || alerte.message}</div>
            <button class="toast-ok-btn" style="margin-top:4px;padding:4px 16px;font-size:1em;cursor:pointer;">OK</button>
        `;
        container.appendChild(toast);
        requestAnimationFrame(() => {
            toast.classList.add('visible');
        });

        toast.querySelector('.toast-ok-btn').addEventListener('click', () => {
            Toasts.close(toast);
            stopAlertSound(); // Arrête le son quand le toast est fermé
        });

        // Play alert sound when toast is shown
        playAlertSound();
    },

    showAlertPopup(alerte) {
        const popup = DOM.alertPopup;
        const message = DOM.alertPopupMessage;

        if (popup && message) {
            message.textContent = alerte.title || alerte.message || '';
            popup.style.display = 'flex';

            // Start the looping alert sound
            playAlertSound();
        }
    },

    closeAlertPopup() {
        DOM.alertPopup?.style.setProperty('display', 'none');
        stopAlertSound(); // Stop the looping sound when popup is closed
    },

    updateSectionAlertes(alertesData) {

        // Normalize input: accept either an array of alerts OR an object { traffic: [], pertes: [] }
        let allAlertes = [];
        if (Array.isArray(alertesData)) {
            allAlertes = alertesData;
        } else if (alertesData && (Array.isArray(alertesData.traffic) || Array.isArray(alertesData.pertes))) {
            allAlertes = [...(alertesData.traffic || []), ...(alertesData.pertes || [])];
        } else {
            allAlertes = [];
        }

        STATE.sections.forEach(section => {
            const alertesDiv = document.getElementById(`alertes-list-${section}`);
            if (!alertesDiv) return;

            const sectionAlertes = allAlertes.filter(alerte => {
                const location = (alerte.location || '').toString();
                const message = (alerte.title || alerte.message || '').toString();

                const locLower = location.toLowerCase();
                const msgLower = message.toLowerCase();

                switch (section) {
                    case 'international':
                        return locLower.includes('sortie internationale') || msgLower.includes('sortie internationale');
                    case 'gi':
                        return (locLower.includes('traffic utilisateur') || locLower.includes('gi') || msgLower.includes('traffic utilisateur') || msgLower.includes('gi')) &&
                            !locLower.includes('roaming') && !msgLower.includes('roaming');
                    case 'roaming':
                        return locLower.includes('roaming') || msgLower.includes('roaming');
                    case 'tdd':
                        return locLower.includes('tdd') || msgLower.includes('tdd');
                    default:
                        return false;
                }
            });

            const openAlertes = sectionAlertes.filter(a => a.status === 'open');
            const closedAlertes = sectionAlertes.filter(a => a.status === 'closed');

            // Check current filter settings from sidebar
            const statusFilter = document.querySelector('input[name="statusFilter"]:checked')?.value || 'all';
            const periodFilter = document.querySelector('input[name="periodFilter"]:checked')?.value || 'all';

            // Get type filters
            const allTypeChecked = document.querySelector('input[name="typeFilter"][value="all"]')?.checked;
            const trafficChecked = document.querySelector('input[name="typeFilter"][value="traffic"]')?.checked;
            const pertesChecked = document.querySelector('input[name="typeFilter"][value="pertes"]')?.checked;

            // Determine which types to show
            let showTraffic = allTypeChecked || trafficChecked;
            let showPertes = allTypeChecked || pertesChecked;

            // Filter by type first
            let typeFilteredAlertes = [...openAlertes, ...closedAlertes];
            if (!allTypeChecked) {
                typeFilteredAlertes = typeFilteredAlertes.filter(alert => {
                    const alertType = alert.type?.toLowerCase();
                    return (showTraffic && alertType === 'traffic') ||
                        (showPertes && alertType === 'pertes');
                });
            }

            // Filter by time period
            let timeFilteredAlertes = typeFilteredAlertes;
            if (periodFilter !== 'all') {
                const now = new Date();
                const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());

                timeFilteredAlertes = typeFilteredAlertes.filter(alert => {
                    try {
                        const alertDate = new Date((alert.creation_time || alert.date).replace(' ', 'T')); // Convert to proper ISO format

                        switch (periodFilter) {
                            case 'today':
                                return alertDate >= today;
                            case 'week':
                                const weekAgo = new Date(today);
                                weekAgo.setDate(weekAgo.getDate() - 7);
                                return alertDate >= weekAgo;
                            case 'month':
                                const monthAgo = new Date(today);
                                monthAgo.setMonth(monthAgo.getMonth() - 1);
                                return alertDate >= monthAgo;
                            default:
                                return true;
                        }
                    } catch (e) {
                        return true; // Include alerts with invalid dates
                    }
                });
            }

            // Separate filtered alerts by status
            const filteredOpenAlertes = timeFilteredAlertes.filter(a => a.status === 'open');
            const filteredClosedAlertes = timeFilteredAlertes.filter(a => a.status === 'closed');

            // Update counter - show counts based on filters
            const alertesCount = document.getElementById(`alertes-count-${section}`);
            if (alertesCount) {
                let countText = '';
                if (filteredClosedAlertes.length > 0 && filteredOpenAlertes.length > 0) {
                    countText = `(<span class="badge-closed">${filteredClosedAlertes.length} fermées</span>, <span class="badge-open">${filteredOpenAlertes.length} ouvertes</span>)`;
                } else if (filteredClosedAlertes.length > 0) {
                    countText = `(<span class="badge-closed">${filteredClosedAlertes.length} fermées</span>)`;
                } else if (filteredOpenAlertes.length > 0) {
                    countText = `(<span class="badge-open">${filteredOpenAlertes.length} ouvertes</span>)`;
                }
                alertesCount.innerHTML = countText;
            }

            // Display alerts based on status filter
            let alertesToShow = [];
            switch (statusFilter) {
                case 'open':
                    alertesToShow = filteredOpenAlertes;
                    break;
                case 'closed':
                    alertesToShow = filteredClosedAlertes;
                    break;
                case 'all':
                default:
                    alertesToShow = [...filteredOpenAlertes, ...filteredClosedAlertes];
                    break;
            }

            let html = '';

            if (alertesToShow.length > 0) {
                html = alertesToShow.map(a => {
                    const isOpen = a.status === 'open';
                    const titreMessage = (a.title ? `<b>${esc(a.title)}</b>` : esc(a.message || ''));
                    return `<div class="alert" data-alerte-id="${esc(a.id)}" style="display:flex;justify-content:space-between;align-items:center;gap:10px;${!isOpen ? 'opacity:0.7;' : ''}">
                        <span>
                            ${titreMessage}
                            <span style="margin-left:8px;padding:2px 8px;border-radius:8px;font-size:0.95em;font-weight:bold;vertical-align:middle;background:${isOpen ? 'var(--danger)' : 'var(--success)'};color:white;">
                                ${isOpen ? 'OPEN' : 'CLOSED'}
                            </span><br>
                            Valeurs: ${(a.valeurs || []).map(v => (v == null ? '—' : Number(v).toFixed(3))).join(' → ')}<br>Date: ${esc(a.creation_time || a.date || 'N/A')}
                        </span>
                        ${isOpen ? `<button class="btn-secondary" style="font-size:0.8em;padding:2px 8px;" data-action="dismissAlerte" data-id="${esc(a.id)}" aria-label="Masquer l'alerte">✕</button>` : ''}
                    </div>`;
                }).join('');
            }
            else {
                // Show appropriate message based on filters
                const hasTimeFilter = periodFilter !== 'all';
                const hasTypeFilter = !allTypeChecked;
                const hasStatusFilter = statusFilter !== 'all';

                if (hasTimeFilter || hasTypeFilter || hasStatusFilter) {
                    html = '<div class="no-data">Aucune alerte trouvée pour les filtres sélectionnés</div>';
                } else if (statusFilter === 'open') {
                    if (closedAlertes.length > 0) {
                        html = '<div class="no-data">Aucune alerte ouverte. <i>(Utilisez les filtres pour voir les alertes fermées)</i></div>';
                    } else {
                        html = '<div class="no-data">Aucune alerte détectée pour cette section</div>';
                    }
                } else {
                    html = '<div class="no-data">Aucune alerte détectée pour cette section</div>';
                }
            }

            alertesDiv.innerHTML = html;
        });
    },

    // Fermer une alerte de section
    async dismissAlerte(alerteId) {
        try {
            // Enregistrer l'alerte comme ignorée dans le localStorage
            this.saveDismissedAlerte(alerteId);

            // Retirer immédiatement l'alerte fermée du DOM
            document.querySelectorAll(`[data-alerte-id='${alerteId}']`).forEach(el => el.remove());
            // Mettre à jour l'état local
            STATE.previousAlertes = STATE.previousAlertes.filter(a => a.id !== alerteId);
            STATE.currentAlertes = STATE.currentAlertes.filter(a => a.id !== alerteId);
            // Recalcul minimal: si une liste devient vide, afficher le placeholder
            STATE.sections.forEach(section => {
                const container = document.getElementById(`alertes-list-${section}`);
                if (container && container.children.length === 0) {
                    container.innerHTML = '<div class="no-data">Aucune alerte détectée pour cette section</div>';
                }
            });

            // Mettre à jour l'interface
            refreshDashboardData();

            Toasts.show(`Alerte ${alerteId} supprimée`, { type: 'success', durationMs: 1000 });

        } catch (error) {
            console.error('Erreur lors de la suppression de l\'alerte:', error);
            Toasts.show(`Erreur lors de la suppression de l'alerte ${alerteId}`, { type: 'error' });
        }
    },

    saveDismissedAlerte(alerteId) {
        let dismissedAlertes = JSON.parse(localStorage.getItem('dismissedAlertes') || '[]');
        if (!dismissedAlertes.includes(alerteId)) {
            dismissedAlertes.push(alerteId);
            localStorage.setItem('dismissedAlertes', JSON.stringify(dismissedAlertes));
        }
    },

    clearDismissedAlerts() {
        if (confirm("Êtes-vous sûr de vouloir effacer toutes les alertes masquées ?")) {
            localStorage.removeItem('dismissedAlertes');
            refreshDashboardData();
        }
    },

    getDismissedAlertes() {
        return JSON.parse(localStorage.getItem('dismissedAlertes') || '[]');
    },

    // Alias for updateSectionAlertes to maintain compatibility
    displayAlertes(alertesData) {
        this.updateSectionAlertes(alertesData);
    }
};


// ====== GESTIONNAIRE DES TABS ======
const TabManager = {
    initializeTabs() {
        document.querySelectorAll('.tab-container').forEach(container => {
            const firstTab = container.querySelector('.tab-btn');
            const firstContent = container.querySelector('.tab-content');
            if (firstTab && firstContent) {
                firstTab.classList.add('active');
                firstContent.classList.add('active');
            }
        });
    },

    showTab(section, interval, event) {
        const btn = event?.target?.closest('.tab-btn');
        const tabContainer = btn?.closest('.tab-container') || document.querySelector('.tab-container');
        if (!tabContainer) return;

        const tabButtons = tabContainer.querySelectorAll('.tab-btn');
        const tabContents = tabContainer.querySelectorAll('.tab-content');

        tabButtons.forEach(btn => btn.classList.remove('active'));
        tabContents.forEach(content => content.classList.remove('active'));

        const intervalMap = { 'now': 'now', '5min': '5min', '1day': '1d', '1week': '1w' };
        const activeButton = Array.from(tabButtons).find(b =>
            b.textContent.trim().toLowerCase() === (intervalMap[interval] || '')
        );
        const activeContent = tabContainer.querySelector(`#${section === 'roaming' ? 'sections-roaming' : 'sections'}-${interval}`) ||
            tabContainer.querySelector(`#sections-${interval}`);

        if (activeButton) activeButton.classList.add('active');
        if (activeContent) {
            activeContent.classList.add('active');
            activeContent.style.cssText = 'opacity: 0; transform: translateY(10px);';
            setTimeout(() => {
                activeContent.style.cssText = 'transition: all 0.3s ease; opacity: 1; transform: translateY(0);';
            }, 50);
        }
    }
};


// ====== AUTO-UPDATE ET SSE ======
const AutoUpdateManager = {
    sse: null,
    sseErrorCount: 0,
    maxSseRetries: 10,

    initializeAutoUpdate() {
        if (STATE.isAutoUpdateEnabled) this.startAutoUpdate();
        this.updateAutoUpdateStatus();
    },

    toggleAutoUpdate() {
        STATE.isAutoUpdateEnabled = !STATE.isAutoUpdateEnabled;
        STATE.isAutoUpdateEnabled ? this.startAutoUpdate() : this.stopAutoUpdate();
        this.updateAutoUpdateStatus();
    },

    startAutoUpdate() {
        try {
            if (this.sse) {
                this.sse.close();
                this.sse = null;
            }

            this.sse = new EventSource('/events');
            this.sse.onopen = () => {
                // Reset error count on successful connection
                this.sseErrorCount = 0;
                // Use local calculation until first scheduled_update from server
                CountdownManager.scheduleNext(UIUtils.secondsToNextFiveMinute());
            };
            this.sse.onerror = (event) => {
                this.sseErrorCount++;
                console.log(`SSE connection error #${this.sseErrorCount}/${this.maxSseRetries}`);

                if (this.sseErrorCount >= this.maxSseRetries) {
                    console.error('SSE connection failed after', this.maxSseRetries, 'attempts. Stopping auto-update.');
                    this.stopAutoUpdate();
                    // Optionally show user notification
                    Toasts.show(`Connexion SSE échouée après ${this.maxSseRetries} tentatives. Mise à jour automatique désactivée.`, { type: 'error' });
                }
            };
            this.sse.onmessage = (ev) => {
                try {
                    const payload = JSON.parse(ev.data || '{}');

                    if (payload.type === 'config_updated') {
                        AlertManager.updateBadge([]);
                        AlertManager.updateSectionAlertes([]);
                        AlertManager.closeAlertPopup();
                        refreshDashboardData();
                    } else if (payload.type === 'alertes_updated' || payload.type === 'scheduled_update') {
                        refreshDashboardData();
                    } else if (payload.type === 'shutdown') {
                        this.stopAutoUpdate();
                        Toasts.show('Le serveur est en maintenance. L\'update a été arrêté.', { type: 'error' });
                        return;
                    }

                    // Use server timestamp for accurate countdown synchronization if available
                    if (payload.type === 'scheduled_update' && payload.at) {
                        CountdownManager.scheduleNext(null, payload.at);
                    } else {
                        CountdownManager.scheduleNext(UIUtils.secondsToNextFiveMinute());
                    }
                } catch (e) { /* ignore */ }
            };
        } catch (e) {
            console.error('SSE init error:', e);
            Toasts.show(`Erreur SSE: ${e?.message || e}`, { type: 'error' });
        }
    },

    stopAutoUpdate() {
        if (this.sse) {
            try { this.sse.close(); } catch (e) { }
            this.sse = null;
        }
        // Reset error count when stopping
        this.sseErrorCount = 0;
        CountdownManager.clear();
    },

    updateAutoUpdateStatus() {
        const statusElement = DOM.autoStatus;
        const button = DOM.autoUpdateBtn;

        if (statusElement) {
            statusElement.textContent = STATE.isAutoUpdateEnabled ? 'ON' : 'OFF';
            statusElement.style.color = STATE.isAutoUpdateEnabled ? 'var(--success)' : 'var(--danger)';
        }
        if (button) {
            button.style.borderColor = STATE.isAutoUpdateEnabled ? 'var(--success)' : 'var(--danger)';
        }
    }
};


// ====== COMPTE À REBOURS ======
const CountdownManager = {
    scheduleNext(secondsToNext, serverTimestamp = null) {
        this.clear();

        // If server timestamp is provided, use it for accurate synchronization
        let deadline;
        if (serverTimestamp) {
            // Parse server ISO timestamp - it already represents the next update time
            const serverTime = new Date(serverTimestamp).getTime();
            deadline = serverTime + (5 * 60 * 1000);
        } else {
            // Fallback to local calculation
            deadline = Date.now() + (secondsToNext * 1000);
        }

        STATE.countdownInterval = setInterval(() => {
            const remaining = Math.max(0, deadline - Date.now());
            this.render(Math.ceil(remaining / 1000));
            if (remaining <= 0) clearInterval(STATE.countdownInterval);
        }, 1000);

        const initialRemaining = Math.max(1, Math.ceil((deadline - Date.now()) / 1000));
        this.render(initialRemaining);
    },

    clear() {
        if (STATE.nextUpdateTimeout) {
            clearTimeout(STATE.nextUpdateTimeout);
            STATE.nextUpdateTimeout = null;
        }
        if (STATE.countdownInterval) {
            clearInterval(STATE.countdownInterval);
            STATE.countdownInterval = null;
        }
        this.render(null);
    },

    render(seconds) {
        const valueEl = DOM.countdown;
        const labelEl = DOM.nextLabel;
        if (!valueEl || !labelEl) return;

        if (seconds === null) {
            labelEl.style.display = "none";
            valueEl.textContent = "";
        } else {
            const mm = String(Math.floor(seconds / 60)).padStart(2, "0");
            const ss = String(seconds % 60).padStart(2, "0");
            labelEl.style.display = "inline";  // on affiche "Prochaine dans" (traduit automatiquement)
            valueEl.textContent = `${mm}:${ss}`;
        }
    }

};


// Notifications (toast) à droite sous le header
const Toasts = {
    container: null,
    ensureContainer() {
        if (!this.container) {
            this.container = document.getElementById('toast-container');
        }
        return this.container;
    },
    show(message, { type = 'info', durationMs = 10000 } = {}) {
        const container = this.ensureContainer();
        if (!container) { alert(message); return; }
        const toast = document.createElement('div');
        toast.className = `toast ${type}`;
        toast.innerHTML = `<span class="toast-message">${message}</span>`;
        container.appendChild(toast);
        // Animation d'entrée
        requestAnimationFrame(() => {
            toast.classList.add('visible');
        });
        // Fermeture auto
        const timer = setTimeout(() => this.close(toast), durationMs);
        // Fermeture au clic
        toast.addEventListener('click', () => { clearTimeout(timer); this.close(toast); });
    },
    close(toast) {
        if (!toast) return;
        toast.classList.remove('visible');
        setTimeout(() => toast.remove(), 300);
    }
};


// ====== GESTIONNAIRE DES TABLEAUX ======
const TableManager = {
    delegateTbodyClick(tbody, rowClass = 'clickable-row', dataAttr = 'data-link', targetTag = 'TR') {
        if (!tbody) return;

        tbody.onclick = async (e) => {
            let el = e.target;
            while (el && !el.classList.contains(rowClass)) el = el.parentElement;

            if (el && el.classList.contains(rowClass)) {
                let link = el.getAttribute('data-link-total');
                if (!link) {
                    link = el.getAttribute('data-link-pertes');
                }
                if (!link) {
                    link = el.getAttribute('data-link-tcp-service-access');
                }
                if (!link) {
                    link = el.getAttribute('data-link-tcp-tts');
                }
                if (!link) {
                    link = el.getAttribute('data-link-dns');
                }
                if (!link) {
                    link = el.getAttribute(dataAttr);
                }
                if (!link) return;

                const nowTimestamp = Math.floor(Date.now() / 1000);
                const roundedTimestamp = Math.ceil(nowTimestamp / 300) * 300;
                const finalLink = link.includes('__END_TIMESTAMP__')
                    ? link.replace('__END_TIMESTAMP__', roundedTimestamp)
                    : link;

                window.open(finalLink, '_blank', 'noopener');
            }
        };
    }
};


// ====== UTILITAIRES UI ======
const UIUtils = {
    trendColorToCss(colorCode) {
        const colors = { up: 'var(--success)', down: 'var(--danger)' };
        return colors[colorCode] || 'var(--gray-light)';
    },

    secondsToNextFiveMinute() {
        const now = new Date();
        const currentMinute = now.getMinutes();
        const base = new Date(now.getFullYear(), now.getMonth(), now.getDate(), now.getHours(), 0, 0, 0);

        let targetMinute = currentMinute % 5 >= 3
            ? Math.floor(currentMinute / 5 + 1) * 5 + 2
            : Math.floor(currentMinute / 5) * 5 + 2;

        const next = new Date(base.getTime() + targetMinute * 60 * 1000);
        if (next <= now) next.setMinutes(next.getMinutes() + 5);

        return Math.max(1, Math.ceil((next.getTime() - now.getTime()) / 1000));
    },

    insertHelpIcon() {
        const controls = DOM.controls;
        if (!controls || DOM.helpIcon) return;

        const helpIcon = document.createElement('span');
        helpIcon.id = 'helpIcon';
        helpIcon.style.cssText = 'display:inline-flex;align-items:center;justify-content:center;font-size:1.1em;cursor:pointer;margin-right:8px;';
        helpIcon.innerHTML = `
          <span style="display:inline-flex;align-items:center;justify-content:center;width:28px;height:28px;border-radius:50%;background:var(--dark-light);border:2px solid var(--orange-primary);color:var(--orange-primary);font-weight:bold;font-size:1.2em;box-shadow:0 2px 8px rgba(0,0,0,0.10);user-select:none;">?</span>
        `;

        let tooltipTimeout;

        helpIcon.addEventListener('mouseenter', (e) => {
            clearTimeout(tooltipTimeout);
            this.hideHelpTooltip();
            this.showHelpTooltip(e);
        });

        helpIcon.addEventListener('mouseleave', () => {
            tooltipTimeout = setTimeout(() => this.hideHelpTooltip(), 300);
        });

        document.addEventListener('mouseover', (e) => {
            const isTooltip = e.target.closest('#help-tooltip');
            const isHelpIcon = e.target.closest('#helpIcon');

            if (isTooltip) {
                clearTimeout(tooltipTimeout);
            } else if (!isHelpIcon) {
                this.hideHelpTooltip();
            }
        });

        const refreshBtn = DOM.refreshBtn;
        const autoUpdateBtn = DOM.autoUpdateBtn;
        if (refreshBtn) controls.insertBefore(helpIcon, refreshBtn);
        else if (autoUpdateBtn) controls.insertBefore(helpIcon, autoUpdateBtn);
        else controls.appendChild(helpIcon);
    },

    showHelpTooltip(e) {
        const helpText = `
            <div style="max-width:420px;padding:18px 12px 12px 12px;line-height:1.6;">
                <h3 style="margin-top:0;">Aide : Signification des flèches et couleurs</h3>
                <ul style="margin-left:18px;">
                    <li><b>Flèche ↑</b> : Augmentation de la valeur</li>
                    <li><b>Flèche ↓</b> : Diminution de la valeur</li>
                    <li><b>Flèche ↔</b> : Valeur stable</li>
                </ul>
                <b>Pour le <span style='color:var(--orange-primary)'>traffic</span> :</b>
                <ul style="margin-left:18px;">
                    <li><span style='color:var(--danger)'>Rouge</span> : Baisse ≥ <code>alert_traffic_threshold_percent</code> 
                        <u>ou</u> ≥ <code>traffic_consecutive_drops_count</code> baisses consécutives 
                        d'au moins <code>traffic_consecutive_drops_min_percent</code></li>
                    <li><span style='color:var(--success)'>Vert</span> : Hausse ≥ <code>traffic_up_threshold_percent</code></li>
                    <li><span style='color:var(--gray-light)'>Gris</span> : Autres cas</li>
                </ul>
                <b>Pour les <span style='color:var(--orange-primary)'>pertes</span> :</b>
                <ul style="margin-left:18px;">
                    <li><span style='color:var(--danger)'>Rouge</span> : Valeur courante &gt; <code>alert_pertes_threshold</code></li>
                    <li><span style='color:var(--success)'>Vert</span> : Valeur courante &lt; seuil <u>et</u> en diminution</li>
                    <li><span style='color:var(--gray-light)'>Gris</span> : Autres cas</li>
                </ul>
            </div>`;

        this.hideHelpTooltip();

        const tooltip = document.createElement('div');
        tooltip.id = 'help-tooltip';
        tooltip.style.cssText = `position:fixed;top:${e.clientY + 18}px;left:${e.clientX + 18}px;z-index:10010;background:var(--dark-secondary);border:2px solid var(--orange-primary);border-radius:12px;box-shadow:0 4px 24px rgba(0,0,0,0.25);color:var(--white);`;
        tooltip.innerHTML = helpText;
        tooltip.addEventListener('mouseleave', () => this.hideHelpTooltip());

        document.body.appendChild(tooltip);
    },

    hideHelpTooltip() {
        document.querySelectorAll('#help-tooltip').forEach(tooltip => tooltip.remove());
    }
};

// ====== GESTIONNAIRE DES SECTIONS ======
const SectionManager = {
    toggleSection(titleElement) {
        const section = titleElement.closest('.collapsible');
        const content = section.querySelector('.section-content');
        const icon = titleElement.querySelector('.toggle-icon');
        const isCollapsed = section.classList.contains('collapsed');

        if (isCollapsed) {
            section.classList.remove('collapsed');
            content.style.display = 'block';
            icon.textContent = '▼';
            requestAnimationFrame(() => {
                content.style.cssText = 'transition: all 0.3s ease; opacity: 1; transform: translateY(0);';
            });
        } else {
            section.classList.add('collapsed');
            icon.textContent = '▶';
            content.style.cssText = 'transition: all 0.3s ease; opacity: 0; transform: translateY(-10px);';
            setTimeout(() => content.style.display = 'none', 300);
        }
    }
};

// ====== VISIBILITÉ DES SECTIONS ======
const SectionVisibility = {
    // Applique la visibilité selon l'objet de config reçu du backend
    applyFromConfig(cfg) {
        const map = {
            international: cfg.alerts_enable_international ?? true,
            gi: cfg.alerts_enable_gi ?? true,
            international_gi: cfg.alerts_enable_international_gi ?? false,
            roaming: cfg.alerts_enable_roaming ?? false,
            tdd: cfg.alerts_enable_tdd ?? false
        };
        this.apply(map);
    },

    // Lit l'état des cases à cocher du panneau et applique
    applyFromCheckboxes() {
        const map = {
            international: !!document.getElementById('alerts_enable_international')?.checked,
            gi: !!document.getElementById('alerts_enable_gi')?.checked,
            international_gi: !!document.getElementById('alerts_enable_international_gi')?.checked,
            roaming: !!document.getElementById('alerts_enable_roaming')?.checked,
            tdd: !!document.getElementById('alerts_enable_tdd')?.checked
        };
        this.apply(map);
    },

    // Applique une map {sectionId: visibleBool}
    apply(visibilityMap) {
        STATE.sections.forEach(sectionId => {
            const visible = visibilityMap[sectionId] !== false; // défaut à true
            const sectionEl = document.getElementById(`section-${sectionId}`);
            if (!sectionEl) return;
            sectionEl.style.display = visible ? '' : 'none';

            // Gérer aussi la sous-section des alertes liée
            const alertSection = document.getElementById(`section-alertes-${sectionId}`);
            if (alertSection) {
                alertSection.style.display = visible ? '' : 'none';
            }
        });
    }
};


// ====== GRILLE ET GLISSER-DÉPOSER DES SECTIONS ======
const GridManager = {
    dragSrcEl: null,
    orderKey: 'sections_order_v1',

    init() {
        const grid = DOM.sectionsGrid;
        if (!grid) return;

        // Appliquer l'ordre canonique demandé: international, gi, international_gi, roaming, tdd
        this.applyOrder(grid, STATE.sections);

        // Supprimer tout ordre précédent persisté pour éviter la réapplication
        try { localStorage.removeItem(this.orderKey); } catch (e) { /* ignore */ }

        // Drag-and-drop désactivé
    },

    getDragAfterElement(container, y, x) {
        const draggableElements = [...container.querySelectorAll('section.data-section:not(.dragging)')];
        // Trouver l'élément après lequel insérer en se basant sur Y (et X pour grille)
        return draggableElements.reduce((closest, child) => {
            const box = child.getBoundingClientRect();
            const offset = y - (box.top + box.height / 2);
            if (offset < 0 && offset > closest.offset) {
                return { offset, element: child };
            } else {
                return closest;
            }
        }, { offset: Number.NEGATIVE_INFINITY }).element;
    },

    saveCurrentOrder(grid) {
        const order = [...grid.querySelectorAll('section.data-section')].map(s => s.dataset.sectionId);
        try { localStorage.setItem(this.orderKey, JSON.stringify(order)); } catch (e) { /* ignore */ }
    },

    restoreOrder(grid) {
        let order = null;
        try { order = JSON.parse(localStorage.getItem(this.orderKey) || 'null'); } catch (e) { order = null; }
        if (!order || !Array.isArray(order) || order.length === 0) return;

        const map = Object.fromEntries([...grid.children].map(c => [c.dataset.sectionId, c]));
        order.forEach(id => { if (map[id]) grid.appendChild(map[id]); });
    },

    applyOrder(grid, orderArray) {
        const childrenMap = Object.fromEntries([...grid.children].map(c => [c.dataset.sectionId, c]));
        orderArray.forEach(id => { if (childrenMap[id]) grid.appendChild(childrenMap[id]); });
    }
};


// ====== THEME MANAGEMENT ======
function initTheme() {
    document.documentElement.setAttribute('data-theme', STATE.theme);
    updateThemeButton();
}

function toggleTheme() {
    STATE.theme = STATE.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', STATE.theme);
    localStorage.setItem('theme', STATE.theme);
    updateThemeButton();
}

function updateThemeButton() {
    const btn = document.getElementById('themeToggleBtn');
    if (btn) {
        btn.textContent = STATE.theme === 'dark' ? '☀️' : '🌙';
        btn.title = STATE.theme === 'dark' ? 'Switch to Light Mode' : 'Switch to Dark Mode';
    }
}

// Track filter state
let filtersChanged = false;

function showFilterStatus() {
    const statusDiv = document.getElementById('filterStatus');
    if (statusDiv) {
        statusDiv.style.display = 'block';
    }
    filtersChanged = true;
}

function hideFilterStatus() {
    const statusDiv = document.getElementById('filterStatus');
    if (statusDiv) {
        statusDiv.style.display = 'none';
    }
    filtersChanged = false;
}

function toggleFilterSidebar() {
    const sidebar = document.getElementById('filterSidebar');
    const overlay = document.querySelector('.filter-sidebar-overlay');

    if (sidebar) {
        sidebar.classList.toggle('active');
    } else {
        console.error('Sidebar element not found!');
    }

    if (overlay) {
        overlay.classList.toggle('active');
    } else {
        console.error('Overlay element not found!');
    }
}

function applyFilters() {
    try {
        // Refresh dashboard data when filters are applied
        refreshDashboardData();
        // Hide the filter status indicator after applying
        hideFilterStatus();
    } catch (error) {
        console.error('Error in applyFilters:', error);
    }
}

function resetFilters() {
    try {
        // Reset all filter controls to default
        const openStatusFilter = document.querySelector('input[name="statusFilter"][value="open"]');
        const allTypeFilter = document.querySelector('input[name="typeFilter"][value="all"]');
        const monthPeriodFilter = document.querySelector('input[name="periodFilter"][value="month"]');

        if (openStatusFilter) openStatusFilter.checked = true;
        if (allTypeFilter) allTypeFilter.checked = true;
        if (monthPeriodFilter) monthPeriodFilter.checked = true;

        // Uncheck other type filters
        document.querySelectorAll('input[name="typeFilter"]:not([value="all"])').forEach(input => {
            input.checked = false;
        });

        // Show indicator that filters have been changed and need to be applied
        showFilterStatus();
    } catch (error) {
        console.error('Error in resetFilters:', error);
    }
}

// Handle type filter logic (exclusive with "all")
function handleTypeFilterChange(input) {
    try {
        const allTypeFilter = document.querySelector('input[name="typeFilter"][value="all"]');
        const otherTypeFilters = document.querySelectorAll('input[name="typeFilter"]:not([value="all"])');

        if (!allTypeFilter || !otherTypeFilters) {
            console.warn('Type filter elements not found');
            return;
        }

        if (input.value === 'all') {
            if (input.checked) {
                // If "all" is checked, uncheck others
                otherTypeFilters.forEach(filter => filter.checked = false);
            }
        } else {
            if (input.checked) {
                // If any specific type is checked, uncheck "all"
                allTypeFilter.checked = false;
            } else {
                // If no specific types are checked, check "all"
                const anyChecked = Array.from(otherTypeFilters).some(filter => filter.checked);
                if (!anyChecked) {
                    allTypeFilter.checked = true;
                }
            }
        }
    } catch (error) {
        console.error('Error in handleTypeFilterChange:', error);
    }
}

// Initialize Web Audio API context (called once at app start)
function initAudioContext() {
    if (STATE.audioContext) return;
    try {
        STATE.audioContext = new (window.AudioContext || window.webkitAudioContext)();
        if (STATE.audioContext.state === 'suspended') {
            STATE.audioContext.resume();
        }
    } catch (error) {
        console.error('Audio context initialization failed:', error);
    }
}

// Play alert sound with looping bell tones
function playAlertSound() {
    stopAlertSound(); // Stop any existing sound first
    initAudioContext();
    
    if (!STATE.audioContext) return;
    
    try {
        // Play first bell immediately
        playBellTone();
        
        // Schedule repeats every 1 second
        STATE.soundInterval = setInterval(playBellTone, 1000);
    } catch (error) {
        console.warn('Alert sound failed:', error);
    }
}

// Stop all alert sounds
function stopAlertSound() {
    // Stop interval
    if (STATE.soundInterval) {
        clearInterval(STATE.soundInterval);
        STATE.soundInterval = null;
    }
    
    // Stop all active oscillators
    STATE.soundOscillators.forEach(osc => {
        try { osc.stop(); } catch (e) {}
    });
    STATE.soundOscillators = [];
}

// Generate and play a single bell tone (dual frequency for distinctiveness)
function playBellTone() {
    if (!STATE.audioContext) return;
    
    try {
        const ctx = STATE.audioContext;
        const now = ctx.currentTime;
        const duration = 0.8;
        
        // Primary frequency (C5)
        const osc1 = ctx.createOscillator();
        const gain1 = ctx.createGain();
        osc1.connect(gain1);
        gain1.connect(ctx.destination);
        osc1.frequency.value = 523;
        osc1.type = 'sine';
        gain1.gain.setValueAtTime(0.25, now);
        gain1.gain.exponentialRampToValueAtTime(0.01, now + duration);
        osc1.start(now);
        osc1.stop(now + duration);
        STATE.soundOscillators.push(osc1);
        
        // Secondary frequency (G5) for richer tone
        const osc2 = ctx.createOscillator();
        const gain2 = ctx.createGain();
        osc2.connect(gain2);
        gain2.connect(ctx.destination);
        osc2.frequency.value = 784;
        osc2.type = 'sine';
        gain2.gain.setValueAtTime(0.15, now);
        gain2.gain.exponentialRampToValueAtTime(0.01, now + duration);
        osc2.start(now);
        osc2.stop(now + duration);
        STATE.soundOscillators.push(osc2);
    } catch (error) {
        console.error('Bell tone generation failed:', error);
    }
}

// ====== RAFRAÎCHISSEMENT PRINCIPAL ======

function getClientId() {
    let cid = localStorage.getItem("client_id");
    if (cid) return cid;

    // première visite → on laisse le serveur le générer
    return "";
}

function refreshDashboardData() {
    if (STATE.refreshInProgress) return;
    STATE.refreshInProgress = true;

    const refreshBtn = DOM.refreshBtn;
    const refreshIcon = refreshBtn?.querySelector('.refresh-icon');

    if (refreshBtn) {
        refreshBtn.disabled = true;
        refreshBtn.style.opacity = '0.6';
        if (refreshIcon) refreshIcon.style.animation = 'spin 1s linear infinite';
    }

    // Collect filter parameters
    const statusFilter = document.querySelector('input[name="statusFilter"]:checked')?.value || 'all';
    const typeFilter = document.querySelector('input[name="typeFilter"]:checked')?.value || 'all';
    const periodFilter = document.querySelector('input[name="periodFilter"]:checked')?.value || 'all';

    // Send filters in request body
    const clientId = getClientId();

    fetch('/api/dashboard_data', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            "X-Client-Id": clientId
        },
        body: JSON.stringify({
            status: statusFilter,
            type: typeFilter,
            period: periodFilter
        })
    })
        .then(response => {
            const newId = response.headers.get("X-Client-Id");
            // N'écrase que si le localStorage est vide !
            if (newId && !localStorage.getItem("client_id")) {
                localStorage.setItem("client_id", newId);
            }

            if (!response.ok) throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            return response.json();
        })
        .then(data => {
            updateDashboardDOM(data);
        })
        .catch(err => {
            console.error('Dashboard update error:', err);
        })
        .finally(() => {
            if (refreshBtn) {
                refreshBtn.disabled = false;
                refreshBtn.style.opacity = '1';
                if (refreshIcon) refreshIcon.style.animation = '';
            }
            STATE.refreshInProgress = false;
        });
}


// ====== MISE À JOUR DU DOM ======
function updateDashboardDOM(data) {
    // Si le backend renvoie une erreur, ne pas tenter de rendre
    if (!data || data.error) {
        if (data && data.error) {
            Toasts.show(`Erreur serveur: ${data.error}`, { type: 'error' });
        }
        return;
    }
    const intervals = ['5min', '1day', '1week'];

    // Helper function to build table rows
    const buildTableRow = (loc, names, recapData, isTotal = false) => {
        const nowRecap = recapData['5min'][loc];
        const linkTotal = nowRecap?.link?.total && nowRecap.link.total !== '#' ? nowRecap.link.total : null;
        const linkPertes = nowRecap?.link?.pertes && nowRecap.link.pertes !== '#' ? nowRecap.link.pertes : null;
        const locationName = names[loc] || loc;

        let row = `<tr${isTotal ? ' class="total-row"' : ''}><td class="location-cell">${locationName}</td>`;

        // Traffic columns
        const nowVal = nowRecap?.total?.end ?? '';
        row += `<td class="traffic-cell${linkTotal ? ' clickable-cell' : ''}"${linkTotal ? ` data-link-total='${linkTotal}'` : ''} title="DIAGNET EXNET: open Traffic View"><span class="value">${nowVal !== '' ? Number(nowVal).toFixed(2) : '-'}</span></td>`;

        intervals.forEach(interval => {
            const recapLoc = recapData[interval] && recapData[interval][loc];
            const start = recapLoc?.total?.start ?? '';
            const end = recapLoc?.total?.end ?? '';
            // const diff = (start !== '' && end !== '') ? Math.abs(parseFloat(start) - parseFloat(end)).toFixed(2) : '';
            const diff = recapLoc?.total?.taux != null ? Math.abs(Number(recapLoc.total.taux)).toFixed(2) : '';

            row += `<td class="traffic-cell${linkTotal ? ' clickable-cell' : ''}"${linkTotal ? ` data-link-total='${linkTotal}'` : ''} title="DIAGNET EXNET: open Traffic View"><span class="value">${start !== '' ? Number(start).toFixed(2) : '-'}</span>`;
            if (recapLoc && diff) {
                row += `<span class="trend" style="color:${UIUtils.trendColorToCss(recapLoc.total?.trend_color)}">${recapLoc.total?.trend_icon ?? ''}${diff}</span>`;
            }
            row += `</td>`;
        });

        // Pertes columns
        const nowPertes = nowRecap?.pertes?.end ?? '';
        row += `<td class="loss-cell separator-col${linkPertes ? ' clickable-cell' : ''}"${linkPertes ? ` data-link-pertes='${linkPertes}'` : ''} title="DIAGNET EXNET: open Packet Loss View"><span class="value">${nowPertes !== '' ? Number(nowPertes).toFixed(3) : '-'}</span></td>`;

        intervals.forEach(interval => {
            const recapLoc = recapData[interval] && recapData[interval][loc];
            const start = recapLoc?.pertes?.start ?? '';
            const end = recapLoc?.pertes?.end ?? '';
            // const diff = (start !== '' && end !== '') ? Math.abs(parseFloat(start) - parseFloat(end)).toFixed(3) : '';
            const diff = recapLoc?.pertes?.taux != null ? Math.abs(Number(recapLoc.pertes.taux)).toFixed(3) : '';

            row += `<td class="loss-cell${linkPertes ? ' clickable-cell' : ''}"${linkPertes ? ` data-link-pertes='${linkPertes}'` : ''} title="DIAGNET EXNET: open Packet Loss View"><span class="value">${start !== '' ? Number(start).toFixed(3) : '-'}</span>`;
            if (recapLoc && diff) {
                row += `<span class="trend" style="color:${UIUtils.trendColorToCss(recapLoc.pertes?.trend_color)}">${recapLoc.pertes?.trend_icon ?? ''}${diff}</span>`;
            }
            row += `</td>`;
        });

        return row + `</tr>`;
    };

    // Helper function to build total row from totals data
    const buildTotalRow = (totalsData, label = 'Total') => {
        const safeTotals = totalsData || {};
        const trafficTotals = safeTotals.traffic || {};
        const pertesTotals = safeTotals.pertes || {};
        let row = `<tr class="total-row"><td class="location-cell">${label}</td>`;

        // Traffic columns
        const totNow = trafficTotals['5min']?.traffic_end ?? '';
        row += `<td class="traffic-cell"><span class="value">${totNow !== '' ? Number(totNow).toFixed(2) : '-'}</span></td>`;

        intervals.forEach(interval => {
            const tot = trafficTotals[interval];
            const start = parseFloat(tot?.traffic_start);
            const end = parseFloat(tot?.traffic_end);
            // const diff = (!isNaN(start) && !isNaN(end)) ? Math.abs(start - end).toFixed(2) : '';
            const diff = tot?.taux_traffic != null ? Math.abs(tot?.taux_traffic).toFixed(2) : '';

            row += `<td class="traffic-cell"><span class="value">${!isNaN(start) ? start.toFixed(2) : '-'}</span>`;
            if (tot && diff) {
                row += `<span class="trend" style="color:${UIUtils.trendColorToCss(tot.trend_color)}">${tot.trend_icon ?? ''}${diff}</span>`;
            }
            row += `</td>`;
        });

        // Pertes columns
        const totNowPertes = pertesTotals['5min']?.pertes_end ?? '';
        row += `<td class="loss-cell separator-col"><span class="value">${totNowPertes !== '' ? Number(totNowPertes).toFixed(3) : '-'}</span></td>`;

        intervals.forEach(interval => {
            const tot = pertesTotals[interval];
            const start = parseFloat(tot?.pertes_start);
            const end = parseFloat(tot?.pertes_end);
            // const diff = (!isNaN(start) && !isNaN(end)) ? Math.abs(start - end).toFixed(3) : '';
            const diff = tot?.taux_pertes != null ? Math.abs(tot?.taux_pertes).toFixed(3) : '';

            row += `<td class="loss-cell"><span class="value">${!isNaN(start) ? start.toFixed(3) : '-'}</span>`;
            if (tot && diff) {
                row += `<span class="trend" style="color:${UIUtils.trendColorToCss(tot.trend_color)}">${tot.trend_icon ?? ''}${diff}</span>`;
            }
            row += `</td>`;
        });

        return row + `</tr>`;
    };

    // Build totals row for TCP+DNS metrics (used by International + GI section)
    const buildTcpDnsTotalRow = (totalsData, label = 'Total') => {
        const safeTotals = totalsData || {};
        let row = `<tr class="total-row"><td class="location-cell">${label}</td>`;

        const metrics = ['latence', 'tcp_no_response', 'tcp_block_wp_up', 'dns_malformed', 'dns_no_response'];
        // Now for each metric, show the 5min 'end' value, then starts for intervals
        metrics.forEach((metric, metricIndex) => {
            const separatorClass = metricIndex > 0 ? ' separator-col' : '';
            const decimalPlaces = metric.startsWith('dns') ? 3 : 2;

            const nowVal = safeTotals[metric]?.['5min'] ? safeTotals[metric]['5min'][`${metric}_end`] : '';
            row += `<td class="tcp-cell${separatorClass}"><span class="value">${nowVal !== '' ? Number(nowVal).toFixed(decimalPlaces) : '-'}</span></td>`;

            intervals.forEach(interval => {
                const info = safeTotals[metric]?.[interval];
                const start = info ? info[`${metric}_start`] : '';
                const taux = info ? info[`taux_${metric}`] : '';
                row += `<td class="tcp-cell"><span class="value">${start !== '' ? Number(start).toFixed(decimalPlaces) : '-'}</span>`;
                if (info && taux !== undefined && taux !== null && taux !== '') {
                    const trendColor = info.trend_color || 'neutral';
                    const trendIcon = info.trend_icon || '';
                    const tauxValue = Math.abs(Number(taux)).toFixed(decimalPlaces);
                    row += `<span class="trend" style="color:${UIUtils.trendColorToCss(trendColor)}">${trendIcon}${tauxValue}</span>`;
                }
                row += `</td>`;
            });
        });

        return row + `</tr>`;
    };


    // Helper function to build TCP metrics table row (for international+gi section)
    const buildTcpMetricsRow = (loc, combinedNames, recapData) => {
        const locationName = combinedNames[loc] || loc;
        let row = `<tr><td class="location-cell">${locationName}</td>`;

        // Get links for this location
        const links = recapData['5min'] && recapData['5min'][loc]?.link;

        // TCP Metrics: latence, tcp_no_response, tcp_block_wp_up
        const tcpMetrics = ['latence', 'tcp_no_response', 'tcp_block_wp_up'];
        const intervals = ['5min', '1day', '1week'];

        tcpMetrics.forEach((metric, metricIndex) => {
            const decimalPlaces = metric === 'latence' ? 2 : 2; // 2 décimales pour packets/sec et latence
            const separatorClass = metricIndex > 0 ? ' separator-col' : '';

            // Determine link attribute based on metric
            let linkAttr = '';
            let linkValue = '';
            // links.tcp_tts is for latency view
            if (metric === 'latence' && links?.tcp_tts) {
                linkAttr = 'data-link-tcp-tts';
                linkValue = links.tcp_tts;
            }
            // links.tcp_service_access is for TCP Service Access (tcp_no_response and tcp_block_wp_up)
            else if ((metric === 'tcp_no_response' || metric === 'tcp_block_wp_up') && links?.tcp_service_access) {
                linkAttr = 'data-link-tcp-service-access';
                linkValue = links.tcp_service_access;
            }

            // NOW column (only for 5min data)
            const nowData = recapData['5min'] && recapData['5min'][loc]?.tcp_metrics?.[metric];
            const valNow = nowData?.end ?? '';

            row += `<td class="tcp-cell${separatorClass}${linkValue ? ' clickable-cell' : ''}"${linkValue ? ` ${linkAttr}='${linkValue}'` : ''} title="${linkValue ? 'DIAGNET EXNET: open TCP ' + (metric === 'latence' ? 'TTS' : 'Service Access') + ' View' : ''}"><span class="value">${valNow !== '' ? Number(valNow).toFixed(decimalPlaces) : '-'}</span></td>`;

            intervals.forEach(interval => {
                const intervalData = recapData[interval] && recapData[interval][loc]?.tcp_metrics?.[metric];
                const val = intervalData?.start ?? '';

                row += `<td class="tcp-cell${linkValue ? ' clickable-cell' : ''}"${linkValue ? ` ${linkAttr}='${linkValue}'` : ''} title="${linkValue ? 'DIAGNET EXNET: open TCP ' + (metric === 'latence' ? 'TTS' : 'Service Access') + ' View' : ''}"><span class="value">${val !== '' ? Number(val).toFixed(decimalPlaces) : '-'}</span>`;

                // Add trend for interval data if available
                if (intervalData && intervalData.taux != null) {
                    const trendColor = intervalData.trend_color || 'neutral';
                    const trendIcon = intervalData.trend_icon || '';
                    const tauxValue = Math.abs(Number(intervalData.taux)).toFixed(decimalPlaces);
                    row += `<span class="trend" style="color:${UIUtils.trendColorToCss(trendColor)}">${trendIcon}${tauxValue}</span>`;
                }
                row += `</td>`;
            });
        });

        // DNS metrics (dns_malformed, dns_no_response)
        const dnsMetrics = ['dns_malformed', 'dns_no_response'];
        dnsMetrics.forEach((metric, idx) => {
            const separatorClass = ' separator-col';
            const decimalPlaces = 3; // DNS percentages should be 3 decimals

            // External link for DNS summary if available
            const dnsLinkValue = links?.dns;
            const dnsLinkAttr = dnsLinkValue ? ` data-link-dns='${dnsLinkValue}'` : '';
            const dnsClickableClass = dnsLinkValue ? ' clickable-cell' : '';

            const nowData = recapData['5min'] && recapData['5min'][loc]?.dns_metrics?.[metric];
            const valNow = nowData?.end ?? '';
            row += `<td class="tcp-cell${separatorClass}${dnsClickableClass}"${dnsLinkAttr} title="${dnsLinkValue ? 'DIAGNET EXNET: open DNS Summary View' : ''}"><span class="value">${valNow !== '' ? Number(valNow).toFixed(decimalPlaces) : '-'}</span></td>`;

            intervals.forEach(interval => {
                const intervalData = recapData[interval] && recapData[interval][loc]?.dns_metrics?.[metric];
                const val = intervalData?.start ?? '';

                row += `<td class="tcp-cell${dnsClickableClass}"${dnsLinkAttr} title="${dnsLinkValue ? 'DIAGNET EXNET: open DNS Summary View' : ''}"><span class="value">${val !== '' ? Number(val).toFixed(decimalPlaces) : '-'}</span>`;
                if (intervalData && intervalData.taux != null) {
                    const trendColor = intervalData.trend_color || 'neutral';
                    const trendIcon = intervalData.trend_icon || '';
                    const tauxValue = Math.abs(Number(intervalData.taux)).toFixed(decimalPlaces);
                    row += `<span class="trend" style="color:${UIUtils.trendColorToCss(trendColor)}">${trendIcon}${tauxValue}</span>`;
                }
                row += `</td>`;
            });
        });

        return row + `</tr>`;
    };

    const tableHeader = `<table class="data-table"><thead><tr><th rowspan="2">Location</th><th colspan="4">Traffic (Gbits/s)</th><th class="separator-col" colspan="4">Pertes (%)</th></tr><tr><th>Now</th><th>5min</th><th>1D</th><th>1W</th><th class="separator-col">Now</th><th>5min</th><th>1D</th><th>1W</th></tr></thead><tbody>`;

    // === Sortie Internationale ===
    let intlTable = tableHeader;
    Object.keys(data.names || {}).forEach(loc => {
        intlTable += buildTableRow(loc, data.names, data.recap);
    });
    intlTable += buildTotalRow(data.totals_international);
    intlTable += `</tbody></table>`;

    const intlDiv = DOM.tableWrapperInternational;
    if (intlDiv) {
        intlDiv.innerHTML = intlTable;
        TableManager.delegateTbodyClick(intlDiv.querySelector('tbody'), 'clickable-cell', 'data-link-total', 'TD');
    }

    // === Traffic Utilisateur (GI) ===
    let giTable = tableHeader;
    Object.keys(data.gi_names || {}).forEach(loc => {
        giTable += buildTableRow(loc, data.gi_names, data.recap);
    });
    giTable += buildTotalRow(data.totals_gi);
    giTable += `</tbody></table>`;

    const giDiv = DOM.tableWrapperGi;
    if (giDiv) {
        giDiv.innerHTML = giTable;
        TableManager.delegateTbodyClick(giDiv.querySelector('tbody'), 'clickable-cell', 'data-link-total', 'TD');
    }

    // === International + GI (TCP Metrics) ===
    const tcpMetricsHeader = `<table class="data-table"><thead><tr><th rowspan="2">Location</th><th colspan="4">Latence (ms)</th><th class="separator-col" colspan="4">TCP No Response (%)</th><th class="separator-col" colspan="4">TCP Block WP Up (%)</th><th class="separator-col" colspan="4">DNS Malformed (%)</th><th class="separator-col" colspan="4">DNS No Response (%)</th></tr><tr><th>Now</th><th>5min</th><th>1D</th><th>1W</th><th class="separator-col">Now</th><th>5min</th><th>1D</th><th>1W</th><th class="separator-col">Now</th><th>5min</th><th>1D</th><th>1W</th><th class="separator-col">Now</th><th>5min</th><th>1D</th><th>1W</th><th class="separator-col">Now</th><th>5min</th><th>1D</th><th>1W</th></tr></thead><tbody>`;

    let internationalGiTable = tcpMetricsHeader;

    // Combine names from international and gi with _gi suffix for gi locations
    const combinedNames = {
        ...data.names,
        ...Object.fromEntries(
            Object.entries(data.gi_names).map(([key, value]) => [key, value + '_gi'])
        )
    };

    // Add rows for all locations from international and gi
    const allLocations = [...new Set([...Object.keys(data.names || {}), ...Object.keys(data.gi_names || {})])];

    allLocations.forEach(loc => {
        internationalGiTable += buildTcpMetricsRow(loc, combinedNames, data.recap);
    });

    // Add totals row for combined TCP+DNS metrics (use combined international+GI totals)
    internationalGiTable += buildTcpDnsTotalRow(data.totals_international_gi, 'Total');
    internationalGiTable += `</tbody></table>`;

    const internationalGiDiv = DOM.tableWrapperInternationalGi;
    if (internationalGiDiv) {
        internationalGiDiv.innerHTML = internationalGiTable;
        TableManager.delegateTbodyClick(internationalGiDiv.querySelector('tbody'), 'clickable-cell', 'data-link-total', 'TD');
    }

    // === TDD ===
    let tddTable = tableHeader;
    Object.keys(data.gi_names || {}).forEach(loc => {
        const modifiedNames = Object.fromEntries(
            Object.entries(data.gi_names).map(([k, v]) => [k, v + ' GI'])
        );
        tddTable += buildTableRow(loc, modifiedNames, data.tdd_recap);
    });
    tddTable += buildTotalRow(data.totals_tdd);
    tddTable += `</tbody></table>`;

    const tddDiv = DOM.tableWrapperTdd;
    if (tddDiv) {
        tddDiv.innerHTML = tddTable;
        TableManager.delegateTbodyClick(tddDiv.querySelector('tbody'), 'clickable-cell', 'data-link-total', 'TD');
    }

    // === Roaming Utilisateur ===
    const roamingIntervals = ['now', '5min', '1day', '1week'];
    roamingIntervals.forEach(interval => {
        let roamingTable = `<table class="data-table"><thead><tr><th>Ville</th><th>Roaming IN UP Gp</th><th>Roaming IN UP S8</th><th>Roaming OUT UP</th></tr></thead><tbody>`;

        data.roaming_cities.forEach(city => {
            roamingTable += `<tr><td class="location-cell">${city.charAt(0).toUpperCase() + city.slice(1)}</td>`;

            data.roaming_types.forEach(type => {
                const location = `${city}_gi_roaming_${type}`;
                const recap = data.roaming_recap[interval === 'now' ? '5min' : interval][location];
                const cellLinkTotal = recap?.link?.total && recap.link.total !== '#' ? recap.link.total : null;
                const cellLinkPertes = recap?.link?.pertes && recap.link.pertes !== '#' ? recap.link.pertes : null;

                if (interval === 'now') {
                    const end = recap?.total?.end ?? '';
                    const endp = recap?.pertes?.end ?? '';
                    roamingTable += `<td class="roaming-cell">
                        <div class="metric${cellLinkTotal ? ' clickable-cell' : ''}"${cellLinkTotal ? ` data-link-total='${cellLinkTotal}'` : ''} title="DIAGNET EXNET: open Traffic View"><span>Traffic:</span><span class="value">${end !== '' ? Number(end).toFixed(2) : '-'}</span></div>
                        <div class="metric${cellLinkPertes ? ' clickable-cell' : ''}"${cellLinkPertes ? ` data-link-pertes='${cellLinkPertes}'` : ''} title="DIAGNET EXNET: open Packet Loss View"><span>Pertes:</span><span class="value">${endp !== '' ? Number(endp).toFixed(3) : '-'}</span></div>
                    </td>`;
                } else {
                    const start = recap?.total?.start ?? '';
                    const end = recap?.total?.end ?? '';
                    const startp = recap?.pertes?.start ?? '';
                    const endp = recap?.pertes?.end ?? '';
                    // const diffTraffic = (start !== '' && end !== '') ? Math.abs(parseFloat(start) - parseFloat(end)).toFixed(2) : '';
                    const diffTraffic = recap?.total?.taux != null ? Math.abs(Number(recap.total.taux)).toFixed(2) : '';
                    // const diffPertes = (startp !== '' && endp !== '') ? Math.abs(parseFloat(startp) - parseFloat(endp)).toFixed(3) : '';
                    const diffPertes = recap?.pertes?.taux != null ? Math.abs(Number(recap.pertes.taux)).toFixed(3) : '';

                    roamingTable += `<td class="roaming-cell">
                        <div class="metric${cellLinkTotal ? ' clickable-cell' : ''}"${cellLinkTotal ? ` data-link-total='${cellLinkTotal}'` : ''} title="DIAGNET EXNET: open Traffic View"><span>Traffic:</span><span class="value">${start !== '' ? Number(start).toFixed(2) : ''}</span><span class="trend" style="color:${UIUtils.trendColorToCss(recap?.total?.trend_color)}">${recap?.total?.trend_icon ?? ''}${diffTraffic}</span></div>
                        <div class="metric${cellLinkPertes ? ' clickable-cell' : ''}"${cellLinkPertes ? ` data-link-pertes='${cellLinkPertes}'` : ''} title="DIAGNET EXNET: open Packet Loss View"><span>Pertes:</span><span class="value">${startp !== '' ? Number(startp).toFixed(3) : ''}</span><span class="trend" style="color:${UIUtils.trendColorToCss(recap?.pertes?.trend_color)}">${recap?.pertes?.trend_icon ?? ''}${diffPertes}</span></div>
                    </td>`;
                }
            });
            roamingTable += `</tr>`;
        });

        roamingTable += `</tbody></table>`;
        const roamingDiv = DOM.getTableWrapperRoaming(interval);
        if (roamingDiv) {
            roamingDiv.innerHTML = roamingTable;
            const tbody = roamingDiv.querySelector('tbody');
            if (tbody) TableManager.delegateTbodyClick(tbody, 'metric', 'data-link', 'DIV');
        }
    });

    // === Alertes ===
    // Ensure alertes data structure exists
    if (!data.alertes) {
        data.alertes = { traffic: [], pertes: [] };
    }
    if (!Array.isArray(data.alertes.traffic)) {
        data.alertes.traffic = [];
    }
    if (!Array.isArray(data.alertes.pertes)) {
        data.alertes.pertes = [];
    }

    const allAlertes = [...(data.alertes?.traffic || []), ...(data.alertes?.pertes || [])];
    allAlertes.sort((a, b) => (b.creation_time || b.date).localeCompare(a.creation_time || a.date));

    // Store dashboard data globally for filter functionality
    window.lastDashboardData = data;

    // Récupérer les alertes ignorées via AlertesManager
    const dismissedAlertes = AlertManager.getDismissedAlertes();

    // Filtrer les alertes à afficher pour exclure celles qui sont ignorées
    const finalAlertesToShow = allAlertes.filter(a => !dismissedAlertes.includes(a.id));

    AlertManager.updateBadge(finalAlertesToShow);
    AlertManager.checkNewAlertes(finalAlertesToShow);
    // AlertManager.displayAlertes(data.alertes);

    // === Timestamp ===
    if (DOM.lastUpdateValue && data.current_time) {
        DOM.lastUpdateValue.textContent = data.current_time;
    }
}

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
