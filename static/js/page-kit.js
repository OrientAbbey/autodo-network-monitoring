// AUTODO — Briques communes aux pages /supervision et /admin : onglets ARIA, tableaux, formulaires, confirmation, graphique SVG.
// Tout le DOM est construit avec el() (jamais innerHTML). Dépend de : common.js, i18n.js.

const PageKit = {
    /** Exécute `fn` en affichant l'erreur dans un toast ; retourne undefined si elle échoue. */
    async guard(fn) {
        try {
            return await fn();
        } catch (e) {
            Toasts.show(e.message || String(e), { type: 'error' });
            return undefined;
        }
    },

    /**
     * Onglets accessibles (rôles tablist/tab/tabpanel, flèches gauche/droite). `definitions` : [{ id, label(): string, load(panel) }].
     * `load` est rappelée à chaque affichage de l'onglet (les données sont donc toujours fraîches).
     */
    tabs(host, definitions) {
        const list = el('div', { class: 'tab-list', role: 'tablist' });
        const panels = el('div', { class: 'tab-panels' });
        const entries = definitions.map(def => {
            const tab = el('button', { type: 'button', class: 'kit-tab', role: 'tab', id: `tab-${def.id}`, 'aria-controls': `panel-${def.id}`, 'aria-selected': 'false', tabindex: '-1', text: def.label() });
            const panel = el('div', { class: 'tab-panel', role: 'tabpanel', id: `panel-${def.id}`, 'aria-labelledby': `tab-${def.id}`, hidden: true });
            list.appendChild(tab);
            panels.appendChild(panel);
            return { def, tab, panel };
        });
        const select = id => {
            entries.forEach(({ def, tab, panel }) => {
                const active = def.id === id;
                tab.setAttribute('aria-selected', String(active));
                tab.tabIndex = active ? 0 : -1;
                tab.classList.toggle('active', active);
                panel.hidden = !active;
                if (active) PageKit.guard(() => def.load(panel));
            });
        };
        entries.forEach(({ def, tab }, index) => {
            tab.addEventListener('click', () => select(def.id));
            tab.addEventListener('keydown', event => {
                const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
                if (!step) return;
                const next = entries[(index + step + entries.length) % entries.length];
                next.tab.focus();
                select(next.def.id);
            });
        });
        host.appendChild(list);
        host.appendChild(panels);
        document.addEventListener('prefs-changed', event => {
            if (event.detail.key === 'lang') entries.forEach(({ def, tab }) => { tab.textContent = def.label(); });
        });
        return { select, entries };
    },

    /** Tableau : columns = [{ label, render(row) -> Node|string, class }]. */
    table(columns, rows, { empty = t('common.none'), caption } = {}) {
        if (!rows.length) return el('p', { class: 'hint empty', text: empty });
        return el('div', { class: 'table-scroll' }, el('table', { class: 'detail-table kit-table' },
            caption ? el('caption', { class: 'sr-only', text: caption }) : null,
            el('thead', {}, el('tr', {}, columns.map(c => el('th', { scope: 'col', class: c.class || '', text: c.label })))),
            el('tbody', {}, rows.map(row => el('tr', {}, columns.map(c => el('td', { class: c.class || '' }, c.render(row))))))));
    },

    field(label, input, hint) {
        const id = input.id || `f-${Math.random().toString(36).slice(2, 8)}`;
        input.id = id;
        return el('div', { class: 'form-group' }, el('label', { for: id, text: label }), input, hint ? el('small', { class: 'hint', text: hint }) : null);
    },

    button(label, onClick, { primary = false, danger = false, disabled = false } = {}) {
        return el('button', { type: 'button', class: danger ? 'btn-danger' : primary ? 'btn-primary' : 'btn-secondary', disabled, on: { click: onClick }, text: label });
    },

    /** Dialogue de confirmation : résout true (OK) ou false (annulé / Échap). */
    confirm(message, { okText = t('common.confirm'), danger = false, withText = null } = {}) {
        return new Promise(resolve => {
            const input = withText ? el('input', { type: 'text', id: 'confirm-text', maxlength: '500', 'aria-label': withText, placeholder: withText }) : null;
            const dialog = el('dialog', { class: 'app-dialog', 'aria-label': message },
                el('div', { class: 'dialog-form' }, el('p', { class: 'confirm-message', text: message }), input,
                    el('div', { class: 'dialog-actions' },
                        el('button', { type: 'button', class: 'btn-secondary', on: { click: () => dialog.close('cancel') }, text: t('common.cancel') }),
                        el('button', { type: 'button', class: danger ? 'btn-danger' : 'btn-primary', on: { click: () => dialog.close('ok') }, text: okText }))));
            dialog.addEventListener('close', () => {
                const accepted = dialog.returnValue === 'ok';
                const text = input ? input.value.trim() : '';
                dialog.remove();
                resolve(withText ? (accepted ? text || true : false) : accepted);
            });
            document.body.appendChild(dialog);
            dialog.showModal();
            (input || dialog.querySelector('.btn-primary, .btn-danger')).focus();
        });
    },

    /** Champ de saisie d'un paramètre de configuration d'après son schéma (bool → case, int/float → nombre borné). */
    paramInput(field, value, { placeholder = '' } = {}) {
        if (field.type === 'bool') return el('input', { type: 'checkbox', name: field.name, checked: !!value });
        return el('input', {
            type: 'number', name: field.name, value: value ?? '', placeholder, step: field.type === 'int' ? '1' : 'any',
            min: field.ge ?? field.gt, max: field.le ?? field.lt, inputmode: 'decimal'
        });
    },

    /** Valeur saisie (bool, nombre, ou null si le champ est vide). */
    readParam(input, field) {
        if (field.type === 'bool') return input.checked;
        if (input.value === '') return null;
        return field.type === 'int' ? parseInt(input.value, 10) : parseFloat(input.value);
    },

    badge(text, kind = 'neutral') {
        return el('span', { class: `kit-badge kit-${kind}`, text });
    },

    section(title, ...children) {
        return el('section', { class: 'kit-section' }, el('h3', { text: title }), ...children);
    },

    /** Écart « avant → après » d'un paramètre de configuration. */
    changeList(changes) {
        if (!changes.length) return el('p', { class: 'hint', text: t('config.no_change') });
        return el('ul', { class: 'change-list' }, changes.map(c => el('li', {}, el('strong', { text: describeParam(c.parameter) }), `: ${PageKit.show(c.before)} → ${PageKit.show(c.after)}`)));
    },

    show(value) {
        if (value === null || value === undefined) return '—';
        if (value === true) return t('common.yes');
        if (value === false) return t('common.no');
        return String(value);
    },

    /** Courbe(s) SVG simples : series = [{ values, class }], band = { lower, upper } (même longueur). */
    lineChart(series, { width = 640, height = 160, band = null, label = '' } = {}) {
        const all = series.flatMap(s => s.values).concat(band ? band.upper : []).filter(v => v !== null && v !== undefined);
        if (all.length < 2) return el('p', { class: 'hint', text: t('common.none') });
        const top = Math.max(...all) * 1.05 || 1;
        const length = Math.max(...series.map(s => s.values.length));
        const x = i => (i / (length - 1)) * width;
        const y = v => height - 4 - (v / top) * (height - 8);
        const children = [];
        if (band) {
            const up = band.upper.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`);
            const low = band.lower.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).reverse();
            children.push(svgEl('polygon', { class: 'chart-band', points: [...up, ...low].join(' ') }));
        }
        series.forEach(s => {
            let segment = [];
            const flush = () => { if (segment.length > 1) children.push(svgEl('polyline', { class: `chart-line ${s.class || ''}`, points: segment.join(' ') })); segment = []; };
            s.values.forEach((v, i) => { if (v === null || v === undefined) flush(); else segment.push(`${x(i).toFixed(1)},${y(v).toFixed(1)}`); });
            flush();
        });
        children.push(svgEl('text', { class: 'chart-axis', x: 2, y: 10 }, `${top.toFixed(1)} Gb/s`));
        return svgEl('svg', { class: 'line-chart', viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': label, preserveAspectRatio: 'none' }, svgEl('title', {}, label), ...children);
    },

    /** Porte d'accès d'une page : demande de connexion, droits insuffisants, ou contenu. */
    gate(host, role, build) {
        const refresh = () => {
            const user = Session.user();
            clear(host);
            if (user && Session.can(role)) {
                build(host);
                return;
            }
            host.appendChild(el('div', { class: 'gate', role: 'status' },
                el('h2', { text: !user ? t('gate.login_required') : user.must_change_password ? t('gate.change_password') : t('gate.insufficient') }),
                el('p', { class: 'hint', text: !user ? t('gate.login_hint') : '' }),
                !user ? el('button', { type: 'button', class: 'btn-primary', dataset: { action: 'openLogin' }, text: t('auth.login') }) : null));
        };
        document.addEventListener('session-changed', refresh);
        return refresh;
    }
};
