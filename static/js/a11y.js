// AUTODO — Accessibilité (U7) : région aria-live, piège à focus de la pop-up, titres de section au clavier, libellés ARIA.
// La palette adaptée au daltonisme est portée par le CSS (`[data-palette="colorblind"]`). Dépend de : common.js, i18n.js.

/** Annonce un message aux lecteurs d'écran (région `aria-live="polite"` visuellement masquée). */
function announce(message) {
    let region = document.getElementById('sr-live');
    if (!region) {
        region = el('div', { id: 'sr-live', class: 'sr-only', role: 'status', 'aria-live': 'polite', 'aria-atomic': 'true' });
        document.body.appendChild(region);
    }
    region.textContent = '';
    setTimeout(() => { region.textContent = message; }, 50);     // le changement de contenu est ce qui déclenche l'annonce
}

const A11y = {
    init() {
        this.makeSectionTitlesKeyboardOperable();
        document.addEventListener('keydown', event => this.trapFocusInAlertPopup(event));
        document.addEventListener('dashboard-updated', () => this.labelTables());
        this.labelTables();
    },

    /** Titres de section : rôle bouton, atteignables au clavier (Tab), Entrée/Espace pour replier, aria-expanded à jour. */
    makeSectionTitlesKeyboardOperable() {
        $$('.section-title[data-action="toggleSection"]').forEach(title => {
            title.setAttribute('role', 'button');
            title.setAttribute('tabindex', '0');
            const section = title.closest('.data-section');
            const sync = () => title.setAttribute('aria-expanded', String(!section.classList.contains('collapsed')));
            sync();
            new MutationObserver(sync).observe(section, { attributes: true, attributeFilter: ['class'] });
        });
        document.addEventListener('keydown', event => {
            const title = event.target.closest?.('.section-title[role="button"]');
            if (title && (event.key === 'Enter' || event.key === ' ')) {
                event.preventDefault();
                title.click();
            }
        });
    },

    /** Pop-up d'alerte : Tab reste à l'intérieur (alertdialog modal). Échap est géré par main.js. */
    trapFocusInAlertPopup(event) {
        const popup = document.getElementById('alert-popup');
        if (event.key !== 'Tab' || !popup || getComputedStyle(popup).display === 'none') return;
        const focusable = $$('button, [href], input, [tabindex]:not([tabindex="-1"])', popup).filter(node => !node.disabled);
        if (!focusable.length) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
        else if (!popup.contains(document.activeElement)) { event.preventDefault(); first.focus(); }
    },

    /** Chaque tableau reçoit un nom accessible (titre de sa section). */
    labelTables() {
        $$('.data-section').forEach(section => {
            const title = $('.section-title', section)?.textContent.replace(/[▶▼]/g, '').trim();
            $$('table.data-table', section).forEach(table => { if (title) table.setAttribute('aria-label', title); });
        });
    }
};
