// AUTODO — Tableaux, sections, visibilité, grille
// Script classique (partage la portée globale avec les autres fichiers de static/js/, chargés dans l'ordre de dashboard.html).

// ====== GESTIONNAIRE DES TABLEAUX ======
const TableManager = {
    delegateTbodyClick(tbody, rowClass = 'clickable-row', dataAttr = 'data-link', targetTag = 'TR') {
        if (!tbody) return;

        tbody.onclick = async (e) => {
            let el = e.target;
            while (el && !el.classList.contains(rowClass)) el = el.parentElement;

            if (el && el.classList.contains(rowClass)) {
                let link = el.getAttribute('data-link-total');
                if (!link) {
                    link = el.getAttribute('data-link-pertes');
                }
                if (!link) {
                    link = el.getAttribute('data-link-tcp-service-access');
                }
                if (!link) {
                    link = el.getAttribute('data-link-tcp-tts');
                }
                if (!link) {
                    link = el.getAttribute('data-link-dns');
                }
                if (!link) {
                    link = el.getAttribute(dataAttr);
                }
                if (!link) return;

                const nowTimestamp = Math.floor(Date.now() / 1000);
                const roundedTimestamp = Math.ceil(nowTimestamp / 300) * 300;
                const finalLink = link.includes('__END_TIMESTAMP__')
                    ? link.replace('__END_TIMESTAMP__', roundedTimestamp)
                    : link;

                window.open(finalLink, '_blank', 'noopener');
            }
        };
    }
};


// ====== UTILITAIRES UI ======
const UIUtils = {
    trendColorToCss(colorCode) {
        const colors = { up: 'var(--ok)', down: 'var(--bad)' };
        return colors[colorCode] || 'var(--text-muted)';
    },

    secondsToNextFiveMinute() {
        const now = new Date();
        const currentMinute = now.getMinutes();
        const base = new Date(now.getFullYear(), now.getMonth(), now.getDate(), now.getHours(), 0, 0, 0);

        let targetMinute = currentMinute % 5 >= 3
            ? Math.floor(currentMinute / 5 + 1) * 5 + 2
            : Math.floor(currentMinute / 5) * 5 + 2;

        const next = new Date(base.getTime() + targetMinute * 60 * 1000);
        if (next <= now) next.setMinutes(next.getMinutes() + 5);

        return Math.max(1, Math.ceil((next.getTime() - now.getTime()) / 1000));
    },

    /** Légende : un vrai dialogue (clavier, lecteur d'écran), plus un simple survol à la souris. Les seuils cités sont ceux de la configuration en vigueur. */
    async openHelp() {
        let cfg = {};
        try { cfg = await api('/api/config'); } catch (e) { /* légende sans chiffres */ }
        const line = (key, params) => el('li', { text: t(key, params) });
        const dialog = el('dialog', { class: 'app-dialog wide', id: 'help-dialog', 'aria-labelledby': 'help-title' },
            el('div', { class: 'dialog-form' },
                el('h3', { id: 'help-title', text: t('help.title') }),
                el('h4', { text: t('help.arrows') }), el('ul', {}, line('help.arrow_up'), line('help.arrow_down'), line('help.arrow_flat')),
                el('h4', { text: t('help.traffic') }),
                el('ul', {}, line('help.traffic_red', { instant: cfg.alert_traffic_threshold_percent ?? '…', count: cfg.traffic_consecutive_drops_count ?? '…', first: cfg.traffic_consecutive_drops_min_percent ?? '…' }),
                    line('help.traffic_green', { up: cfg.traffic_up_threshold_percent ?? '…' }), line('help.other_grey')),
                el('h4', { text: t('help.loss') }),
                el('ul', {}, line('help.loss_red', { threshold: cfg.alert_pertes_threshold ?? '…' }), line('help.loss_green'), line('help.other_grey')),
                el('h4', { text: t('help.rows') }), el('ul', {}, line('help.row_alert'), line('help.spark')),
                el('div', { class: 'dialog-actions' }, el('button', { type: 'button', class: 'btn-primary', dataset: { action: 'closeDialog' }, text: t('common.close') }))));
        dialog.addEventListener('close', () => dialog.remove());
        document.body.appendChild(dialog);
        dialog.showModal();
    }
};

// ====== GESTIONNAIRE DES SECTIONS ======
const SectionManager = {
    toggleSection(titleElement) {
        const section = titleElement.closest('.collapsible');
        const content = section.querySelector('.section-content');
        const icon = titleElement.querySelector('.toggle-icon');
        const isCollapsed = section.classList.contains('collapsed');

        if (isCollapsed) {
            section.classList.remove('collapsed');
            content.style.display = 'block';
            icon.textContent = '▼';
            requestAnimationFrame(() => {
                content.style.cssText = 'transition: all 0.3s ease; opacity: 1; transform: translateY(0);';
            });
        } else {
            section.classList.add('collapsed');
            icon.textContent = '▶';
            content.style.cssText = 'transition: all 0.3s ease; opacity: 0; transform: translateY(-10px);';
            setTimeout(() => content.style.display = 'none', 300);
        }
    }
};

// ====== ORDRE DES SECTIONS DE LA GRILLE ======
const GridManager = {
    legacyOrderStorageKey: 'sections_order_v1',      // ancien ordre persisté (glisser-déposer retiré) : purgé au démarrage

    init() {
        const grid = DOM.sectionsGrid;
        if (!grid) return;

        // Appliquer l'ordre canonique demandé: international, gi, international_gi, roaming, tdd
        this.applyOrder(grid, STATE.sections);

        try { localStorage.removeItem(this.legacyOrderStorageKey); } catch (e) { /* stockage indisponible : sans conséquence */ }
    },

    applyOrder(grid, orderArray) {
        const childrenMap = Object.fromEntries([...grid.children].map(c => [c.dataset.sectionId, c]));
        orderArray.forEach(id => { if (childrenMap[id]) grid.appendChild(childrenMap[id]); });
    }
};
