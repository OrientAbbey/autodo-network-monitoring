// AUTODO — Session : connexion nominative, widget de compte, changement de mot de passe obligatoire.
// Le visiteur n'a PAS de compte : sans session, `Session.user()` vaut null et le tableau de bord reste consultable.
// Dépend de : common.js, i18n.js, prefs.js. Émet `session-changed` (detail = utilisateur ou null).

const Session = (() => {
    const LEVEL = { supervision: 1, admin: 2 };
    let current = null;
    let loginDialog = null;
    let passwordDialog = null;

    const user = () => current;
    const can = role => !!current && !current.must_change_password && (LEVEL[current.role] || 0) >= LEVEL[role];

    async function load() {
        try {
            current = (await api('/api/me')).user;
        } catch (e) {
            current = null;
        }
        render();
        document.dispatchEvent(new CustomEvent('session-changed', { detail: current }));
        if (current && current.must_change_password) openPasswordChange(true);
        return current;
    }

    // ---------------------------------------------------------------- widget (en-tête)
    function render() {
        const host = document.getElementById('account-widget');
        if (!host) return;
        clear(host);
        if (!current) {
            host.appendChild(el('button', { type: 'button', class: 'btn-secondary', id: 'loginBtn', dataset: { action: 'openLogin' }, text: t('auth.login') }));
            return;
        }
        host.appendChild(el('span', { class: 'account-name', id: 'account-name', text: current.display_name || current.username }));
        host.appendChild(el('span', { class: `role-badge role-${current.role}`, id: 'role-badge', text: t(`role.${current.role}`) }));
        if (can('supervision')) host.appendChild(el('a', { class: 'btn-secondary', href: '/supervision', id: 'nav-supervision', text: t('nav.supervision') }));
        if (can('admin')) host.appendChild(el('a', { class: 'btn-secondary', href: '/admin', id: 'nav-admin', text: t('nav.admin') }));
        host.appendChild(el('button', { type: 'button', class: 'btn-secondary', id: 'logoutBtn', dataset: { action: 'logout' }, text: t('auth.logout') }));
    }

    // ---------------------------------------------------------------- connexion
    function field(id, label, type, extra) {
        return el('div', { class: 'form-group' },
            el('label', { for: id, text: label }),
            el('input', { id, name: id, type, required: true, autocomplete: type === 'password' ? 'current-password' : 'username', ...extra }));
    }

    function buildDialog(id, titleText, fields, submitText, onSubmit, { cancellable = true } = {}) {
        const error = el('p', { class: 'form-error', role: 'alert', id: `${id}-error` });
        const form = el('form', { class: 'dialog-form', id: `${id}-form`, novalidate: true },
            el('h3', { text: titleText }), fields, error,
            el('div', { class: 'dialog-actions' },
                cancellable ? el('button', { type: 'button', class: 'btn-secondary', dataset: { action: 'closeDialog' }, text: t('common.cancel') }) : null,
                el('button', { type: 'submit', class: 'btn-primary', text: submitText })));
        form.addEventListener('submit', async event => {
            event.preventDefault();
            error.textContent = '';
            try { await onSubmit(new FormData(form)); } catch (e) { error.textContent = e.message || String(e); }
        });
        const dialog = el('dialog', { id, class: 'app-dialog', 'aria-labelledby': `${id}-title` }, form);
        form.querySelector('h3').id = `${id}-title`;
        if (!cancellable) dialog.addEventListener('cancel', e => e.preventDefault());       // Échap ne contourne pas un changement obligatoire
        document.body.appendChild(dialog);
        return dialog;
    }

    function openLogin() {
        if (!loginDialog) {
            loginDialog = buildDialog('login-dialog', t('auth.login'), [field('login-username', t('auth.username'), 'text'), field('login-password', t('auth.password'), 'password')],
                t('auth.signin'), async form => {
                    await api('/api/login', { method: 'POST', body: { username: form.get('login-username'), password: form.get('login-password') } });
                    loginDialog.close();
                    loginDialog.querySelector('form').reset();
                    await load();
                    Toasts.show(t('auth.welcome', { name: current.display_name || current.username }), { type: 'success', durationMs: 3000 });
                });
        }
        loginDialog.showModal();
        loginDialog.querySelector('input').focus();
    }

    function openPasswordChange(forced = false) {
        if (passwordDialog) passwordDialog.remove();
        const fields = [
            field('pw-current', t('auth.current_password'), 'password'),
            field('pw-new', t('auth.new_password'), 'password', { autocomplete: 'new-password' }),
            el('p', { class: 'hint', text: t('auth.password_rules') })];
        passwordDialog = buildDialog('password-dialog', forced ? t('auth.change_required') : t('auth.change_password'), fields, t('auth.change_submit'), async form => {
            await api('/api/me/password', { method: 'POST', body: { current_password: form.get('pw-current'), new_password: form.get('pw-new') } });
            passwordDialog.close();
            await load();
            Toasts.show(t('auth.password_changed'), { type: 'success', durationMs: 4000 });
        }, { cancellable: !forced });
        const dialog = passwordDialog;
        dialog.addEventListener('close', () => dialog.remove());          // un dialogue fermé ne reste pas dans le DOM
        dialog.showModal();
        dialog.querySelector('input').focus();
    }

    async function logout() {
        try { await api('/api/logout', { method: 'POST' }); } catch (e) { /* déjà déconnecté */ }
        current = null;
        render();
        document.dispatchEvent(new CustomEvent('session-changed', { detail: null }));
        if (document.body.dataset.requires) location.href = '/';           // pages réservées : retour au tableau de bord
    }

    document.addEventListener('session-expired', () => {
        if (!current) return;
        current = null;
        render();
        document.dispatchEvent(new CustomEvent('session-changed', { detail: null }));
        Toasts.show(t('auth.expired'), { type: 'error' });
    });
    document.addEventListener('prefs-changed', event => { if (event.detail.key === 'lang') render(); });
    document.addEventListener('click', event => {
        const target = event.target.closest('[data-action]');
        if (!target) return;
        const action = target.dataset.action;
        if (action === 'openLogin') openLogin();
        else if (action === 'logout') logout();
        else if (action === 'changePassword') openPasswordChange(false);
        else if (action === 'closeDialog') target.closest('dialog')?.close();
    });

    return { load, user, can, openLogin, openPasswordChange, logout, render };
})();
