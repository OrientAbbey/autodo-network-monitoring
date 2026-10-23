// AUTODO — Son, voix, notifications système, titre clignotant (S1, S5, S6).
// Tout est DÉSACTIVÉ par défaut ; le visiteur l'active dans ses préférences (ou `?wall=1&sound=1` pour un mur d'écran).
// Les fonctions historiques `initAudioContext`, `playAlertSound`, `stopAlertSound`, `playBellTone` sont conservées.
// Dépend de : common.js, i18n.js, prefs.js.

const SoundState = { context: null, interval: null, oscillators: [], banner: null };

/** Le son est-il autorisé ? Mur d'écran : seulement avec sound=1. Sinon : préférence du visiteur (désactivé par défaut). */
function soundEnabled() {
    return Prefs.wall.active ? Prefs.wall.sound : Prefs.get('sound');
}

function initAudioContext() {
    if (SoundState.context) return;
    try {
        SoundState.context = new (window.AudioContext || window.webkitAudioContext)();
    } catch (error) {
        console.error('Audio context initialization failed:', error);
    }
}

/** Politique d'autoplay des navigateurs : tant que la page n'a reçu aucun geste, le contexte audio reste « suspended ». */
function audioBlockedByBrowser() {
    return !!SoundState.context && SoundState.context.state === 'suspended';
}

function hideSoundBanner() {
    SoundState.banner?.remove();
    SoundState.banner = null;
}

/** S6 — bandeau « cliquez pour activer le son » quand le navigateur bloque l'audio. */
function showSoundBanner() {
    if (SoundState.banner) return;
    SoundState.banner = el('button', { type: 'button', id: 'sound-banner', class: 'sound-banner', role: 'status', dataset: { action: 'enableSound' }, text: t('sound.click_to_enable') });
    document.body.appendChild(SoundState.banner);
}

async function resumeAudio() {
    if (!SoundState.context) return;
    try { await SoundState.context.resume(); } catch (e) { /* refusé : le bandeau reste */ }
    if (!audioBlockedByBrowser()) {
        hideSoundBanner();
        if (SoundState.pendingAlert) playAlertSound();
    }
}

function playAlertSound(force = false) {
    if (!force && !soundEnabled()) return;          // `force` : bouton « tester le son » des préférences
    stopAlertSound();
    initAudioContext();
    if (!SoundState.context) return;
    if (audioBlockedByBrowser()) {
        SoundState.pendingAlert = true;
        SoundState.context.resume().catch(() => { });
        showSoundBanner();
        return;
    }
    SoundState.pendingAlert = false;
    playBellTone();
    SoundState.interval = setInterval(playBellTone, 1000);
}

function stopAlertSound() {
    SoundState.pendingAlert = false;
    if (SoundState.interval) {
        clearInterval(SoundState.interval);
        SoundState.interval = null;
    }
    SoundState.oscillators.forEach(osc => { try { osc.stop(); } catch (e) { /* déjà arrêté */ } });
    SoundState.oscillators = [];
}

function playBellTone() {
    const ctx = SoundState.context;
    if (!ctx) return;
    try {
        const now = ctx.currentTime;
        const duration = 0.8;
        [[523, 0.25], [784, 0.15]].forEach(([frequency, volume]) => {       // do5 + sol5 : timbre de cloche reconnaissable
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.connect(gain);
            gain.connect(ctx.destination);
            osc.frequency.value = frequency;
            osc.type = 'sine';
            gain.gain.setValueAtTime(volume, now);
            gain.gain.exponentialRampToValueAtTime(0.01, now + duration);
            osc.start(now);
            osc.stop(now + duration);
            SoundState.oscillators.push(osc);
        });
    } catch (error) {
        console.error('Bell tone generation failed:', error);
    }
}

// ====== S5 — annonce vocale (Web Speech), désactivée par défaut ======
function speakAlert(text) {
    if (!Prefs.get('voice') || Prefs.wall.active && !Prefs.wall.sound || !('speechSynthesis' in window)) return;
    try {
        window.speechSynthesis.cancel();
        const utterance = new SpeechSynthesisUtterance(text);
        utterance.lang = Prefs.get('lang') === 'en' ? 'en-US' : 'fr-FR';
        window.speechSynthesis.speak(utterance);
    } catch (e) { /* voix indisponible */ }
}

function stopSpeech() {
    try { window.speechSynthesis?.cancel(); } catch (e) { /* ignoré */ }
}

// ====== S6 — notification système et titre clignotant quand l'onglet est en arrière-plan ======
const TitleBlink = {
    timer: null, original: document.title, on: false,
    start(label) {
        if (this.timer) return;
        this.original = document.title;
        this.timer = setInterval(() => { this.on = !this.on; document.title = this.on ? `🚨 ${label}` : this.original; }, 1000);
    },
    stop() {
        if (!this.timer) return;
        clearInterval(this.timer);
        this.timer = null;
        document.title = this.original;
    }
};

function systemNotify(title, body) {
    if (!Prefs.get('notifications') || !('Notification' in window) || Notification.permission !== 'granted' || !document.hidden) return;
    try { new Notification(title, { body, tag: 'autodo-alert' }); } catch (e) { /* ignoré */ }
}

async function requestNotificationPermission() {
    if (!('Notification' in window)) return 'unsupported';
    return Notification.permission === 'default' ? Notification.requestPermission() : Notification.permission;
}

/** Point d'entrée unique de AlertManager : tous les canaux d'alerte, chacun selon les préférences du visiteur. */
const AlertChannels = {
    raise(title, count) {
        playAlertSound();
        speakAlert(title);
        systemNotify(t('alert.new'), title);
        if (document.hidden) TitleBlink.start(t('alert.title_blink', { count }));
    },
    clear() {
        stopAlertSound();
        stopSpeech();
        TitleBlink.stop();
    }
};

document.addEventListener('visibilitychange', () => { if (!document.hidden) TitleBlink.stop(); });
document.addEventListener('click', event => {
    if (event.target.closest('[data-action="enableSound"]')) resumeAudio();
});
['pointerdown', 'keydown'].forEach(name => document.addEventListener(name, () => { if (SoundState.context && audioBlockedByBrowser()) resumeAudio(); }, { once: false, passive: true }));
document.addEventListener('prefs-changed', event => {
    if (event.detail.key === 'sound' && !event.detail.value) { stopAlertSound(); hideSoundBanner(); }
    if (event.detail.key === 'notifications' && event.detail.value) requestNotificationPermission();
});
