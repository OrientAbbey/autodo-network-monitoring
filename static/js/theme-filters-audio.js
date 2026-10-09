// AUTODO — Thème, filtres, sons d'alerte
// Script classique (partage la portée globale avec les autres fichiers de static/js/, chargés dans l'ordre de dashboard.html).

// ====== THEME MANAGEMENT ======
function initTheme() {
    document.documentElement.setAttribute('data-theme', STATE.theme);
    updateThemeButton();
}

function toggleTheme() {
    STATE.theme = STATE.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', STATE.theme);
    localStorage.setItem('theme', STATE.theme);
    updateThemeButton();
}

function updateThemeButton() {
    const btn = document.getElementById('themeToggleBtn');
    if (btn) {
        btn.textContent = STATE.theme === 'dark' ? '☀️' : '🌙';
        btn.title = STATE.theme === 'dark' ? 'Switch to Light Mode' : 'Switch to Dark Mode';
    }
}

// Track filter state
let filtersChanged = false;

function showFilterStatus() {
    const statusDiv = document.getElementById('filterStatus');
    if (statusDiv) {
        statusDiv.style.display = 'block';
    }
    filtersChanged = true;
}

function hideFilterStatus() {
    const statusDiv = document.getElementById('filterStatus');
    if (statusDiv) {
        statusDiv.style.display = 'none';
    }
    filtersChanged = false;
}

function toggleFilterSidebar() {
    const sidebar = document.getElementById('filterSidebar');
    const overlay = document.querySelector('.filter-sidebar-overlay');

    if (sidebar) {
        sidebar.classList.toggle('active');
    } else {
        console.error('Sidebar element not found!');
    }

    if (overlay) {
        overlay.classList.toggle('active');
    } else {
        console.error('Overlay element not found!');
    }
}

function applyFilters() {
    try {
        // Refresh dashboard data when filters are applied
        refreshDashboardData();
        // Hide the filter status indicator after applying
        hideFilterStatus();
    } catch (error) {
        console.error('Error in applyFilters:', error);
    }
}

function resetFilters() {
    try {
        // Reset all filter controls to default
        const openStatusFilter = document.querySelector('input[name="statusFilter"][value="open"]');
        const allTypeFilter = document.querySelector('input[name="typeFilter"][value="all"]');
        const monthPeriodFilter = document.querySelector('input[name="periodFilter"][value="month"]');

        if (openStatusFilter) openStatusFilter.checked = true;
        if (allTypeFilter) allTypeFilter.checked = true;
        if (monthPeriodFilter) monthPeriodFilter.checked = true;

        // Uncheck other type filters
        document.querySelectorAll('input[name="typeFilter"]:not([value="all"])').forEach(input => {
            input.checked = false;
        });

        // Show indicator that filters have been changed and need to be applied
        showFilterStatus();
    } catch (error) {
        console.error('Error in resetFilters:', error);
    }
}

// Handle type filter logic (exclusive with "all")
function handleTypeFilterChange(input) {
    try {
        const allTypeFilter = document.querySelector('input[name="typeFilter"][value="all"]');
        const otherTypeFilters = document.querySelectorAll('input[name="typeFilter"]:not([value="all"])');

        if (!allTypeFilter || !otherTypeFilters) {
            console.warn('Type filter elements not found');
            return;
        }

        if (input.value === 'all') {
            if (input.checked) {
                // If "all" is checked, uncheck others
                otherTypeFilters.forEach(filter => filter.checked = false);
            }
        } else {
            if (input.checked) {
                // If any specific type is checked, uncheck "all"
                allTypeFilter.checked = false;
            } else {
                // If no specific types are checked, check "all"
                const anyChecked = Array.from(otherTypeFilters).some(filter => filter.checked);
                if (!anyChecked) {
                    allTypeFilter.checked = true;
                }
            }
        }
    } catch (error) {
        console.error('Error in handleTypeFilterChange:', error);
    }
}

// Initialize Web Audio API context (called once at app start)
function initAudioContext() {
    if (STATE.audioContext) return;
    try {
        STATE.audioContext = new (window.AudioContext || window.webkitAudioContext)();
        if (STATE.audioContext.state === 'suspended') {
            STATE.audioContext.resume();
        }
    } catch (error) {
        console.error('Audio context initialization failed:', error);
    }
}

// Play alert sound with looping bell tones
function playAlertSound() {
    stopAlertSound(); // Stop any existing sound first
    initAudioContext();
    
    if (!STATE.audioContext) return;
    
    try {
        // Play first bell immediately
        playBellTone();
        
        // Schedule repeats every 1 second
        STATE.soundInterval = setInterval(playBellTone, 1000);
    } catch (error) {
        console.warn('Alert sound failed:', error);
    }
}

// Stop all alert sounds
function stopAlertSound() {
    // Stop interval
    if (STATE.soundInterval) {
        clearInterval(STATE.soundInterval);
        STATE.soundInterval = null;
    }
    
    // Stop all active oscillators
    STATE.soundOscillators.forEach(osc => {
        try { osc.stop(); } catch (e) {}
    });
    STATE.soundOscillators = [];
}

// Generate and play a single bell tone (dual frequency for distinctiveness)
function playBellTone() {
    if (!STATE.audioContext) return;
    
    try {
        const ctx = STATE.audioContext;
        const now = ctx.currentTime;
        const duration = 0.8;
        
        // Primary frequency (C5)
        const osc1 = ctx.createOscillator();
        const gain1 = ctx.createGain();
        osc1.connect(gain1);
        gain1.connect(ctx.destination);
        osc1.frequency.value = 523;
        osc1.type = 'sine';
        gain1.gain.setValueAtTime(0.25, now);
        gain1.gain.exponentialRampToValueAtTime(0.01, now + duration);
        osc1.start(now);
        osc1.stop(now + duration);
        STATE.soundOscillators.push(osc1);
        
        // Secondary frequency (G5) for richer tone
        const osc2 = ctx.createOscillator();
        const gain2 = ctx.createGain();
        osc2.connect(gain2);
        gain2.connect(ctx.destination);
        osc2.frequency.value = 784;
        osc2.type = 'sine';
        gain2.gain.setValueAtTime(0.15, now);
        gain2.gain.exponentialRampToValueAtTime(0.01, now + duration);
        osc2.start(now);
        osc2.stop(now + duration);
        STATE.soundOscillators.push(osc2);
    } catch (error) {
        console.error('Bell tone generation failed:', error);
    }
}
