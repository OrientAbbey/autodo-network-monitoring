// AUTODO — Thème et filtres (le son est dans sound.js)
// Script classique (partage la portée globale avec les autres fichiers de static/js/, chargés dans l'ordre de dashboard.html).

// ====== THEME (préférence du navigateur : voir prefs.js) ======
function initTheme() {
    updateThemeButton();
}

function toggleTheme() {
    Prefs.set('theme', Prefs.get('theme') === 'dark' ? 'light' : 'dark');
}

function updateThemeButton() {
    const btn = document.getElementById('themeToggleBtn');
    if (btn) {
        const dark = Prefs.get('theme') === 'dark';
        btn.textContent = dark ? '☀️' : '🌙';
        btn.title = dark ? t('theme.to_light') : t('theme.to_dark');
        btn.setAttribute('aria-label', btn.title);
    }
}
document.addEventListener('prefs-changed', event => { if (['theme', 'lang'].includes(event.detail.key)) updateThemeButton(); });

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
