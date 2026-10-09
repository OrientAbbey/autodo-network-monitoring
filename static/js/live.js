// AUTODO — Onglets, mise à jour automatique (SSE), compte à rebours, toasts
// Script classique (partage la portée globale avec les autres fichiers de static/js/, chargés dans l'ordre de dashboard.html).

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
            button.setAttribute('aria-pressed', String(!!STATE.isAutoUpdateEnabled));
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
