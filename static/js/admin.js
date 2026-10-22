// AUTODO — Page /admin (rôle « admin ») : usage, santé, comptes, sessions, audit, détecteurs « futurs », simulation « et si ? ».
// Dépend de : common.js, i18n.js, prefs.js, session.js, param-labels.js, page-kit.js.

const Admin = {
    schema: null,
    backfillTimer: null,

    kv(object, labels = {}) {
        return el('dl', { class: 'kv' }, Object.entries(object).map(([key, value]) => [
            el('dt', { text: labels[key] || key }), el('dd', { text: typeof value === 'object' && value !== null ? JSON.stringify(value) : PageKit.show(value) })]));
    },

    card(label, value) {
        return el('div', { class: 'stat-card' }, el('div', { class: 'stat-value', text: String(value) }), el('div', { class: 'stat-label', text: label }));
    },

    // ---------------------------------------------------------------- 1. usage
    async loadUsage(panel) {
        const period = panel.dataset.period || '7days';
        const data = await api(`/api/admin/usage?period=${period}`);
        clear(panel);
        const select = el('select', { id: 'usage-period', 'aria-label': t('usage.period') },
            [['today', 'usage.today'], ['7days', 'usage.7days'], ['1month', 'usage.1month'], ['3months', 'usage.3months']].map(([v, k]) => el('option', { value: v, selected: v === period, text: t(k) })));
        select.addEventListener('change', () => { panel.dataset.period = select.value; this.loadUsage(panel); });
        const stats = data.access_stats;
        panel.append(PageKit.field(t('usage.period'), select),
            el('div', { class: 'stat-cards' }, this.card(t('usage.viewers_now'), data.viewers_now), this.card(t('usage.unique_ips'), stats.total_unique_ips),
                this.card(t('usage.avg_daily'), stats.avg_daily_unique), this.card(t('usage.today_unique'), stats.today_unique_count)),
            PageKit.section(t('usage.peaks'), PageKit.table([{ label: t('common.date'), render: p => p.day }, { label: t('usage.peak'), render: p => p.peak_viewers }, { label: t('usage.at'), render: p => p.peak_at }], data.peaks)),
            PageKit.section(t('usage.alerts'), PageKit.table([{ label: t('common.date'), render: ([d]) => d }, { label: 'Traffic', render: ([, v]) => v.traffic },
                { label: 'Pertes', render: ([, v]) => v.pertes }, { label: 'DB_DOWN', render: ([, v]) => v.db_down }, { label: 'Total', render: ([, v]) => v.total }], Object.entries(data.alerts_stats))),
            PageKit.section(t('usage.access'), PageKit.table([{ label: t('common.date'), render: l => l.date }, { label: 'IP', render: l => l.ip_address || '' },
                { label: t('usage.visits'), render: l => l.access_count }, { label: t('usage.users'), render: l => l.unique_users }, { label: t('usage.last'), render: l => l.last_access || '' }], data.access_logs.slice(0, 100))));
    },

    // ---------------------------------------------------------------- 2. santé
    async loadHealth(panel) {
        const [health, history] = await Promise.all([api('/api/admin/health'), api('/api/admin/history')]);
        clear(panel);
        const m = health.monitoring;
        const orange = m.orange || {};
        panel.append(
            el('div', { class: 'stat-cards' }, this.card(t('health.cycles_ok'), m.cycles_ok), this.card(t('health.cycles_failed'), m.cycles_failed),
                this.card(t('health.last_duration'), `${m.last_duration_s}s`), this.card(t('health.uptime'), `${Math.round(m.uptime_s / 60)} min`)),
            PageKit.section(t('health.monitoring'), this.kv({ [t('health.last_ok')]: m.last_ok, [t('health.last_error')]: m.last_error || '—', [t('health.last_stats')]: m.last_stats })),
            PageKit.section(t('health.sources'), PageKit.table([{ label: t('tickets.base'), render: s => s.key },
                { label: t('health.state'), render: s => PageKit.badge(s.failing ? t('health.failing', { n: s.failed_cycles }) : 'OK', s.failing ? 'danger' : 'ok') }], health.sources)),
            PageKit.section(t('health.orange'), this.kv({ ...orange }), this.kv(health.orange_queue)),
            PageKit.section(t('health.tickets'), this.kv(health.tickets)),
            PageKit.section(t('health.jobs'), PageKit.table([{ label: 'Job', render: j => j.id }, { label: t('health.next_run'), render: j => j.next_run || '—' }], health.jobs)),
            PageKit.section(t('health.history'), el('p', { class: 'hint', text: t('health.history_rows', { rows: history.total_rows, days: history.retention_days }) })));
    },

    // ---------------------------------------------------------------- 3. comptes
    async loadUsers(panel) {
        const { users } = await api('/api/admin/users');
        clear(panel);
        const me = Session.user();
        const form = el('form', { class: 'inline-form', id: 'user-form' },
            PageKit.field(t('users.username'), el('input', { type: 'text', id: 'new-username', required: true, autocomplete: 'off', maxlength: '64' })),
            PageKit.field(t('users.display_name'), el('input', { type: 'text', id: 'new-display', maxlength: '100' })),
            PageKit.field(t('users.role'), el('select', { id: 'new-role' }, el('option', { value: 'supervision', text: t('role.supervision') }), el('option', { value: 'admin', text: t('role.admin') }))),
            PageKit.field(t('users.initial_password'), el('input', { type: 'password', id: 'new-password', required: true, autocomplete: 'new-password' }), t('auth.password_rules')),
            el('button', { type: 'submit', class: 'btn-primary', text: t('users.create') }));
        form.addEventListener('submit', async event => {
            event.preventDefault();
            const done = await PageKit.guard(() => api('/api/admin/users', { method: 'POST', body: { username: $('#new-username').value, display_name: $('#new-display').value, role: $('#new-role').value, password: $('#new-password').value } }));
            if (done) { Toasts.show(t('users.created'), { type: 'success', durationMs: 3000 }); this.loadUsers(panel); }
        });
        const patch = async (user, body) => { if (await PageKit.guard(() => api(`/api/admin/users/${user.id}`, { method: 'PATCH', body }))) this.loadUsers(panel); };
        panel.append(PageKit.section(t('users.new'), form), PageKit.section(t('users.list'), PageKit.table([
            { label: t('users.username'), render: u => u.username + (me && me.username === u.username ? ` (${t('users.you')})` : '') },
            { label: t('users.display_name'), render: u => u.display_name },
            { label: t('users.role'), render: u => {
                const select = el('select', { 'aria-label': `${t('users.role')} ${u.username}` }, ['supervision', 'admin'].map(r => el('option', { value: r, selected: r === u.role, text: t(`role.${r}`) })));
                select.addEventListener('change', () => patch(u, { role: select.value }));
                return select;
            } },
            { label: t('users.status'), render: u => PageKit.badge(u.active ? t('users.active') : t('users.disabled'), u.active ? 'ok' : 'danger') },
            { label: t('users.last_login'), render: u => u.last_login_at || '—' },
            { label: '', render: u => el('div', { class: 'row-actions' },
                PageKit.button(u.active ? t('users.disable') : t('users.enable'), () => patch(u, { active: !u.active })),
                PageKit.button(t('users.reset_password'), async () => {
                    const password = await PageKit.confirm(t('users.reset_prompt', { name: u.username }), { okText: t('users.reset_password'), withText: t('users.new_password') });
                    if (!password || password === true) return;
                    const out = await PageKit.guard(() => api(`/api/admin/users/${u.id}/reset-password`, { method: 'POST', body: { new_password: password } }));
                    if (out) Toasts.show(t('users.reset_done', { n: out.sessions_closed }), { type: 'success', durationMs: 4000 });
                })) }], users)));
    },

    // ---------------------------------------------------------------- 4. sessions
    async loadSessions(panel) {
        const { sessions } = await api('/api/admin/sessions');
        clear(panel);
        panel.append(PageKit.table([
            { label: t('users.username'), render: s => s.username + (s.current ? ` (${t('users.you')})` : '') }, { label: t('users.role'), render: s => t(`role.${s.role}`) },
            { label: 'IP', render: s => s.ip }, { label: t('sessions.agent'), render: s => (s.user_agent || '').slice(0, 60) },
            { label: t('sessions.opened'), render: s => s.created_at }, { label: t('sessions.last_seen'), render: s => s.last_seen },
            { label: '', render: s => s.current ? '' : PageKit.button(t('sessions.revoke'), async () => {
                if (await PageKit.guard(() => api(`/api/admin/sessions/${s.session_id}`, { method: 'DELETE' }))) this.loadSessions(panel);
            }, { danger: true }) }], sessions));
    },

    // ---------------------------------------------------------------- 5. audit
    async loadAudit(panel) {
        const days = parseInt(panel.dataset.days || '30', 10);
        const action = panel.dataset.action || '';
        const query = new URLSearchParams({ days });
        if (action) query.set('action', action);
        const { entries } = await api(`/api/admin/audit?${query}`);
        clear(panel);
        const daysInput = el('input', { type: 'number', id: 'audit-days', min: '1', max: '400', value: days });
        const actionInput = el('input', { type: 'text', id: 'audit-action', maxlength: '64', value: action, placeholder: 'ticket_reject, login_failed…' });
        const apply = () => { panel.dataset.days = daysInput.value; panel.dataset.action = actionInput.value.trim(); this.loadAudit(panel); };
        panel.append(el('div', { class: 'inline-form' }, PageKit.field(t('audit.days'), daysInput), PageKit.field(t('audit.action'), actionInput),
            PageKit.button(t('audit.filter'), apply, { primary: true }),
            el('a', { class: 'btn-secondary', id: 'audit-csv', href: `/api/admin/audit.csv?days=${days}`, download: 'autodo-audit.csv', text: t('audit.export') })),
            PageKit.table([{ label: t('common.date'), render: e => e.at }, { label: t('audit.actor'), render: e => e.actor }, { label: 'IP', render: e => e.ip },
                { label: t('audit.action'), render: e => e.action }, { label: t('audit.target'), render: e => e.target },
                { label: t('audit.details'), render: e => e.details ? el('code', { text: JSON.stringify(e.details).slice(0, 200) }) : '' }], entries));
    },

    // ---------------------------------------------------------------- 6. détecteurs « futurs »
    async loadDetectors(panel) {
        clearInterval(this.backfillTimer);
        const [list, history, report] = await Promise.all([api('/api/admin/detectors'), api('/api/admin/history'), api(`/api/admin/detectors/report?days=${panel.dataset.reportDays || 7}`)]);
        if (!this.schema) this.schema = await api('/api/supervision/config-schema');
        clear(panel);

        // --- historique
        const daysInput = el('input', { type: 'number', id: 'backfill-days', min: '1', max: '90', value: '30' });
        const progress = el('div', { id: 'backfill-progress', role: 'status' });
        const showProgress = state => {
            clear(progress);
            if (!state.started_at) return;
            const entries = Object.entries(state.progress || {});
            progress.append(el('p', { class: 'hint', text: state.running ? t('history.running', { done: entries.length }) : t('history.finished', { rows: entries.reduce((n, [, v]) => n + v.rows, 0), errors: entries.filter(([, v]) => v.error).length }) }));
        };
        showProgress(history.backfill);
        const poll = () => { this.backfillTimer = setInterval(async () => {
            const state = (await api('/api/admin/history')).backfill;
            showProgress(state);
            if (!state.running) { clearInterval(this.backfillTimer); this.loadDetectors(panel); }
        }, 2000); };
        if (history.backfill.running) poll();
        panel.append(PageKit.section(t('history.title'), el('p', { class: 'hint', text: t('history.hint') }),
            el('p', { id: 'history-stats', text: t('health.history_rows', { rows: history.total_rows, days: history.retention_days }) }),
            el('div', { class: 'inline-form' }, PageKit.field(t('history.days'), daysInput),
                PageKit.button(t('history.import'), async () => {
                    const out = await PageKit.guard(() => api('/api/admin/history/backfill', { method: 'POST', body: { days: parseInt(daysInput.value, 10) } }));
                    if (out) { Toasts.show(t('history.started'), { type: 'success', durationMs: 3000 }); poll(); }
                }, { primary: true })), progress));

        // --- détecteurs
        panel.appendChild(PageKit.section(t('detectors.title'), el('p', { class: 'hint', text: list.ml_available ? t('detectors.hint') : t('detectors.ml_missing') }),
            PageKit.table([
                { label: t('detectors.detector'), render: d => el('span', {}, el('strong', { text: `${d.code} ` }), d.title) },
                { label: t('detectors.enabled'), render: d => {
                    const box = el('input', { type: 'checkbox', checked: d.enabled, disabled: !d.shadow, 'aria-label': `${d.title}` });
                    box.addEventListener('change', () => PageKit.guard(() => api(`/api/admin/detectors/${d.key}`, { method: 'POST', body: { enabled: box.checked } })));
                    return box;
                } },
                { label: t('detectors.verdicts'), render: d => d.shadow ? d.verdicts_24h : '—' },
                { label: t('detectors.trained'), render: d => !d.needs_training ? '—' : d.available === false ? PageKit.badge(t('detectors.unavailable'), 'danger') : (d.trained_at || t('detectors.never')) },
                { label: '', render: d => !d.needs_training ? el('span', { class: 'hint', text: d.description }) : PageKit.button(t('detectors.train'), async () => {
                    const out = await PageKit.guard(() => api(`/api/admin/detectors/${d.key}/train`, { method: 'POST', body: {} }));
                    if (out) { Toasts.show(t('detectors.trained_ok'), { type: 'success', durationMs: 4000 }); this.loadDetectors(panel); }
                }, { disabled: d.available === false }) }], list.detectors)));

        // --- rapport du mode ombre
        const reportDays = el('select', { id: 'report-days', 'aria-label': t('report.days') }, [1, 7, 30].map(n => el('option', { value: n, selected: String(n) === String(panel.dataset.reportDays || 7), text: t('report.n_days', { n }) })));
        reportDays.addEventListener('change', () => { panel.dataset.reportDays = reportDays.value; this.loadDetectors(panel); });
        const rows = Object.entries(report.detectors);
        panel.appendChild(PageKit.section(t('report.title'), el('p', { class: 'hint', text: t('report.hint') }), reportDays,
            PageKit.table([{ label: t('detectors.detector'), render: ([, r]) => `${r.code} ${r.title}` }, { label: t('report.episodes'), render: ([, r]) => r.episodes },
                { label: t('report.confirmed'), render: ([, r]) => PageKit.show(r.confirmed) }, { label: t('report.without_ticket'), render: ([, r]) => PageKit.show(r.without_ticket) },
                { label: t('report.recall'), render: ([, r]) => r.recall === null ? '—' : `${Math.round(r.recall * 100)} % (${r.tickets_covered}/${r.tickets_total})` },
                { label: t('report.lead'), render: ([, r]) => r.median_lead_minutes === null ? '—' : `${r.median_lead_minutes} min` }], rows),
            PageKit.table([{ label: t('detectors.detector'), render: e => e.detector }, { label: t('tickets.base'), render: e => e.db_key }, { label: t('report.start'), render: e => e.start },
                { label: t('report.end'), render: e => e.end }, { label: t('tickets.ticket'), render: e => e.ticket_id ? `#${e.ticket_id}` : '—' }, { label: t('report.explanation'), render: e => e.explanation }],
                report.episodes.slice(0, 50), { empty: t('report.none') })));

        // --- analyses à la demande
        const bases = Object.keys(history.bases);
        const base = el('select', { id: 'analysis-base', 'aria-label': t('overrides.base') }, bases.map(b => el('option', { value: b, text: b })));
        const out = el('div', { id: 'analysis-output', role: 'status' });
        const capacity = el('input', { type: 'number', id: 'analysis-capacity', min: '0.1', step: 'any', value: '10' });
        panel.appendChild(PageKit.section(t('analysis.title'), el('p', { class: 'hint', text: t('analysis.hint') }), PageKit.field(t('overrides.base'), base),
            el('div', { class: 'inline-form' },
                PageKit.button(t('analysis.forecast'), () => PageKit.guard(() => this.runForecast(base.value, out))),
                PageKit.field(t('analysis.capacity'), capacity),
                PageKit.button(t('analysis.capacity_run'), () => PageKit.guard(async () => {
                    const r = await api(`/api/admin/analysis/capacity?db_key=${encodeURIComponent(base.value)}&capacity_gbps=${capacity.value}`);
                    clear(out).append(el('h4', { text: t('analysis.capacity') }), this.kv(r));
                })),
                PageKit.button(t('analysis.shifts'), () => PageKit.guard(async () => {
                    const r = await api(`/api/admin/analysis/level-shifts?db_key=${encodeURIComponent(base.value)}`);
                    clear(out).append(el('h4', { text: t('analysis.shifts') }), r.shifts.length ? el('ul', {}, r.shifts.map(s => el('li', { text: `${s.day} — ${s.explanation}` }))) : el('p', { class: 'hint', text: t('analysis.no_shift', { days: r.days_of_data }) }));
                }))), out));
    },

    async runForecast(dbKey, out) {
        const r = await api(`/api/admin/analysis/forecast?db_key=${encodeURIComponent(dbKey)}&days=14`);
        clear(out).append(el('h4', { text: t('analysis.forecast') }),
            el('p', { class: 'hint', text: t('analysis.forecast_hint', { start: r.forecast_start, std: r.residual_std }) }),
            PageKit.lineChart([{ values: r.expected, class: 'chart-expected' }], { band: { lower: r.lower, upper: r.upper }, label: t('analysis.forecast') }));
    },

    // ---------------------------------------------------------------- 7. simulation « et si ? »
    async loadSimulation(panel) {
        if (!this.schema) this.schema = await api('/api/supervision/config-schema');
        const { fields, config } = this.schema;
        const numeric = fields.filter(f => f.type !== 'bool' && PARAM_META[f.name] && PARAM_META[f.name].group !== 'governance');
        clear(panel);
        const inputs = new Map(numeric.map(f => [f.name, PageKit.paramInput(f, config[f.name])]));
        const days = el('select', { id: 'sim-days', 'aria-label': t('simulation.days') }, [1, 3, 7, 14, 30].map(n => el('option', { value: n, selected: n === 7, text: t('report.n_days', { n }) })));
        const result = el('div', { id: 'sim-result', role: 'status' });
        const run = PageKit.button(t('simulation.run'), async () => {
            const updates = {};
            inputs.forEach((input, name) => { const v = PageKit.readParam(input, fields.find(f => f.name === name)); if (v !== null && v !== config[name]) updates[name] = v; });
            clear(result).appendChild(el('p', { class: 'hint', text: t('simulation.running') }));
            const out = await PageKit.guard(() => api('/api/admin/simulation', { method: 'POST', body: { config: updates, days: parseInt(days.value, 10) } }));
            if (out) this.showSimulation(out, result, Object.keys(updates).length);
        }, { primary: true });
        run.id = 'sim-run';
        panel.append(el('p', { class: 'hint', text: t('simulation.hint') }),
            el('div', { class: 'param-grid' }, numeric.map(f => {
                const input = inputs.get(f.name);
                input.id = `sim-${f.name}`;
                return el('div', { class: 'form-group' }, el('label', { for: input.id, text: paramLabel(f.name) + (paramUnit(f.name) ? ` (${paramUnit(f.name)})` : '') }), input);
            })), el('div', { class: 'inline-form' }, PageKit.field(t('simulation.days'), days), run), result);
    },

    showSimulation(r, host, changed) {
        const sign = n => (n > 0 ? `+${n}` : String(n));
        const alertRow = a => [a.db_key, a.type, a.start, a.reason, a.followed_by_ticket ? t('simulation.had_ticket') : t('simulation.no_ticket')];
        const alertTable = list => PageKit.table([{ label: t('tickets.base'), render: a => a[0] }, { label: t('tickets.type'), render: a => a[1] }, { label: t('report.start'), render: a => a[2] },
            { label: t('tickets.reason'), render: a => a[3] }, { label: t('tickets.ticket'), render: a => a[4] }], list.map(alertRow), { empty: t('common.none') });
        clear(host).append(
            el('p', { class: 'hint', text: t('simulation.period', { from: r.from, to: r.to, bases: r.bases_evaluated }) + (changed ? '' : ` ${t('simulation.unchanged')}`) }),
            el('div', { class: 'stat-cards' }, this.card(t('simulation.current'), r.totals.current.total), this.card(t('simulation.candidate'), r.totals.candidate.total),
                this.card(t('simulation.delta'), sign(r.delta)), this.card(t('simulation.new_no_ticket'), r.new_alerts_without_ticket)),
            PageKit.section(t('simulation.per_base'), PageKit.table([{ label: t('tickets.base'), render: b => b.db_key }, { label: t('simulation.current'), render: b => b.current },
                { label: t('simulation.candidate'), render: b => b.candidate }, { label: t('simulation.delta'), render: b => sign(b.delta) }], r.per_base.filter(b => b.delta !== 0 || b.current > 0))),
            PageKit.section(t('simulation.new_alerts', { n: r.new_alerts_total }), alertTable(r.new_alerts)),
            PageKit.section(t('simulation.removed_alerts', { n: r.removed_alerts_total }), alertTable(r.removed_alerts)));
    },

    init() {
        const host = document.getElementById('app');
        const refresh = PageKit.gate(host, 'admin', root => {
            root.appendChild(el('p', { class: 'hint who', id: 'who' }, t('page.signed_in_as', { name: Session.user().display_name || Session.user().username, role: t(`role.${Session.user().role}`) })));
            PageKit.tabs(root, [
                { id: 'usage', label: () => t('tabs.usage'), load: p => this.loadUsage(p) },
                { id: 'health', label: () => t('tabs.health'), load: p => this.loadHealth(p) },
                { id: 'users', label: () => t('tabs.users'), load: p => this.loadUsers(p) },
                { id: 'sessions', label: () => t('tabs.sessions'), load: p => this.loadSessions(p) },
                { id: 'audit', label: () => t('tabs.audit'), load: p => this.loadAudit(p) },
                { id: 'detectors', label: () => t('tabs.detectors'), load: p => this.loadDetectors(p) },
                { id: 'simulation', label: () => t('tabs.simulation'), load: p => this.loadSimulation(p) }]).select('usage');
        });
        document.addEventListener('prefs-changed', event => { if (event.detail.key === 'lang' && Session.can('admin')) refresh(); });
    }
};

document.addEventListener('DOMContentLoaded', () => Admin.init());
