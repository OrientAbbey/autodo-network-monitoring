// AUTODO — Alertes : liste, toasts, pop-up
// Script classique (partage la portée globale avec les autres fichiers de static/js/, chargés dans l'ordre de dashboard.html).

// ====== GESTIONNAIRE DES ALERTES ======
const AlertManager = {
    /** Une alerte « en attente de validation » n'est pas encore un ticket officiel : ni son, ni pop-up, ni compteur. */
    isActionable(alerte) {
        return alerte.status === 'open' && alerte.validation_state !== 'pending';
    },

    updateBadge(allAlertes) {
        document.querySelectorAll('#alertes-badge-open, #alertes-badge').forEach(e => e.remove());

        const openAlertes = allAlertes.filter(a => this.isActionable(a));
        const totalOpenAlertes = openAlertes.length;
        const badgeColor = totalOpenAlertes > 0 ? 'var(--danger)' : 'var(--success)';

        const badge = el('div', { id: 'alertes-badge', class: 'alertes-badge', style: `background:${badgeColor};display:none` },
            el('span', { text: `🚨 ${openAlertes.filter(a => a.type === 'TRAFFIC').length} ${t('badge.traffic')}` }),
            el('span', { style: 'margin-left:10px;', text: `⚠️ ${openAlertes.filter(a => a.type === 'PERTES').length} ${t('badge.losses')}` }));
        const openBtn = el('div', { id: 'alertes-badge-open', class: 'alertes-badge-open', title: t('badge.show'), role: 'button', tabindex: '0',
            'aria-label': t('badge.show'), style: `background:${badgeColor};display:flex` }, '🔔',
            el('span', { class: 'badge-count', style: `background:${badgeColor};display:${totalOpenAlertes > 0 ? 'block' : 'none'}`, text: totalOpenAlertes }));

        const closeBtn = DOM.createCloseBtn();
        closeBtn.setAttribute('aria-label', t('common.close'));
        closeBtn.addEventListener('click', e => { badge.style.display = 'none'; openBtn.style.display = 'flex'; e.stopPropagation(); });
        badge.appendChild(closeBtn);
        const reveal = () => { badge.style.display = 'flex'; openBtn.style.display = 'none'; };
        openBtn.addEventListener('click', reveal);
        openBtn.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); reveal(); } });

        const host = DOM.controls || document.body;
        host.appendChild(badge);
        host.appendChild(openBtn);
    },

    /**
     * Pop-up d'alerte PARTAGÉE : elle liste les alertes ouvertes que PERSONNE n'a encore acquittées.
     * Quand quelqu'un clique « Ok », l'accusé est enregistré côté serveur : la pop-up se ferme chez tous les spectateurs.
     */
    checkNewAlertes(allAlertes) {
        const actionable = allAlertes.filter(a => this.isActionable(a));
        STATE.currentAlertes = actionable;
        const unacked = actionable.filter(a => !a.acked);
        const popupVisible = DOM.alertPopup && getComputedStyle(DOM.alertPopup).display !== 'none';

        if (popupVisible && !(STATE.popupIds || []).some(id => unacked.some(a => a.id === id))) {
            this.closeAlertPopup();                         // acquittée ailleurs (ou fermée) : plus rien à signaler
        }
        const fresh = unacked.filter(a => !STATE.shownIds.has(a.id));
        if (fresh.length > 0) {
            unacked.forEach(a => STATE.shownIds.add(a.id));
            this.showAlertPopup(unacked);
        }
        STATE.isFirstLoad = false;
        this.updateSectionAlertes(allAlertes);
        STATE.previousAlertes = [...actionable];
    },

    showAlertPopup(alertes) {
        const popup = DOM.alertPopup;
        const message = DOM.alertPopupMessage;
        if (!popup || !message || alertes.length === 0) return;
        STATE.popupIds = alertes.map(a => a.id);
        const label = a => a.title || a.message || '';
        clear(message);
        message.appendChild(el('ul', { class: 'popup-alerts' }, alertes.slice(0, 5).map(a => el('li', { text: label(a) }))));
        if (alertes.length > 5) message.appendChild(el('p', { class: 'hint', text: t('alert.and_more', { count: alertes.length - 5 }) }));
        popup.style.display = 'flex';
        this._lastFocus = document.activeElement;
        popup.querySelector('button')?.focus();             // focus sur « Ok » (lecteurs d'écran, clavier)
        announce(`${t('alert.new')} : ${alertes.map(label).slice(0, 3).join(' ; ')}`);
        AlertChannels.raise(label(alertes[0]), alertes.length);
    },

    /** Ferme la pop-up SANS acquitter (acquittement fait ailleurs, changement de configuration…). */
    closeAlertPopup() {
        DOM.alertPopup?.style.setProperty('display', 'none');
        AlertChannels.clear();
        this._lastFocus?.focus?.();                         // rend le focus à l'élément précédent
        this._lastFocus = null;
        STATE.popupIds = [];
    },

    /** Bouton « Ok » / Échap : enregistre l'accusé de réception partagé puis ferme la pop-up. */
    async acknowledge() {
        const ids = STATE.popupIds || [];
        this.closeAlertPopup();
        if (ids.length === 0) return;
        try {
            await api('/api/ack', { method: 'POST', body: { ticket_ids: ids } });
        } catch (e) {
            Toasts.show(t('alert.ack_failed', { error: e.message }), { type: 'error' });
        }
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
                    countText = `(<span class="badge-closed">${filteredClosedAlertes.length} ${t('alert.closed_n')}</span>, <span class="badge-open">${filteredOpenAlertes.length} ${t('alert.open_n')}</span>)`;
                } else if (filteredClosedAlertes.length > 0) {
                    countText = `(<span class="badge-closed">${filteredClosedAlertes.length} ${t('alert.closed_n')}</span>)`;
                } else if (filteredOpenAlertes.length > 0) {
                    countText = `(<span class="badge-open">${filteredOpenAlertes.length} ${t('alert.open_n')}</span>)`;
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
                            </span>${a.validation_state === 'pending' ? `<span class="badge-pending">${esc(t('alert.pending'))}</span>` : ''}${a.acked ? `<span class="badge-acked" title="${esc(a.acked_by || '')}">✓ ${esc(t('alert.acked'))}</span>` : ''}<br>
                            ${esc(t('alert.values'))}: ${(a.valeurs || []).map(v => (v == null ? '—' : Number(v).toFixed(3))).join(' → ')}<br>${esc(t('alert.date'))}: ${esc(a.creation_time || a.date || 'N/A')}
                        </span>
                        ${isOpen ? `<button class="btn-secondary" style="font-size:0.8em;padding:2px 8px;" data-action="dismissAlerte" data-id="${esc(a.id)}" aria-label="${esc(t('alert.hide'))}">✕</button>` : ''}
                    </div>`;
                }).join('');
            }
            else {
                // Show appropriate message based on filters
                const hasTimeFilter = periodFilter !== 'all';
                const hasTypeFilter = !allTypeChecked;
                const hasStatusFilter = statusFilter !== 'all';

                if (hasTimeFilter || hasTypeFilter || hasStatusFilter) {
                    html = `<div class="no-data">${esc(t('alert.none_filtered'))}</div>`;
                } else if (statusFilter === 'open') {
                    if (closedAlertes.length > 0) {
                        html = `<div class="no-data">${esc(t('alert.none_open'))} <i>(${esc(t('alert.use_filters'))})</i></div>`;
                    } else {
                        html = `<div class="no-data">${esc(t('alert.none_section'))}</div>`;
                    }
                } else {
                    html = `<div class="no-data">${esc(t('alert.none_section'))}</div>`;
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
                    container.innerHTML = `<div class="no-data">${esc(t('alert.none_section'))}</div>`;
                }
            });

            // Mettre à jour l'interface
            refreshDashboardData();

            Toasts.show(t('alert.hidden', { id: alerteId }), { type: 'success', durationMs: 1000 });

        } catch (error) {
            console.error('Erreur lors de la suppression de l\'alerte:', error);
            Toasts.show(t('alert.hide_failed', { id: alerteId }), { type: 'error' });
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
        if (confirm(t('alert.confirm_clear'))) {
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
