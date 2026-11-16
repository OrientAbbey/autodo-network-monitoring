// AUTODO — Préférences d'affichage, STOCKÉES DANS LE NAVIGATEUR (localStorage) : langue, thème, palette, son, voix, notifications.
// Le visiteur peut tout régler lui-même ; rien n'est envoyé au serveur. Dépend de : common.js.

const Prefs = (() => {
    const KEY = 'autodo.prefs';
    const DEFAULTS = { lang: 'fr', theme: 'dark', palette: 'default', sound: false, voice: false, notifications: false };
    const CHOICES = { lang: ['fr', 'en'], theme: ['dark', 'light'], palette: ['default', 'colorblind'] };
    const SHAREABLE = { l: 'lang', t: 'theme', p: 'palette' };      // JAMAIS le son : un lien ne doit pas déclencher d'alarme chez autrui
    const data = { ...DEFAULTS };

    function valid(key, value) {
        if (!(key in DEFAULTS)) return false;
        return CHOICES[key] ? CHOICES[key].includes(value) : typeof value === 'boolean';
    }

    function load() {
        let stored = {};
        try { stored = JSON.parse(localStorage.getItem(KEY) || '{}') || {}; } catch (e) { stored = {}; }
        const legacyTheme = localStorage.getItem('theme');           // ancienne clé : on la reprend une fois
        if (!stored.theme && CHOICES.theme.includes(legacyTheme)) stored.theme = legacyTheme;
        if (!stored.lang && (navigator.language || '').toLowerCase().startsWith('en')) stored.lang = 'en';
        Object.keys(DEFAULTS).forEach(key => { if (valid(key, stored[key])) data[key] = stored[key]; });
    }

    function save() {
        try {
            localStorage.setItem(KEY, JSON.stringify(data));
            localStorage.setItem('theme', data.theme);               // compatibilité avec l'ancien code
        } catch (e) { /* stockage indisponible : réglages valables pour cette page seulement */ }
    }

    function apply() {
        const root = document.documentElement;
        root.setAttribute('data-theme', data.theme);
        root.setAttribute('data-palette', data.palette);
        root.setAttribute('lang', data.lang);
    }

    function set(key, value) {
        if (!valid(key, value) || data[key] === value) return false;
        data[key] = value;
        save();
        apply();
        document.dispatchEvent(new CustomEvent('prefs-changed', { detail: { key, value } }));
        return true;
    }

    const toBase64Url = text => btoa(String.fromCharCode(...new TextEncoder().encode(text))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
    const fromBase64Url = text => {
        const padded = text.replace(/-/g, '+').replace(/_/g, '/') + '==='.slice((text.length + 3) % 4);
        return new TextDecoder().decode(Uint8Array.from(atob(padded), c => c.charCodeAt(0)));
    };

    /** Lien partageable : langue + thème + palette, dans le FRAGMENT (jamais envoyé au serveur). */
    function shareLink() {
        const payload = {};
        Object.entries(SHAREABLE).forEach(([short, key]) => { payload[short] = data[key]; });
        return `${location.origin}${location.pathname}#prefs=${toBase64Url(JSON.stringify(payload))}`;
    }

    /** Importe `#prefs=…` (valeurs vérifiées une à une) puis RETIRE le fragment de l'URL. Retourne true si quelque chose a été appliqué. */
    function importFromHash() {
        const match = /(?:^#|&)prefs=([A-Za-z0-9_-]+)/.exec(location.hash);
        if (!match) return false;
        let applied = false;
        try {
            const payload = JSON.parse(fromBase64Url(match[1]));
            Object.entries(SHAREABLE).forEach(([short, key]) => { if (valid(key, payload[short])) applied = set(key, payload[short]) || applied; });
        } catch (e) { /* lien invalide : ignoré */ }
        history.replaceState(null, '', location.pathname + location.search);
        return applied;
    }

    // Mur d'écran (?wall=1[&sound=1][&rotate=30]) : lu une fois ; le son n'y est actif qu'avec sound=1.
    const params = new URLSearchParams(location.search);
    const rotateSeconds = Math.max(5, Math.min(600, parseInt(params.get('rotate') || '30', 10) || 30));
    const wall = { active: params.get('wall') === '1', sound: params.get('sound') === '1', rotate: rotateSeconds };

    load();
    apply();
    return { get: key => data[key], set, shareLink, importFromHash, wall, all: () => ({ ...data }), CHOICES };
})();
