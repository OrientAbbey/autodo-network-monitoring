// AUTODO — Panneau de configuration (administrateurs)
// Script classique (partage la portée globale avec les autres fichiers de static/js/, chargés dans l'ordre de dashboard.html).

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
