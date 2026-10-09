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
        const colors = { up: 'var(--success)', down: 'var(--danger)' };
        return colors[colorCode] || 'var(--gray-light)';
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

    insertHelpIcon() {
        const controls = DOM.controls;
        if (!controls || DOM.helpIcon) return;

        const helpIcon = document.createElement('span');
        helpIcon.id = 'helpIcon';
        helpIcon.style.cssText = 'display:inline-flex;align-items:center;justify-content:center;font-size:1.1em;cursor:pointer;margin-right:8px;';
        helpIcon.innerHTML = `
          <span style="display:inline-flex;align-items:center;justify-content:center;width:28px;height:28px;border-radius:50%;background:var(--dark-light);border:2px solid var(--orange-primary);color:var(--orange-primary);font-weight:bold;font-size:1.2em;box-shadow:0 2px 8px rgba(0,0,0,0.10);user-select:none;">?</span>
        `;

        let tooltipTimeout;

        helpIcon.addEventListener('mouseenter', (e) => {
            clearTimeout(tooltipTimeout);
            this.hideHelpTooltip();
            this.showHelpTooltip(e);
        });

        helpIcon.addEventListener('mouseleave', () => {
            tooltipTimeout = setTimeout(() => this.hideHelpTooltip(), 300);
        });

        document.addEventListener('mouseover', (e) => {
            const isTooltip = e.target.closest('#help-tooltip');
            const isHelpIcon = e.target.closest('#helpIcon');

            if (isTooltip) {
                clearTimeout(tooltipTimeout);
            } else if (!isHelpIcon) {
                this.hideHelpTooltip();
            }
        });

        const refreshBtn = DOM.refreshBtn;
        const autoUpdateBtn = DOM.autoUpdateBtn;
        if (refreshBtn) controls.insertBefore(helpIcon, refreshBtn);
        else if (autoUpdateBtn) controls.insertBefore(helpIcon, autoUpdateBtn);
        else controls.appendChild(helpIcon);
    },

    showHelpTooltip(e) {
        const helpText = `
            <div style="max-width:420px;padding:18px 12px 12px 12px;line-height:1.6;">
                <h3 style="margin-top:0;">Aide : Signification des flèches et couleurs</h3>
                <ul style="margin-left:18px;">
                    <li><b>Flèche ↑</b> : Augmentation de la valeur</li>
                    <li><b>Flèche ↓</b> : Diminution de la valeur</li>
                    <li><b>Flèche ↔</b> : Valeur stable</li>
                </ul>
                <b>Pour le <span style='color:var(--orange-primary)'>traffic</span> :</b>
                <ul style="margin-left:18px;">
                    <li><span style='color:var(--danger)'>Rouge</span> : Baisse ≥ <code>alert_traffic_threshold_percent</code> 
                        <u>ou</u> ≥ <code>traffic_consecutive_drops_count</code> baisses consécutives 
                        d'au moins <code>traffic_consecutive_drops_min_percent</code></li>
                    <li><span style='color:var(--success)'>Vert</span> : Hausse ≥ <code>traffic_up_threshold_percent</code></li>
                    <li><span style='color:var(--gray-light)'>Gris</span> : Autres cas</li>
                </ul>
                <b>Pour les <span style='color:var(--orange-primary)'>pertes</span> :</b>
                <ul style="margin-left:18px;">
                    <li><span style='color:var(--danger)'>Rouge</span> : Valeur courante &gt; <code>alert_pertes_threshold</code></li>
                    <li><span style='color:var(--success)'>Vert</span> : Valeur courante &lt; seuil <u>et</u> en diminution</li>
                    <li><span style='color:var(--gray-light)'>Gris</span> : Autres cas</li>
                </ul>
            </div>`;

        this.hideHelpTooltip();

        const tooltip = document.createElement('div');
        tooltip.id = 'help-tooltip';
        tooltip.style.cssText = `position:fixed;top:${e.clientY + 18}px;left:${e.clientX + 18}px;z-index:10010;background:var(--dark-secondary);border:2px solid var(--orange-primary);border-radius:12px;box-shadow:0 4px 24px rgba(0,0,0,0.25);color:var(--white);`;
        tooltip.innerHTML = helpText;
        tooltip.addEventListener('mouseleave', () => this.hideHelpTooltip());

        document.body.appendChild(tooltip);
    },

    hideHelpTooltip() {
        document.querySelectorAll('#help-tooltip').forEach(tooltip => tooltip.remove());
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

// ====== VISIBILITÉ DES SECTIONS ======
const SectionVisibility = {
    // Applique la visibilité selon l'objet de config reçu du backend
    applyFromConfig(cfg) {
        const map = {
            international: cfg.alerts_enable_international ?? true,
            gi: cfg.alerts_enable_gi ?? true,
            international_gi: cfg.alerts_enable_international_gi ?? false,
            roaming: cfg.alerts_enable_roaming ?? false,
            tdd: cfg.alerts_enable_tdd ?? false
        };
        this.apply(map);
    },

    // Lit l'état des cases à cocher du panneau et applique
    applyFromCheckboxes() {
        const map = {
            international: !!document.getElementById('alerts_enable_international')?.checked,
            gi: !!document.getElementById('alerts_enable_gi')?.checked,
            international_gi: !!document.getElementById('alerts_enable_international_gi')?.checked,
            roaming: !!document.getElementById('alerts_enable_roaming')?.checked,
            tdd: !!document.getElementById('alerts_enable_tdd')?.checked
        };
        this.apply(map);
    },

    // Applique une map {sectionId: visibleBool}
    apply(visibilityMap) {
        STATE.sections.forEach(sectionId => {
            const visible = visibilityMap[sectionId] !== false; // défaut à true
            const sectionEl = document.getElementById(`section-${sectionId}`);
            if (!sectionEl) return;
            sectionEl.style.display = visible ? '' : 'none';

            // Gérer aussi la sous-section des alertes liée
            const alertSection = document.getElementById(`section-alertes-${sectionId}`);
            if (alertSection) {
                alertSection.style.display = visible ? '' : 'none';
            }
        });
    }
};


// ====== GRILLE ET GLISSER-DÉPOSER DES SECTIONS ======
const GridManager = {
    dragSrcEl: null,
    orderKey: 'sections_order_v1',

    init() {
        const grid = DOM.sectionsGrid;
        if (!grid) return;

        // Appliquer l'ordre canonique demandé: international, gi, international_gi, roaming, tdd
        this.applyOrder(grid, STATE.sections);

        // Supprimer tout ordre précédent persisté pour éviter la réapplication
        try { localStorage.removeItem(this.orderKey); } catch (e) { /* ignore */ }

        // Drag-and-drop désactivé
    },

    getDragAfterElement(container, y, x) {
        const draggableElements = [...container.querySelectorAll('section.data-section:not(.dragging)')];
        // Trouver l'élément après lequel insérer en se basant sur Y (et X pour grille)
        return draggableElements.reduce((closest, child) => {
            const box = child.getBoundingClientRect();
            const offset = y - (box.top + box.height / 2);
            if (offset < 0 && offset > closest.offset) {
                return { offset, element: child };
            } else {
                return closest;
            }
        }, { offset: Number.NEGATIVE_INFINITY }).element;
    },

    saveCurrentOrder(grid) {
        const order = [...grid.querySelectorAll('section.data-section')].map(s => s.dataset.sectionId);
        try { localStorage.setItem(this.orderKey, JSON.stringify(order)); } catch (e) { /* ignore */ }
    },

    restoreOrder(grid) {
        let order = null;
        try { order = JSON.parse(localStorage.getItem(this.orderKey) || 'null'); } catch (e) { order = null; }
        if (!order || !Array.isArray(order) || order.length === 0) return;

        const map = Object.fromEntries([...grid.children].map(c => [c.dataset.sectionId, c]));
        order.forEach(id => { if (map[id]) grid.appendChild(map[id]); });
    },

    applyOrder(grid, orderArray) {
        const childrenMap = Object.fromEntries([...grid.children].map(c => [c.dataset.sectionId, c]));
        orderArray.forEach(id => { if (childrenMap[id]) grid.appendChild(childrenMap[id]); });
    }
};
