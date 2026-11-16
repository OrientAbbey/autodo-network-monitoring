// AUTODO — Indice de défilement (U5) : un tableau plus large que son conteneur reçoit un repère (ombre + ligne d'aide),
// pour qu'aucun tableau ne soit coupé sans que l'on sache qu'il défile. Dépend de : common.js, i18n.js.

const TableScroll = {
    update() {
        $$('.table-wrapper').forEach(w => {
            const scrollable = w.offsetParent !== null && w.scrollWidth > w.clientWidth + 2;
            w.toggleAttribute('data-scrollable', scrollable);
            let hint = w.previousElementSibling?.classList.contains('scroll-hint') ? w.previousElementSibling : null;
            if (scrollable && !hint) {
                hint = el('div', { class: 'scroll-hint', 'aria-hidden': 'true', text: t('table.scroll_hint') });
                w.before(hint);
            }
            if (hint) hint.hidden = !scrollable;
        });
    }
};

['dashboard-updated', 'sparklines-ready', 'resize'].forEach(name => (name === 'resize' ? window : document).addEventListener(name, () => requestAnimationFrame(() => TableScroll.update())));
document.addEventListener('prefs-changed', event => { if (event.detail.key === 'lang') $$('.scroll-hint').forEach(h => { h.textContent = t('table.scroll_hint'); }); });
