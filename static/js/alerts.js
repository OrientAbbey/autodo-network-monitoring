// AUTODO — Alertes : liste, toasts, pop-up
// Script classique (partage la portée globale avec les autres fichiers de static/js/, chargés dans l'ordre de dashboard.html).

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
            this._lastFocus = document.activeElement;
            popup.querySelector('button')?.focus();        // focus sur « Ok » (lecteurs d'écran, clavier)

            // Start the looping alert sound
            playAlertSound();
        }
    },

    closeAlertPopup() {
        DOM.alertPopup?.style.setProperty('display', 'none');
        stopAlertSound(); // Stop the looping sound when popup is closed
        this._lastFocus?.focus?.();                         // rend le focus à l'élément précédent
        this._lastFocus = null;
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
