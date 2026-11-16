// AUTODO — Interface commune : préférences (dialogue), lien partageable, présence, bandeau de maintenance, initialisation.
// Chargé sur TOUTES les pages après session.js. Dépend de : common.js, i18n.js, prefs.js, session.js.

const AppUI = {
    prefsDialog: null,

    // ---------------------------------------------------------------- dialogue des préférences
    radioGroup(name, legend, options) {
        return el('fieldset', { class: 'pref-group' }, el('legend', { text: legend }),
            options.map(([value, label]) => el('label', { class: 'pref-option' },
                el('input', { type: 'radio', name, value, checked: Prefs.get(name) === value, on: { change: () => Prefs.set(name, value) } }),
                el('span', { text: label }))));
    },

    toggle(name, label) {
        return el('label', { class: 'pref-option' },
            el('input', { type: 'checkbox', name, checked: Prefs.get(name), on: { change: event => Prefs.set(name, event.target.checked) } }),
            el('span', { text: label }));
    },

    buildPrefsDialog() {
        const linkField = el('input', { type: 'text', readonly: true, id: 'prefs-link', value: Prefs.shareLink(), 'aria-label': t('prefs.share_link') });
        const copied = el('span', { class: 'hint', id: 'prefs-copied', role: 'status' });
        const dialog = el('dialog', { id: 'prefs-dialog', class: 'app-dialog', 'aria-labelledby': 'prefs-title' },
            el('div', { class: 'dialog-form' },
                el('h3', { id: 'prefs-title', text: t('prefs.title') }),
                this.radioGroup('lang', t('prefs.language'), [['fr', 'Français'], ['en', 'English']]),
                this.radioGroup('theme', t('prefs.theme'), [['dark', t('prefs.theme_dark')], ['light', t('prefs.theme_light')]]),
                this.radioGroup('palette', t('prefs.palette'), [['default', t('prefs.palette_default')], ['colorblind', t('prefs.palette_colorblind')]]),
                el('fieldset', { class: 'pref-group' }, el('legend', { text: t('prefs.alerts') }),
                    this.toggle('sound', t('prefs.sound')), this.toggle('voice', t('prefs.voice')), this.toggle('notifications', t('prefs.notifications')),
                    el('button', { type: 'button', class: 'btn-secondary', dataset: { action: 'testSound' }, text: t('prefs.test_sound') })),
                el('fieldset', { class: 'pref-group' }, el('legend', { text: t('prefs.share') }),
                    el('p', { class: 'hint', text: t('prefs.share_hint') }), linkField,
                    el('button', { type: 'button', class: 'btn-secondary', dataset: { action: 'copyPrefsLink' }, text: t('prefs.copy_link') }), copied),
                el('div', { class: 'dialog-actions' }, el('button', { type: 'button', class: 'btn-primary', dataset: { action: 'closeDialog' }, text: t('common.close') }))));
        document.body.appendChild(dialog);
        return dialog;
    },

    openPrefs() {
        this.prefsDialog?.remove();                     // reconstruit : reflète la langue et les valeurs courantes
        this.prefsDialog = this.buildPrefsDialog();
        this.prefsDialog.showModal();
    },

    async copyPrefsLink() {
        const link = Prefs.shareLink();
        $('#prefs-link').value = link;
        try { await navigator.clipboard.writeText(link); $('#prefs-copied').textContent = t('prefs.copied'); }
        catch (e) { $('#prefs-link').select(); $('#prefs-copied').textContent = t('prefs.copy_manually'); }
    },

    // ---------------------------------------------------------------- présence (spectateurs connectés)
    async refreshPresence() {
        const host = document.getElementById('presence');
        if (!host) return;
        try {
            const data = await api('/api/presence');
            host.textContent = `👥 ${data.viewers}`;
            host.title = t('presence.title', { viewers: data.viewers, peak: data.peak_today });
            host.setAttribute('aria-label', host.title);
        } catch (e) { /* silencieux : indicateur secondaire */ }
    },

    // ---------------------------------------------------------------- bandeau de maintenance
    async refreshMaintenance() {
        const host = document.getElementById('maintenance-banner');
        if (!host) return;
        try {
            const { windows } = await api('/api/maintenance/active');
            clear(host);
            host.hidden = windows.length === 0;
            windows.forEach(w => host.appendChild(el('div', { class: 'maintenance-item' },
                `🛠️ ${w.db_key === '*' ? t('maintenance.all') : w.db_key} — ${t('maintenance.until', { time: w.ends_at })}`, w.reason ? ` (${w.reason})` : '')));
        } catch (e) { /* silencieux */ }
    },

    init() {
        if (Prefs.importFromHash()) Toasts.show(t('prefs.imported'), { type: 'success', durationMs: 3000 });
        applyI18n();
        Session.load();
        this.refreshPresence();
        this.refreshMaintenance();
        setInterval(() => this.refreshPresence(), 30000);
        setInterval(() => this.refreshMaintenance(), 60000);
        document.addEventListener('prefs-changed', event => {
            const link = document.getElementById('prefs-link');
            if (link) link.value = Prefs.shareLink();                   // le lien affiché suit toujours les réglages courants
            if (event.detail.key === 'lang') {
                const reopen = this.prefsDialog?.open;
                applyI18n();
                if (reopen) {                                           // le dialogue se retraduit sans se fermer
                    this.openPrefs();
                    this.prefsDialog.querySelector('input[name="lang"]:checked')?.focus();
                }
                this.refreshPresence();
                this.refreshMaintenance();
                if (typeof window.lastDashboardData === 'object' && window.lastDashboardData && typeof updateDashboardDOM === 'function') updateDashboardDOM(window.lastDashboardData);
            }
        });
        document.addEventListener('click', event => {
            const target = event.target.closest('[data-action]');
            const action = target?.dataset.action;
            if (action === 'openPrefs') this.openPrefs();
            else if (action === 'copyPrefsLink') this.copyPrefsLink();
            else if (action === 'testSound') { playAlertSound(true); setTimeout(stopAlertSound, 2500); }
        });
        A11y.init();
    }
};

document.addEventListener('DOMContentLoaded', () => AppUI.init());
