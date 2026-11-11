// AUTODO — Outils communs (chargé en PREMIER sur toutes les pages).
// Scripts CLASSIQUES partageant la portée globale : ne JAMAIS redéclarer ailleurs `esc`, `api`, `ApiError`, `el`, `svgEl`, `Toasts`.
// Règle de sécurité : le DOM se construit avec `el()` / `textContent` ; jamais `innerHTML` avec des données.

const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

class ApiError extends Error {
    constructor(status, message) {
        super(message);
        this.name = 'ApiError';
        this.status = status;
    }
}

/** Appel JSON same-origin (cookie de session inclus). Lève ApiError(status, message lisible) si la réponse n'est pas 2xx. */
async function api(url, { method = 'GET', body, headers = {} } = {}) {
    const options = { method, credentials: 'same-origin', headers: { Accept: 'application/json', ...headers } };
    if (body !== undefined) {
        options.headers['Content-Type'] = 'application/json';
        options.body = JSON.stringify(body);
    }
    const response = await fetch(url, options);
    const isJson = (response.headers.get('content-type') || '').includes('json');
    const data = isJson ? await response.json().catch(() => null) : null;
    if (!response.ok) {
        const detail = data && data.detail;
        const message = typeof detail === 'string' ? detail
            : Array.isArray(detail) ? detail.map(d => d.msg).join('; ') : (response.statusText || `HTTP ${response.status}`);
        if (response.status === 401) document.dispatchEvent(new CustomEvent('session-expired'));
        throw new ApiError(response.status, message);
    }
    return data;
}

/**
 * Construit un élément DOM. attrs : class, text, dataset:{}, aria:{}, on:{évènement: fonction}, et tout autre attribut
 * (true → attribut vide ; false/null/undefined → ignoré). `value`, `checked`, `disabled`, `selected` sont posés en propriété.
 * Les enfants peuvent être des chaînes (nœuds texte), des nœuds, des tableaux (aplatis) ou rien.
 */
function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    applyAttrs(node, attrs);
    appendChildren(node, children);
    return node;
}

function applyAttrs(node, attrs) {
    for (const [key, value] of Object.entries(attrs || {})) {
        if (value === false || value === null || value === undefined) continue;
        if (key === 'class') node.className = value;
        else if (key === 'text') node.textContent = value;
        else if (key === 'dataset') Object.entries(value).forEach(([k, v]) => { node.dataset[k] = v; });
        else if (key === 'aria') Object.entries(value).forEach(([k, v]) => node.setAttribute('aria-' + k, v));
        else if (key === 'on') Object.entries(value).forEach(([evt, fn]) => node.addEventListener(evt, fn));
        else if (['value', 'checked', 'disabled', 'selected', 'hidden'].includes(key)) node[key] = value;
        else node.setAttribute(key, value === true ? '' : value);
    }
}

function appendChildren(node, children) {
    for (const child of children.flat(Infinity)) {
        if (child === null || child === undefined || child === false) continue;
        node.appendChild(child instanceof Node ? child : document.createTextNode(String(child)));
    }
}

/** Idem `el` pour le SVG (espace de noms obligatoire). */
function svgEl(tag, attrs, ...children) {
    const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [key, value] of Object.entries(attrs || {})) {
        if (value !== false && value !== null && value !== undefined) node.setAttribute(key, value);
    }
    appendChildren(node, children);
    return node;
}

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
    return node;
}

function formatNumber(value, digits = 2) {
    return value === null || value === undefined || Number.isNaN(Number(value)) ? '—' : Number(value).toFixed(digits);
}

/** Pause aléatoire (gigue) : étale les rechargements de N navigateurs après un évènement serveur. */
function jitter(maxMs) {
    return Math.floor(Math.random() * maxMs);
}

