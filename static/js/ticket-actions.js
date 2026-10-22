// AUTODO — Actions sur les tickets (page /tickets) : chronologie (tous), puis — rôle supervision — valider, rejeter, supprimer,
// commenter (avec envoi optionnel à Orange) et retour d'expérience « réel / faux positif ».
// Dépend de : common.js, i18n.js, session.js, page-kit.js.

const TicketActions = {
    async refresh() {
        if (typeof Data !== 'undefined') await Data.fetch();
    },

    async timeline(id) {
        const { events } = await api(`/tickets/api/${id}/timeline`);
        const dialog = el('dialog', { class: 'app-dialog wide', id: 'timeline-dialog', 'aria-labelledby': 'timeline-title' },
            el('div', { class: 'dialog-form' }, el('h3', { id: 'timeline-title', text: t('tickets.timeline_title', { id }) }),
                events.length ? el('ol', { class: 'timeline' }, events.map(e => el('li', { class: `timeline-item kind-${e.kind}` },
                    el('time', { text: e.at }), el('strong', { text: ` ${t(`ev.${e.kind}`)} ` }), el('span', { class: 'hint', text: `· ${e.actor}` }),
                    e.to_orange ? el('span', { class: 'kit-badge kit-neutral', title: t('tickets.sent_orange'), text: e.orange_sent ? t('tickets.sent') : t('tickets.queued') }) : null,
                    el('div', { class: 'timeline-message', text: e.message })))) : el('p', { class: 'hint', text: t('common.none') }),
                el('div', { class: 'dialog-actions' }, el('button', { type: 'button', class: 'btn-primary', dataset: { action: 'closeDialog' }, text: t('common.close') }))));
        dialog.addEventListener('close', () => dialog.remove());
        document.body.appendChild(dialog);
        dialog.showModal();
    },

    async approve(id) {
        if (await PageKit.guard(() => api(`/tickets/api/${id}/approve`, { method: 'POST' }))) {
            Toasts.show(t('validation.approved', { id }), { type: 'success', durationMs: 3000 });
            await this.refresh();
        }
    },

    async remove(id, kind) {
        const prompt = kind === 'reject' ? t('validation.reject_prompt', { id }) : t('tickets.delete_prompt', { id });
        const reason = await PageKit.confirm(prompt, { danger: true, okText: kind === 'reject' ? t('validation.reject') : t('common.delete'), withText: t('validation.reason') });
        if (!reason) return;
        const out = await PageKit.guard(() => api(`/tickets/api/${id}/${kind === 'reject' ? 'reject' : 'delete'}`, { method: 'POST', body: { reason: reason === true ? '' : reason } }));
        if (out) {
            if (out.silence_minutes) Toasts.show(t('validation.silenced', { minutes: out.silence_minutes }), { type: 'info', durationMs: 4000 });
            await this.refresh();
        }
    },

    comment(id) {
        const message = el('textarea', { id: 'comment-text', rows: '4', maxlength: '1500', required: true, 'aria-label': t('tickets.comment') });
        const toOrange = el('input', { type: 'checkbox', id: 'comment-orange' });
        const error = el('p', { class: 'form-error', role: 'alert' });
        const form = el('form', { class: 'dialog-form', novalidate: true }, el('h3', { text: t('tickets.comment_title', { id }) }), message,
            el('label', { class: 'pref-option' }, toOrange, el('span', { text: t('tickets.send_orange') })), error,
            el('div', { class: 'dialog-actions' }, el('button', { type: 'button', class: 'btn-secondary', dataset: { action: 'closeDialog' }, text: t('common.cancel') }),
                el('button', { type: 'submit', class: 'btn-primary', text: t('tickets.add_comment') })));
        const dialog = el('dialog', { class: 'app-dialog', 'aria-label': t('tickets.comment') }, form);
        form.addEventListener('submit', async event => {
            event.preventDefault();
            try {
                await api(`/tickets/api/${id}/comment`, { method: 'POST', body: { message: message.value, send_to_orange: toOrange.checked } });
                dialog.close();
                Toasts.show(t('tickets.comment_added'), { type: 'success', durationMs: 2500 });
            } catch (e) { error.textContent = e.message; }
        });
        dialog.addEventListener('close', () => dialog.remove());
        document.body.appendChild(dialog);
        dialog.showModal();
        message.focus();
    },

    async feedback(id, label) {
        if (await PageKit.guard(() => api(`/tickets/api/${id}/feedback`, { method: 'POST', body: { label } }))) {
            Toasts.show(t('tickets.feedback_saved'), { type: 'success', durationMs: 2500 });
        }
    },

    /** Boutons d'une carte ticket (chaîne HTML : seules des valeurs échappées et des clés de traduction y figurent). */
    buttons(ticket) {
        const id = esc(ticket.id);
        const button = (action, label, cls = 'btn-secondary') => `<button type="button" class="btn ${cls}" data-action="${action}" data-id="${id}">${esc(label)}</button>`;
        const parts = [button('ticketTimeline', `🕒 ${t('tickets.timeline')}`)];
        if (Session.can('supervision')) {
            if (ticket.validation_state === 'pending' && ticket.status === 'open') {
                parts.push(button('ticketApprove', `✔ ${t('validation.approve')}`, 'btn-orange'), button('ticketReject', `✖ ${t('validation.reject')}`));
            } else if (ticket.status !== 'deleted') {
                parts.push(button('ticketDelete', `🗑 ${t('common.delete')}`));
            }
            parts.push(button('ticketComment', `💬 ${t('tickets.comment')}`), button('ticketReal', `👍 ${t('tickets.real')}`), button('ticketFalse', `👎 ${t('tickets.false_positive')}`));
        }
        return parts.join('');
    }
};

document.addEventListener('click', event => {
    const target = event.target.closest('[data-action^="ticket"]');
    if (!target) return;
    const id = target.dataset.id;
    const actions = {
        ticketTimeline: () => PageKit.guard(() => TicketActions.timeline(id)), ticketApprove: () => TicketActions.approve(id),
        ticketReject: () => TicketActions.remove(id, 'reject'), ticketDelete: () => TicketActions.remove(id, 'delete'),
        ticketComment: () => TicketActions.comment(id), ticketReal: () => TicketActions.feedback(id, 'real'), ticketFalse: () => TicketActions.feedback(id, 'false')
    };
    (actions[target.dataset.action] || (() => { }))();
});
