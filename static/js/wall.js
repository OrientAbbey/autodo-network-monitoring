// AUTODO — Mur d'écran (T4) : `/?wall=1[&sound=1][&rotate=30]`.
// Grandes polices, contrôles masqués, UNE section visible à la fois en rotation (toutes les `rotate` secondes, 30 par défaut).
// Le son n'y est actif qu'avec `sound=1` (voir `soundEnabled()`). Dépend de : common.js, prefs.js.

const Wall = {
    index: 0,
    timer: null,

    sections() {
        return $$('#sections-grid > .data-section');
    },

    show(index) {
        const sections = this.sections();
        if (!sections.length) return;
        this.index = ((index % sections.length) + sections.length) % sections.length;
        sections.forEach((section, i) => {
            section.classList.toggle('wall-hidden', i !== this.index);
            section.classList.remove('collapsed');
        });
        const title = $('.section-title', sections[this.index])?.textContent.replace(/[▶▼]/g, '').trim() || '';
        const label = document.getElementById('wall-title');
        if (label) label.textContent = `${title}  ·  ${this.index + 1}/${sections.length}`;
        document.body.dataset.wallSection = sections[this.index].dataset.sectionId || '';
    },

    init() {
        if (!Prefs.wall.active) return;
        document.body.classList.add('wall');
        document.body.insertBefore(el('div', { id: 'wall-title', class: 'wall-title', role: 'status' }), document.body.firstChild);
        this.show(0);
        this.timer = setInterval(() => this.show(this.index + 1), Prefs.wall.rotate * 1000);
        // Les tableaux se redessinent à chaque cycle : on réapplique la section visible
        document.addEventListener('dashboard-updated', () => this.show(this.index));
    }
};

document.addEventListener('DOMContentLoaded', () => Wall.init());
