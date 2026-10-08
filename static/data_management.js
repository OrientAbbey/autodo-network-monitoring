// Script extrait du template (CSP : aucun script inline).
        const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
        let accessChart = null, alertsChart = null, isAuthenticated = false, isLoading = false, currentRequest = null;

        // ==================== CHECK EXISTING AUTH ON PAGE LOAD ====================
        window.addEventListener('load', async () => {
            try {
                const response = await fetch('/api/data-management?action=data', {
                    method: 'GET',
                    credentials: 'same-origin'
                });
                if (response.ok) {
                    // Valid cookie, show data management screen directly
                    showDMScreen();
                    loadData();
                }
            } catch (e) {
                // No valid cookie or error, show login screen
            }
        });

        // ==================== LOGIN LOGIC ====================
        document.getElementById('loginForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            const passwordInput = document.getElementById('password');
            const loginBtn = document.getElementById('loginBtn');
            const errorMessage = document.getElementById('errorMessage');
            const successMessage = document.getElementById('successMessage');
            errorMessage.style.display = 'none';
            successMessage.style.display = 'none';
            loginBtn.disabled = true;
            const originalText = loginBtn.textContent;
            loginBtn.innerHTML = '<span class="loading"></span>Connexion...';
            try {
                const response = await fetch('/api/data-management',
                    {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ action: 'login', password: passwordInput.value })
                    });
                const data = await response.json();
                if (response.ok && data.success) {
                    isAuthenticated = true;
                    successMessage.textContent = data.message;
                    successMessage.style.display = 'block';
                    setTimeout(() => {
                        showDMScreen();
                        loadData();
                    }, 500);
                }
                else {
                    throw new Error(data.detail || 'Mot de passe incorrect');
                }
            }
            catch (error) {
                errorMessage.textContent = '❌ ' + (error.message || 'Erreur lors de la connexion');
                errorMessage.style.display = 'block';
                loginBtn.disabled = false;
                loginBtn.textContent = originalText;
                passwordInput.value = '';
                passwordInput.focus();
            }
        });

        function showDMScreen() {
            document.getElementById('loginScreen').classList.add('dm-hidden');
            document.getElementById('dmScreen').classList.remove('dm-hidden');
        }

        function returnToDashboardWithLogout() {
            isAuthenticated = false;
            window.location.href = '/';
        }

        // ==================== DATA MANAGEMENT LOGIC ====================
        function parseDate(d) {
            return d ? new Date(d) : new Date();
        }

        async function loadData() {
            if (isLoading) return; // empêche double appel
            isLoading = true;

            // Annule la requête précédente si elle existe
            if (currentRequest) {
                currentRequest.abort();
            }

            const controller = new AbortController();
            currentRequest = controller;

            const period = document.getElementById('period').value;

            try {
                const res = await fetch(`/api/data-management?action=data&period=${period}`, {
                    signal: controller.signal
                });

                const data = await res.json();

                if (!data || data.error) {
                    console.error("❌ No data or error in response:", data);
                    return;
                }

                updateStats(data);
                updateCharts(data);
                updateTables(data);
            }
            catch (err) {
                if (err.name !== 'AbortError') {
                    console.error("Erreur chargement:", err);
                }
            }
            finally {
                isLoading = false;
            }
        }

        function updateStats(data) {
            const stats = data.access_stats || {}, alerts = data.alerts_stats || {};
            let traffic = 0, pertes = 0, db_down = 0, today_traffic = 0, today_pertes = 0, today_db_down = 0;
            const today = new Date().toISOString().split('T')[0];
            Object.entries(alerts).forEach(([date, day]) => {
                traffic += day.traffic || 0;
                pertes += day.pertes || 0;
                db_down += day.db_down || 0;
                if (date === today) {
                    today_traffic += day.traffic || 0;
                    today_pertes += day.pertes || 0;
                    today_db_down += day.db_down || 0;
                }
            });
            const total = traffic + pertes + db_down;
            const today_total = today_traffic + today_pertes + today_db_down;
            document.getElementById('total-tickets').textContent = total;
            document.getElementById('tickets-breakdown').textContent = `Traffic: ${traffic}, Perte: ${pertes}${db_down > 0 ? `, BD Down: ${db_down}` : ''}`;
            document.getElementById('tickets-today').textContent = `Aujourd'hui: ${today_total} (Traffic: ${today_traffic}, Perte: ${today_pertes}${today_db_down > 0 ? `, BD Down: ${today_db_down}` : ''})`;
            document.getElementById('avg-access').textContent = stats.avg_daily_unique || 0;
            document.getElementById('avg-access-subtext').textContent = `Periode: ${data.period || 0}`;
            document.getElementById('today-access').textContent = stats.today_unique_count || 0;
        }

        function updateCharts(data) {
            const access = data.access_stats?.daily_data || {};
            const dates = Object.keys(access).sort();
            const counts = dates.map(d => access[d]);
            updateAccessChart(dates, counts);

            const alerts = data.alerts_stats || {};

            const alertDates = Object.keys(alerts).sort();
            const traffic = alertDates.map(d => alerts[d].traffic || 0);
            const pertes = alertDates.map(d => alerts[d].pertes || 0);
            const db_down = alertDates.map(d => alerts[d].db_down || 0);
            updateAlertsChart(alertDates, traffic, pertes, db_down);
        }

        function updateAccessChart(dates, counts) {
            const ctx = document.getElementById('accessChart').getContext('2d');
            if (accessChart) {
                accessChart.data.labels = dates;
                accessChart.data.datasets[0].data = counts;
                accessChart.update();
            }
            else {
                accessChart = new Chart(ctx,
                    {
                        type: 'line',
                        data:
                        {
                            labels: dates,
                            datasets: [
                                {
                                    label: 'Accès Uniques',
                                    data: counts,
                                    borderColor: '#2196F3',
                                    backgroundColor: 'rgba(33, 150, 243, 0.1)',
                                    tension: 0.3,
                                    fill: true,
                                    pointRadius: 4,
                                    pointBackgroundColor: '#2196F3'
                                }]
                        },
                        options:
                        {
                            responsive: true,
                            maintainAspectRatio: true,
                            plugins: { legend: { display: true } },
                            scales: { y: { beginAtZero: true } }
                        }
                    });
            }
        }

        function updateAlertsChart(dates, traffic, pertes, db_down) {
            const ctx = document.getElementById('alertsChart').getContext('2d');
            if (alertsChart) {
                alertsChart.data.labels = dates;
                alertsChart.data.datasets[0].data = traffic;
                alertsChart.data.datasets[1].data = pertes;
                if (alertsChart.data.datasets[2]) alertsChart.data.datasets[2].data = db_down;
                alertsChart.update();
            }
            else {
                const datasets = [
                    { label: 'Traffic', data: traffic, backgroundColor: 'rgba(76, 175, 80, 0.7)', borderColor: '#4CAF50', borderWidth: 1 },
                    { label: 'Perte', data: pertes, backgroundColor: 'rgba(244, 67, 54, 0.7)', borderColor: '#f44336', borderWidth: 1 }
                ];
                if (db_down.some(v => v > 0)) datasets.push({ label: 'BD Down', data: db_down, backgroundColor: 'rgba(255, 152, 0, 0.7)', borderColor: '#FF9800', borderWidth: 1 });
                alertsChart = new Chart(ctx,
                    {
                        type: 'bar',
                        data: { labels: dates, datasets },
                        options:
                        {
                            responsive: true,
                            maintainAspectRatio: true,
                            scales: { y: { beginAtZero: true } },
                            plugins: { legend: { display: true } }
                        }
                    });
            }
        }

        function updateTables(data, period) {
            let logs = (data.access_logs || []);

            document.getElementById('access-logs-table').innerHTML = logs.length ?
                logs.map(log => `<tr><td>${esc(log.date || '-')}</td><td>${esc(log.ip_address || '-')}</td><td><span class="badge">${esc(log.access_count || 1)}</span></td><td>${parseDate(log.last_access).toLocaleString('fr-FR')}</td></tr>`).join('') :
                '<tr><td colspan="4" class="loading-text">Aucun accès</td></tr>';

            let configs = (data.config_changes || []);

            document.getElementById('config-table').innerHTML = configs.length ?
                configs.map(cfg => `<tr><td>${parseDate(cfg.timestamp).toLocaleString('fr-FR')}</td><td>${esc(cfg.parameter || '-')}</td><td>${esc(cfg.modified_by || 'système')}</td></tr>`).join('') :
                '<tr><td colspan="3" class="loading-text">Aucune modification</td></tr>';
        }

        // Focus on password input on page load
        window.addEventListener('load', () => {
            document.getElementById('password').focus();
        });
    

document.addEventListener('click', (e) => {
    const el = e.target.closest('[data-action]');
    if (!el) return;
    if (el.dataset.action === 'logout') returnToDashboardWithLogout();
    else if (el.dataset.action === 'loadData') loadData();
});
document.addEventListener('change', (e) => { if (e.target.dataset && e.target.dataset.change === 'loadData') loadData(); });