// ====== NOTIFICATIONS À L'ÉCRAN (toasts) — texte seulement, jamais de HTML ======
const Toasts = {
    container: null,
    ensureContainer() {
        if (!this.container || !document.body.contains(this.container)) {
            this.container = document.getElementById('toast-container')
                || document.body.appendChild(el('div', { id: 'toast-container', class: 'toast-container', 'aria-live': 'polite', 'aria-atomic': 'true' }));
        }
        return this.container;
    },
    show(message, { type = 'info', durationMs = 10000 } = {}) {
        const toast = el('div', { class: `toast ${type}`, role: type === 'error' ? 'alert' : 'status' }, el('span', { class: 'toast-message', text: message }));
        this.ensureContainer().appendChild(toast);
        requestAnimationFrame(() => toast.classList.add('visible'));
        const timer = setTimeout(() => this.close(toast), durationMs);
        toast.addEventListener('click', () => { clearTimeout(timer); this.close(toast); });
    },
    close(toast) {
        if (!toast) return;
        toast.classList.remove('visible');
        setTimeout(() => toast.remove(), 300);
    }
};


// ====== MENUS (déroulants d'actions) — contrat HTML :
//   <div class="menu"><button data-menu-toggle aria-haspopup="menu" aria-expanded="false" aria-controls="ID">…</button>
//   <div class="menu-list" id="ID" role="menu" hidden><button class="menu-item" role="menuitem">…</button> …</div></div>
// Clavier : Entrée/Espace/↓ ouvre, ↑↓ Début Fin naviguent, Échap ferme et rend le focus au bouton, Tab ferme.
const Menus = {
    items(list) { return Array.from(list.querySelectorAll('[role="menuitem"]:not([disabled])')).filter(i => !i.hidden); },
    close(menu, restoreFocus = false) {
        const toggle = menu.querySelector('[data-menu-toggle]');
        const list = menu.querySelector('.menu-list');
        if (!list || list.hidden) return;
        list.hidden = true;
        toggle.setAttribute('aria-expanded', 'false');
        if (restoreFocus) toggle.focus();
    },
    closeAll(except = null) {
        document.querySelectorAll('.menu').forEach(m => { if (m !== except) this.close(m); });
    },
    open(menu, focusIndex = 0) {
        this.closeAll(menu);
        const list = menu.querySelector('.menu-list');
        menu.querySelector('[data-menu-toggle]').setAttribute('aria-expanded', 'true');
        list.hidden = false;
        const items = this.items(list);
        if (items.length) items[Math.max(0, Math.min(focusIndex, items.length - 1))].focus();
    }
};

document.addEventListener('click', event => {
    const toggle = event.target.closest('[data-menu-toggle]');
    if (toggle) {
        const menu = toggle.closest('.menu');
        if (menu.querySelector('.menu-list').hidden) Menus.open(menu); else Menus.close(menu, true);
        return;
    }
    const item = event.target.closest('.menu-item');
    if (item) { Menus.close(item.closest('.menu')); return; }          // l'action de l'élément s'exécute (son propre gestionnaire) puis le menu se ferme
    Menus.closeAll();                                                  // clic ailleurs
});

document.addEventListener('keydown', event => {
    const menu = event.target.closest?.('.menu');
    if (!menu) return;
    const list = menu.querySelector('.menu-list');
    const isToggle = event.target.matches('[data-menu-toggle]');
    if (isToggle && ['ArrowDown', 'ArrowUp'].includes(event.key)) { event.preventDefault(); Menus.open(menu, event.key === 'ArrowDown' ? 0 : 99); return; }
    if (list.hidden) return;
    const items = Menus.items(list);
    const at = items.indexOf(document.activeElement);
    const move = { ArrowDown: at + 1, ArrowUp: at - 1, Home: 0, End: items.length - 1 }[event.key];
    if (move !== undefined) { event.preventDefault(); items[(move + items.length) % items.length]?.focus(); }
    else if (event.key === 'Escape') { event.preventDefault(); Menus.close(menu, true); }
    else if (event.key === 'Tab') Menus.close(menu);
});
