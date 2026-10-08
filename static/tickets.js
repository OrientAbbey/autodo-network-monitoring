// Script extrait du template (CSP : aucun script inline).
        const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
        // State Management
        const State = {
            allTickets: [],
            filteredTickets: [],
            currentCriteria: {
                alert_pertes_threshold: 3.0  // Default value to prevent errors
            },
            filtersVisible: false
        };

        // Utility Functions
        const Utils = {
            formatDate: (dateString) => new Date(dateString).toLocaleString(),

            getDateRange: (period) => {
                const now = new Date();
                const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
                const ranges = {
                    today: { start: today, end: new Date(today.getTime() + 86400000) },
                    yesterday: { start: new Date(today.getTime() - 86400000), end: today },
                    week: { start: new Date(today.getTime() - 604800000), end: new Date(today.getTime() + 86400000) },
                    month: { start: new Date(today.getTime() - 2592000000), end: new Date(today.getTime() + 86400000) },
                    current_month: { start: new Date(now.getFullYear(), now.getMonth(), 1), end: new Date(now.getFullYear(), now.getMonth() + 1, 1) }
                };
                return ranges[period] || null;
            },

            formatTTR: (seconds) => {
                const days = Math.floor(seconds / 86400);
                const hours = Math.floor((seconds % 86400) / 3600);
                const minutes = Math.floor((seconds % 3600) / 60);
                if (days > 0) return `${days}d ${hours}h ${minutes}m`;
                if (hours > 0) return `${hours}h ${minutes}m`;
                return `${minutes}m`;
            }
        };

        // Data Management
        const Data = {
            fetch: async () => {
                try {
                    document.getElementById('resultsCount').textContent = 'Loading tickets...';
                    const response = await fetch('/tickets/api');
                    const payload = await response.json();
                    State.allTickets = Array.isArray(payload) ? payload : payload.tickets;
                    State.totalTickets = Array.isArray(payload) ? payload.length : payload.total;
                    if (document.getElementById('timeFilter').value === 'all' && !document.getElementById('idSearch').value) {
                        document.getElementById('timeFilter').value = 'current_month';
                    }
                    Filters.apply();
                } catch (error) {
                    console.error('Error:', error);
                    document.getElementById('resultsCount').textContent = 'Error loading tickets';
                }
            }
        };

        // Filter Management
        const Filters = {
            apply: () => {
                const filters = {
                    id: document.getElementById('idSearch').value.toLowerCase().trim(),
                    type: document.getElementById('typeFilter').value,
                    site: document.getElementById('siteFilter').value,
                    database: document.getElementById('databaseFilter').value,
                    status: document.getElementById('statusFilter').value,
                    time: document.getElementById('timeFilter').value
                };

                State.filteredTickets = State.allTickets.filter(ticket => {
                    if (filters.id && !ticket.id.toString().toLowerCase().includes(filters.id)) return false;
                    if (filters.type && ticket.type !== filters.type) return false;
                    if (filters.site && ticket.site !== filters.site) return false;
                    if (filters.database && ticket.database_name !== filters.database) return false;
                    if (filters.status && ticket.status !== filters.status) return false;
                    if (filters.time !== 'all') {
                        const dateRange = Utils.getDateRange(filters.time);
                        if (dateRange) {
                            const ticketDate = new Date(ticket.timestamp);
                            if (ticketDate < dateRange.start || ticketDate >= dateRange.end) return false;
                        }
                    }
                    return true;
                });

                UI.displayTickets();
                UI.showActiveFilters(filters);
            },

            clear: () => {
                document.getElementById('idSearch').value = '';
                ['typeFilter', 'siteFilter', 'databaseFilter', 'statusFilter'].forEach(id => {
                    document.getElementById(id).value = '';
                });
                document.getElementById('timeFilter').value = 'current_month';
                Filters.apply();
            }
        };

        // UI Management
        const UI = {
            toggleFilters: () => {
                const container = document.getElementById('filters-container');
                const arrow = document.getElementById('filter-arrow');
                State.filtersVisible = !State.filtersVisible;
                container.classList.toggle('show', State.filtersVisible);
                arrow.textContent = State.filtersVisible ? '▲' : '▼';
            },

            createTicketCard: (ticket) => {
                const hasPertes = ticket.type === 'traffic' && ticket.pertes_warnings?.length > 0;
                const slicedValues = ticket.last_values?.slice(1) || [];

                return `
                    <div class="ticket-card">
                        <div class="ticket-header">
                            <div>
                                <span class="ticket-type">${esc(ticket.type)}</span>
                                <span class="ticket-id">#${esc(ticket.id)}</span>
                                ${hasPertes ? '<span class="pertes-indicator">Pertes: High</span>' : ''}
                            </div>
                            <span class="ticket-status status-${esc(ticket.status)}">${esc(ticket.status)}</span>
                        </div>
                        <div class="ticket-info">
                            <div class="info-row"><span class="info-label">Agent:</span><span>${esc(ticket.agent || '-')}</span></div>
                            <div class="info-row"><span class="info-label">Severity:</span><span>${esc(ticket.severity || '-')}</span></div>
                            <div class="info-row"><span class="info-label">Orange Ticket:</span><span style="color: var(--light-orange); font-weight: bold;">${esc(ticket.orange_ticket_id || ticket.external_api_ticket_id || 'N/A')}</span></div>
                            <div class="info-row"><span class="info-label">Site:</span><span>${esc(ticket.site || '-')}</span></div>
                            <div class="info-row"><span class="info-label">Database:</span><span>${esc(ticket.database_name || '-')}</span></div>
                            <div class="info-row"><span class="info-label">Timestamp:</span><span>${ticket.timestamp ? Utils.formatDate(ticket.timestamp) : '-'}</span></div>
                            <div class="info-row"><span class="info-label">Value:</span><span>${ticket.value !== undefined ? `${ticket.value}${ticket.type === 'pertes' ? ' %' : ticket.type === 'traffic' ? ' Gb/s' : ticket.type === 'db_down' ? ' min' : ''}` : '-'}</span></div>
                            ${ticket.type === 'traffic' ? `<div class="info-row"><span class="info-label">Pertes:</span><span style="color: ${ticket.pertes === 'Yes' ? '#ff4444' : '#4CAF50'}; font-weight: bold;">${esc(ticket.pertes || 'No')}</span></div>` : ''}
                            <div class="info-row"><span>${esc(ticket.reason || '-')}</span></div>
                            ${ticket.status === 'closed' && ticket.closure_timestamp ? `
                                <div class="info-row"><span class="info-label">Closed At:</span><span>${Utils.formatDate(ticket.closure_timestamp)}</span></div>
                                ${ticket.human_readable ? `<div class="info-row"><span class="info-label">TTR:</span><span style="color: var(--light-orange); font-weight: bold;">${esc(ticket.human_readable)}</span></div>` : ''}
                            ` : ''}
                            ${slicedValues.length > 0 && ticket.type === 'traffic' ? `
                                <div class="intervals">
                                    <div class="info-label">Traffic Values:</div>
                                    ${slicedValues.map(v => `<div class="interval-item">${esc(v.timestamp.slice(0, 16))} | ${esc(v.value)} Gb/s</div>`).join('')}
                                </div>
                            ` : ''}
                            ${ticket.type === 'pertes' && slicedValues.length > 0 ? `
                                <div class="intervals">
                                    <div class="info-label">Previous Pertes Values:</div>
                                    ${slicedValues.map(v => `<div class="interval-item" style="color: ${Number(v.value) > State.currentCriteria.alert_pertes_threshold ? '#ff6b6b' : '#4CAF50'}">${esc(v.timestamp.slice(0, 16))} | ${esc(v.value)}%</div>`).join('')}
                                </div>
                            ` : ''}
                            ${hasPertes ? `
                                <div class="pertes-warnings">
                                    <div class="info-label" style="color: #ff6b6b; font-weight: bold;">⚠️ High Packet Loss Detected:</div>
                                    ${ticket.pertes_warnings.map(w => `<div class="pertes-warning-item"><span style="color: #ff6b6b; font-size: 0.9em;">${w.timestamp} - ${w.loss_percentage}% packet loss</span></div>`).join('')}
                                </div>
                            ` : ''}
                        </div>
                    </div>
                `;
            },

            displayTickets: () => {
                const sorted = [...State.filteredTickets].sort((a, b) => {
                    if (a.status === 'open' && b.status === 'closed') return -1;
                    if (a.status === 'closed' && b.status === 'open') return 1;
                    return new Date(b.timestamp) - new Date(a.timestamp);
                });

                const container = document.getElementById('tickets-container');
                document.getElementById('resultsCount').textContent = `Showing ${sorted.length} of ${State.allTickets.length} tickets`;

                if (sorted.length === 0) {
                    container.innerHTML = '<div style="grid-column: 1/-1; text-align: center; color: var(--light-orange); font-size: 1.2em; padding: 40px;">No tickets found</div>';
                } else {
                    container.innerHTML = sorted.map(t => UI.createTicketCard(t)).join('');
                }

                UI.updateTTRSummary(sorted);
            },

            updateTTRSummary: (tickets) => {
                const closed = tickets.filter(t => t.status === 'closed' && t.ttr_seconds);
                const summary = document.getElementById('ttrSummary');
                if (closed.length === 0) {
                    summary.textContent = '';
                    return;
                }
                const avgTTR = closed.reduce((sum, t) => sum + t.ttr.seconds, 0) / closed.length;
                summary.textContent = `Avg TTR: ${Utils.formatTTR(avgTTR)} (${closed.length} closed tickets)`;
            },

            showActiveFilters: (filters) => {
                const active = [];
                if (filters.id) active.push(`ID: <b>${filters.id}</b>`);
                if (filters.type) active.push(`Type: <b>${filters.type}</b>`);
                if (filters.site) active.push(`Site: <b>${filters.site}</b>`);
                if (filters.database) active.push(`Database: <b>${filters.database}</b>`);
                if (filters.status) active.push(`Status: <b>${filters.status}</b>`);
                if (filters.time && filters.time !== 'all') {
                    const labels = { today: "Today", yesterday: "Yesterday", week: "Last 7 Days", month: "Last 30 Days", current_month: "Current Month" };
                    active.push(`Time: <b>${labels[filters.time]}</b>`);
                }
                document.getElementById('activeFilters').innerHTML = active.length > 0 ? `Active Filters: ${active.join(' | ')}` : '';
            },

            openSummaryTable: () => window.location.href = '/'
        };

        // Load criteria on page load for filtering purposes only
        const loadCriteriaForFiltering = async () => {
            try {
                const r = await fetch('/api/config');
                if (r.ok) {
                    const response = await r.json();
                    try {
                        const criteria = response.data;
                        // Merge with defaults to ensure all properties exist
                        State.currentCriteria = {
                            alert_pertes_threshold: 3.0,
                            ...criteria
                        };
                    } catch (parseError) {
                        console.error('Invalid JSON response:', response);
                    }
                }
            } catch (error) {
                console.error('Error loading criteria:', error);
            }
        };

        // Event Listeners
        document.addEventListener('DOMContentLoaded', () => {
            // Load criteria on page load for filtering purposes only
            loadCriteriaForFiltering();

            // Filter event listeners
            ['idSearch', 'typeFilter', 'siteFilter', 'databaseFilter', 'statusFilter', 'timeFilter'].forEach(id => {
                const el = document.getElementById(id);
                if (el) {
                    el.addEventListener(id === 'idSearch' ? 'input' : 'change', Filters.apply);
                }
            });

            // Close filters on outside click
            document.addEventListener('click', (e) => {
                const container = document.querySelector('.filters-toggle-container');
                if (container && !container.contains(e.target) && State.filtersVisible) {
                    UI.toggleFilters();
                }
            });

            // Initial load
            Data.fetch();

            // Auto-refresh every 5 minutes
            setInterval(Data.fetch, 300000);
        });
    

document.addEventListener('click', (e) => {
    const el = e.target.closest('[data-action]');
    if (!el) return;
    const actions = {
        toggleFilters: () => UI.toggleFilters(), applyFilters: () => Filters.apply(), clearFilters: () => Filters.clear(),
        goDashboard: () => { window.location.href = '/'; }, refresh: () => Data.fetch(),
    };
    (actions[el.dataset.action] || (() => {}))();
});
