// AUTODO — Cartes mobiles (U1) : sous 640 px, une carte par base remplace les larges tableaux (`#mobile-cards`).
// Les cartes sont construites à partir des lignes `tr[data-db]` déjà rendues (mêmes valeurs, aucune requête de plus).
// Dépend de : common.js, i18n.js. Réagit à `dashboard-updated`, `sparklines-ready` et au changement de langue.

const MobileCards = {
    query: window.matchMedia('(max-width: 639px)'),

    /** Colonnes d'un tableau : « Now / 5min / 1D / 1W » par groupe ; on reprend « Now » et « 1D » de chaque groupe utile. */
    cellValue(cells, index) {
        return cells[index]?.querySelector('.value')?.textContent.trim() || '—';
    },

    build() {
        const host = document.getElementById('mobile-cards');
        if (!host) return;
        if (!this.query.matches) { host.hidden = true; clear(host); return; }
        const cards = new Map();
        $$('#sections-grid > .data-section').forEach(section => {
            const sectionName = $('.section-title', section)?.textContent.replace(/[▶▼]/g, '').trim() || '';
            $$('table.data-table', section).forEach(table => {
                const isTcp = /latence|latency/i.test(table.tHead?.textContent || '');
                $$('tbody tr[data-db]', table).forEach(row => {
                    const key = `${section.dataset.sectionId}:${row.dataset.db}`;
                    const cells = Array.from(row.cells);
                    const card = cards.get(key) || { name: cells[0].firstChild?.textContent.trim() || row.dataset.db, db: row.dataset.db, section: sectionName, rows: [], spark: cells[0].querySelector('.sparkline') };
                    if (isTcp) {
                        card.rows.push([t('card.latency'), this.cellValue(cells, 1)], [t('card.tcp_no_response'), this.cellValue(cells, 5)]);
                    } else {
                        card.rows.push([t('card.traffic_now'), this.cellValue(cells, 1)], [t('card.traffic_1d'), this.cellValue(cells, 3)], [t('card.loss_now'), this.cellValue(cells, 5)]);
                    }
                    cards.set(key, card);
                });
            });
        });
        clear(host);
        host.hidden = cards.size === 0;
        cards.forEach(card => host.appendChild(el('article', { class: 'mobile-card', dataset: { db: card.db } },
            el('h3', {}, card.name, el('small', { class: 'card-section', text: card.section })),
            card.spark ? card.spark.cloneNode(true) : null,
            el('dl', {}, card.rows.map(([label, value]) => [el('dt', { text: label }), el('dd', { text: value })])))));
    }
};

['dashboard-updated', 'sparklines-ready'].forEach(name => document.addEventListener(name, () => MobileCards.build()));
document.addEventListener('prefs-changed', event => { if (event.detail.key === 'lang') MobileCards.build(); });
MobileCards.query.addEventListener('change', () => MobileCards.build());
