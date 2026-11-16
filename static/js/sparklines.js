// AUTODO — Mini-courbes 24 h (U3) : tendance du trafic + bande « attendu » (±15 %) dans la 1ʳᵉ cellule de chaque ligne `tr[data-db]`.
// Données : GET /api/sparklines (96 points de 15 min, mis en cache côté serveur 60 s). Dessin : SVG construit avec svgEl().
// Dépend de : common.js, i18n.js. Réagit à l'évènement `dashboard-updated` (les tableaux sont redessinés à chaque mise à jour).

const Sparklines = {
    data: null,
    fetchedAt: 0,
    loading: false,
    WIDTH: 96,
    HEIGHT: 22,
    BAND: 0.15,

    async load() {
        if (this.loading || (this.data && Date.now() - this.fetchedAt < 55000)) return;
        this.loading = true;
        try {
            this.data = (await api('/api/sparklines')).bases;
            this.fetchedAt = Date.now();
        } catch (e) { /* pas d'historique encore : aucune courbe */ }
        this.loading = false;
    },

    /** Construit le SVG d'une base, ou null s'il n'y a pas assez de points. */
    build(db, series) {
        const values = series.values;
        const present = values.filter(v => v !== null);
        if (present.length < 4) return null;
        const expected = series.expected;
        const top = Math.max(...present, ...expected.filter(v => v !== null).map(v => v * (1 + this.BAND)), 0.001);
        const x = i => (i / (values.length - 1)) * this.WIDTH;
        const y = v => this.HEIGHT - 2 - (v / top) * (this.HEIGHT - 4);
        const children = [];

        // Bande « attendu » : polygone entre attendu×0,85 et attendu×1,15 sur les créneaux connus
        const known = expected.map((v, i) => (v === null ? null : i)).filter(i => i !== null);
        if (known.length > 1) {
            const upper = known.map(i => `${x(i).toFixed(1)},${y(expected[i] * (1 + this.BAND)).toFixed(1)}`);
            const lower = known.slice().reverse().map(i => `${x(i).toFixed(1)},${y(expected[i] * (1 - this.BAND)).toFixed(1)}`);
            children.push(svgEl('polygon', { class: 'spark-band', points: [...upper, ...lower].join(' ') }));
        }

        // Courbe réelle : un segment par suite de points consécutifs (les trous ne sont pas reliés)
        let segment = [];
        const flush = () => { if (segment.length > 1) children.push(svgEl('polyline', { class: 'spark-line', points: segment.join(' ') })); segment = []; };
        values.forEach((v, i) => { if (v === null) flush(); else segment.push(`${x(i).toFixed(1)},${y(v).toFixed(1)}`); });
        flush();

        const last = values.length - 1;
        const lastValue = values[last];
        const lastExpected = expected[last];
        const outside = lastValue !== null && lastExpected !== null && lastValue < lastExpected * (1 - this.BAND);
        if (lastValue !== null) children.push(svgEl('circle', { class: outside ? 'spark-dot spark-low' : 'spark-dot', cx: x(last).toFixed(1), cy: y(lastValue).toFixed(1), r: 2 }));

        const description = lastValue === null ? t('spark.no_data')
            : t('spark.title', { now: formatNumber(lastValue, 2), expected: lastExpected === null ? '—' : formatNumber(lastExpected, 2) });
        return svgEl('svg', { class: 'sparkline' + (outside ? ' spark-alert' : ''), viewBox: `0 0 ${this.WIDTH} ${this.HEIGHT}`, width: this.WIDTH, height: this.HEIGHT,
            role: 'img', 'aria-label': `${db}: ${description}` }, svgEl('title', {}, description), ...children);
    },

    render() {
        if (!this.data) return;
        $$('tr[data-db]').forEach(row => {
            const cell = row.querySelector('.location-cell');
            const series = this.data[row.dataset.db];
            if (!cell || !series || cell.querySelector('.sparkline')) return;
            const svg = this.build(row.dataset.db, series);
            if (svg) cell.appendChild(svg);
        });
        document.dispatchEvent(new CustomEvent('sparklines-ready'));
    },

    async refresh() {
        await this.load();
        this.render();
    }
};

document.addEventListener('dashboard-updated', () => Sparklines.refresh());
