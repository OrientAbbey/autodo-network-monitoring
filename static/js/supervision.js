// AUTODO — Page /supervision (rôle « supervision », l'admin y a aussi accès) :
// paramètres globaux, seuils par base, maintenance, historique des versions (restauration), validation des tickets.
// Dépend de : common.js, i18n.js, prefs.js, session.js, param-labels.js, page-kit.js.

const Supervision = {
    schema: null,
    overridesDraft: {},

    async loadSchema() {
        this.schema = await api('/api/supervision/config-schema');
        this.overridesDraft = JSON.parse(JSON.stringify(this.schema.overrides || {}));
        return this.schema;
    },

    // ---------------------------------------------------------------- 1. paramètres globaux
    async loadParams(panel, saved = null) {
        const { fields, config } = await this.loadSchema();
        clear(panel);
        const inputs = new Map();
        const form = el('form', { class: 'param-form', id: 'params-form', novalidate: true });
        PARAM_GROUPS.forEach(group => {
            const members = fields.filter(f => (PARAM_META[f.name]?.group) === group);
            if (!members.length) return;
            form.appendChild(el('fieldset', { class: 'param-group' }, el('legend', { text: t(`group.${group}`) }),
                el('div', { class: 'param-grid' }, members.map(field => {
                    const input = PageKit.paramInput(field, config[field.name]);
                    inputs.set(field.name, [input, field]);
                    const unit = paramUnit(field.name);
                    const bounds = field.type === 'bool' ? '' : `${field.ge ?? field.gt ?? ''}…${field.le ?? field.lt ?? ''}`;
                    return el('div', { class: field.type === 'bool' ? 'form-group checkbox-row' : 'form-group' },
                        el('label', { for: `p-${field.name}`, text: paramLabel(field.name) + (unit ? ` (${unit})` : '') }),
                        (input.id = `p-${field.name}`, input.setAttribute('aria-describedby', `h-${field.name}`), input),
                        el('small', { class: 'hint', id: `h-${field.name}`, text: paramHelp(field.name) + (bounds && bounds !== '…' ? ` [${bounds}]` : '') }));
                }))));
        });
        const result = el('div', { id: 'params-result', role: 'status' });
        form.appendChild(el('div', { class: 'form-actions' }, el('button', { type: 'submit', class: 'btn-primary', id: 'saveParamsBtn', text: t('config.save') })));
        form.addEventListener('submit', async event => {
            event.preventDefault();
            const updates = {};
            inputs.forEach(([input, field], name) => {
                const value = PageKit.readParam(input, field);
                if (value !== null && value !== config[name]) updates[name] = value;
            });
            if (!Object.keys(updates).length) { clear(result).appendChild(el('p', { class: 'hint', text: t('config.no_change') })); return; }
            const outcome = await PageKit.guard(() => api('/api/supervision/config', { method: 'POST', body: updates }));
            if (!outcome) return;
            Toasts.show(t('config.saved', { version: outcome.version ?? '—' }), { type: 'success', durationMs: 3000 });
            await this.loadParams(panel, outcome);                          // le formulaire est reconstruit avec les nouvelles valeurs de référence
        });
        if (saved) result.append(el('p', { class: 'success', text: t('config.saved', { version: saved.version ?? '—' }) }), PageKit.changeList(saved.changes));
        panel.appendChild(form);
        panel.appendChild(result);
    },

    // ---------------------------------------------------------------- 2. seuils par base
    async loadOverrides(panel) {
        const { fields, config, databases } = await this.loadSchema();
        const overridable = fields.filter(f => f.overridable);
        clear(panel);
        const select = el('select', { id: 'override-base', 'aria-label': t('overrides.base') },
            databases.map(d => el('option', { value: d.key, text: `${d.label} (${d.key})` })));
        const body = el('div', { id: 'override-body' });
        const summary = el('div', { id: 'override-summary', class: 'chips' });

        const renderSummary = () => {
            clear(summary);
            const keys = Object.keys(this.overridesDraft).filter(k => Object.keys(this.overridesDraft[k] || {}).length);
            summary.appendChild(el('strong', { text: t('overrides.with_custom') + ' ' }));
            summary.append(...(keys.length ? keys.map(k => el('span', { class: 'chip', text: `${k} (${Object.keys(this.overridesDraft[k]).length})` })) : [t('common.none')]));
        };

        const renderBase = () => {
            clear(body);
            const key = select.value;
            const current = this.overridesDraft[key] || {};
            body.appendChild(PageKit.table([
                { label: t('overrides.param'), render: f => paramLabel(f.name) + (paramUnit(f.name) ? ` (${paramUnit(f.name)})` : '') },
                { label: t('overrides.global'), render: f => PageKit.show(config[f.name]) },
                { label: t('overrides.for_base'), render: f => {
                    const input = PageKit.paramInput(f, current[f.name], { placeholder: t('overrides.inherit') });
                    input.id = `o-${f.name}`;
                    input.setAttribute('aria-label', `${paramLabel(f.name)} — ${key}`);
                    input.addEventListener('input', () => {
                        const value = PageKit.readParam(input, f);
                        const draft = this.overridesDraft[key] || (this.overridesDraft[key] = {});
                        if (value === null) delete draft[f.name]; else draft[f.name] = value;
                        if (!Object.keys(draft).length) delete this.overridesDraft[key];
                        renderSummary();
                    });
                    return input;
                } }], overridable, { caption: t('overrides.title') }));
        };

        select.addEventListener('change', renderBase);
        const result = el('div', { id: 'overrides-result', role: 'status' });
        const save = PageKit.button(t('config.save'), async () => {
            const outcome = await PageKit.guard(() => api('/api/supervision/overrides', { method: 'PUT', body: { overrides: this.overridesDraft } }));
            if (!outcome) return;
            clear(result).append(el('p', { class: 'success', text: t('config.saved', { version: outcome.version ?? '—' }) }), PageKit.changeList(outcome.changes));
            this.overridesDraft = JSON.parse(JSON.stringify(outcome.overrides || {}));
            renderSummary();
        }, { primary: true });
        save.id = 'saveOverridesBtn';
        panel.append(el('p', { class: 'hint', text: t('overrides.hint') }), PageKit.field(t('overrides.base'), select), summary, body, el('div', { class: 'form-actions' }, save), result);
        renderBase();
        renderSummary();
    },

    // ---------------------------------------------------------------- 3. maintenance
    async loadMaintenance(panel) {
        const [{ windows }, schema] = await Promise.all([api('/api/supervision/maintenance'), this.schema ? Promise.resolve(this.schema) : this.loadSchema()]);
        clear(panel);
        const base = el('select', { id: 'maint-base' }, el('option', { value: '*', text: t('maintenance.all') }),
            schema.databases.map(d => el('option', { value: d.key, text: `${d.label} (${d.key})` })));
        const soon = new Date(Date.now() + 2 * 3600 * 1000 - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16);
        const ends = el('input', { type: 'datetime-local', id: 'maint-end', value: soon, required: true });
        const reason = el('input', { type: 'text', id: 'maint-reason', maxlength: '200', placeholder: t('maintenance.reason_hint') });
        const form = el('form', { class: 'inline-form', id: 'maintenance-form' },
            PageKit.field(t('maintenance.base'), base), PageKit.field(t('maintenance.until_label'), ends), PageKit.field(t('maintenance.reason'), reason),
            el('button', { type: 'submit', class: 'btn-primary', text: t('maintenance.add') }));
        form.addEventListener('submit', async event => {
            event.preventDefault();
            const end = ends.value.replace('T', ' ') + ':00';
            const done = await PageKit.guard(() => api('/api/supervision/maintenance', { method: 'POST', body: { db_key: base.value, ends_at: end, reason: reason.value } }));
            if (done) { Toasts.show(t('maintenance.added'), { type: 'success', durationMs: 2500 }); this.loadMaintenance(panel); }
        });
        panel.append(el('p', { class: 'hint', text: t('maintenance.hint') }), form, PageKit.section(t('maintenance.current'),
            PageKit.table([
                { label: t('maintenance.base'), render: w => w.db_key === '*' ? t('maintenance.all') : w.db_key },
                { label: t('maintenance.from'), render: w => w.starts_at },
                { label: t('maintenance.until_label'), render: w => w.ends_at },
                { label: t('maintenance.reason'), render: w => (w.auto_silence ? '🔕 ' : '') + (w.reason || '') },
                { label: '', render: w => PageKit.button(t('common.delete'), async () => {
                    if (await PageKit.confirm(t('maintenance.confirm_delete'), { danger: true, okText: t('common.delete') })) {
                        await PageKit.guard(() => api(`/api/supervision/maintenance/${w.id}`, { method: 'DELETE' }));
                        this.loadMaintenance(panel);
                    }
                }, { danger: true }) }], windows, { empty: t('maintenance.none') })));
    },

    // ---------------------------------------------------------------- 4. historique des versions
    async loadVersions(panel) {
        const { versions } = await api('/api/supervision/config-versions');
        clear(panel);
        const detail = el('div', { id: 'version-detail', class: 'kit-section' });
        panel.append(el('p', { class: 'hint', text: t('versions.hint') }), PageKit.table([
            { label: '#', render: v => `v${v.id}` }, { label: t('common.date'), render: v => v.at }, { label: t('versions.author'), render: v => v.actor },
            { label: t('versions.comment'), render: v => v.comment },
            { label: '', render: v => PageKit.button(t('versions.view'), () => PageKit.guard(() => this.showVersion(v.id, detail, panel))) }], versions), detail);
    },

    async showVersion(id, host, panel) {
        const version = await api(`/api/supervision/config-versions/${id}`);
        clear(host);
        host.append(el('h3', { text: t('versions.detail', { id }) }),
            el('p', { class: 'hint', text: t('versions.vs_current') }), PageKit.changeList(version.changes_vs_current.map(c => ({ parameter: c.parameter, before: c.before, after: c.after }))));
        if (version.changes_vs_current.length) {
            host.appendChild(PageKit.button(t('versions.restore'), async () => {
                if (!(await PageKit.confirm(t('versions.confirm_restore', { id }), { okText: t('versions.restore') }))) return;
                const done = await PageKit.guard(() => api(`/api/supervision/config-versions/${id}/restore`, { method: 'POST' }));
                if (done) { Toasts.show(t('versions.restored', { id }), { type: 'success', durationMs: 3000 }); this.loadVersions(panel); }
            }, { primary: true }));
        }
    },

    // ---------------------------------------------------------------- 5. tickets à valider
    async loadValidation(panel) {
        const { tickets } = await api('/api/supervision/tickets-to-validate');
        clear(panel);
        panel.append(el('p', { class: 'hint', text: t('validation.hint') }), PageKit.table([
            { label: 'ID', render: tk => `#${tk.id}` }, { label: t('tickets.type'), render: tk => tk.type }, { label: t('tickets.base'), render: tk => tk.database_name },
            { label: t('common.date'), render: tk => tk.timestamp }, { label: t('tickets.reason'), render: tk => tk.reason || '' },
            { label: '', render: tk => el('div', { class: 'row-actions' },
                PageKit.button(t('validation.approve'), async () => { if (await PageKit.guard(() => api(`/tickets/api/${tk.id}/approve`, { method: 'POST' }))) this.loadValidation(panel); }, { primary: true }),
                PageKit.button(t('validation.reject'), async () => {
                    const reasonText = await PageKit.confirm(t('validation.reject_prompt', { id: tk.id }), { danger: true, okText: t('validation.reject'), withText: t('validation.reason') });
                    if (!reasonText) return;
                    const out = await PageKit.guard(() => api(`/tickets/api/${tk.id}/reject`, { method: 'POST', body: { reason: reasonText === true ? '' : reasonText } }));
                    if (out) { if (out.silence_minutes) Toasts.show(t('validation.silenced', { minutes: out.silence_minutes }), { type: 'info', durationMs: 4000 }); this.loadValidation(panel); }
                }, { danger: true })) }], tickets, { empty: t('validation.none') }));
    },

    init() {
        const host = document.getElementById('app');
        const refresh = PageKit.gate(host, 'supervision', root => {
            root.appendChild(el('p', { class: 'hint who', id: 'who' }, t('page.signed_in_as', { name: Session.user().display_name || Session.user().username, role: t(`role.${Session.user().role}`) })));
            PageKit.tabs(root, [
                { id: 'params', label: () => t('tabs.params'), load: p => this.loadParams(p) },
                { id: 'overrides', label: () => t('tabs.overrides'), load: p => this.loadOverrides(p) },
                { id: 'maintenance', label: () => t('tabs.maintenance'), load: p => this.loadMaintenance(p) },
                { id: 'versions', label: () => t('tabs.versions'), load: p => this.loadVersions(p) },
                { id: 'validation', label: () => t('tabs.validation'), load: p => this.loadValidation(p) }]).select('params');
        });
        document.addEventListener('prefs-changed', event => { if (event.detail.key === 'lang' && Session.can('supervision')) refresh(); });
    }
};

document.addEventListener('DOMContentLoaded', () => Supervision.init());
