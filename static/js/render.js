// AUTODO — Rafraîchissement des données et rendu du DOM
// Script classique (partage la portée globale avec les autres fichiers de static/js/, chargés dans l'ordre de dashboard.html).

// ====== RAFRAÎCHISSEMENT PRINCIPAL ======

function getClientId() {
    let cid = localStorage.getItem("client_id");
    if (cid) return cid;

    // première visite → on laisse le serveur le générer
    return "";
}

function refreshDashboardData() {
    if (STATE.refreshInProgress) return;
    STATE.refreshInProgress = true;

    const refreshBtn = DOM.refreshBtn;
    const refreshIcon = refreshBtn?.querySelector('.refresh-icon');

    if (refreshBtn) {
        refreshBtn.disabled = true;
        refreshBtn.style.opacity = '0.6';
        if (refreshIcon) refreshIcon.style.animation = 'spin 1s linear infinite';
    }

    // Collect filter parameters
    const statusFilter = document.querySelector('input[name="statusFilter"]:checked')?.value || 'all';
    const typeFilter = document.querySelector('input[name="typeFilter"]:checked')?.value || 'all';
    const periodFilter = document.querySelector('input[name="periodFilter"]:checked')?.value || 'all';

    // Send filters in request body
    const clientId = getClientId();

    fetch('/api/dashboard_data', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            "X-Client-Id": clientId
        },
        body: JSON.stringify({
            status: statusFilter,
            type: typeFilter,
            period: periodFilter
        })
    })
        .then(response => {
            const newId = response.headers.get("X-Client-Id");
            // N'écrase que si le localStorage est vide !
            if (newId && !localStorage.getItem("client_id")) {
                localStorage.setItem("client_id", newId);
            }

            if (!response.ok) throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            return response.json();
        })
        .then(data => {
            updateDashboardDOM(data);
        })
        .catch(err => {
            console.error('Dashboard update error:', err);
        })
        .finally(() => {
            if (refreshBtn) {
                refreshBtn.disabled = false;
                refreshBtn.style.opacity = '1';
                if (refreshIcon) refreshIcon.style.animation = '';
            }
            STATE.refreshInProgress = false;
        });
}


// ====== MISE À JOUR DU DOM ======
function updateDashboardDOM(data) {
    // Si le backend renvoie une erreur, ne pas tenter de rendre
    if (!data || data.error) {
        if (data && data.error) {
            Toasts.show(`Erreur serveur: ${data.error}`, { type: 'error' });
        }
        return;
    }
    const intervals = ['5min', '1day', '1week'];

    // Helper function to build table rows
    const buildTableRow = (loc, names, recapData, isTotal = false) => {
        const nowRecap = recapData['5min'][loc];
        const linkTotal = nowRecap?.link?.total && nowRecap.link.total !== '#' ? nowRecap.link.total : null;
        const linkPertes = nowRecap?.link?.pertes && nowRecap.link.pertes !== '#' ? nowRecap.link.pertes : null;
        const locationName = names[loc] || loc;

        let row = `<tr${isTotal ? ' class="total-row"' : ''}><td class="location-cell">${locationName}</td>`;

        // Traffic columns
        const nowVal = nowRecap?.total?.end ?? '';
        row += `<td class="traffic-cell${linkTotal ? ' clickable-cell' : ''}"${linkTotal ? ` data-link-total='${linkTotal}'` : ''} title="DIAGNET EXNET: open Traffic View"><span class="value">${nowVal !== '' ? Number(nowVal).toFixed(2) : '-'}</span></td>`;

        intervals.forEach(interval => {
            const recapLoc = recapData[interval] && recapData[interval][loc];
            const start = recapLoc?.total?.start ?? '';
            const end = recapLoc?.total?.end ?? '';
            // const diff = (start !== '' && end !== '') ? Math.abs(parseFloat(start) - parseFloat(end)).toFixed(2) : '';
            const diff = recapLoc?.total?.taux != null ? Math.abs(Number(recapLoc.total.taux)).toFixed(2) : '';

            row += `<td class="traffic-cell${linkTotal ? ' clickable-cell' : ''}"${linkTotal ? ` data-link-total='${linkTotal}'` : ''} title="DIAGNET EXNET: open Traffic View"><span class="value">${start !== '' ? Number(start).toFixed(2) : '-'}</span>`;
            if (recapLoc && diff) {
                row += `<span class="trend" style="color:${UIUtils.trendColorToCss(recapLoc.total?.trend_color)}">${recapLoc.total?.trend_icon ?? ''}${diff}</span>`;
            }
            row += `</td>`;
        });

        // Pertes columns
        const nowPertes = nowRecap?.pertes?.end ?? '';
        row += `<td class="loss-cell separator-col${linkPertes ? ' clickable-cell' : ''}"${linkPertes ? ` data-link-pertes='${linkPertes}'` : ''} title="DIAGNET EXNET: open Packet Loss View"><span class="value">${nowPertes !== '' ? Number(nowPertes).toFixed(3) : '-'}</span></td>`;

        intervals.forEach(interval => {
            const recapLoc = recapData[interval] && recapData[interval][loc];
            const start = recapLoc?.pertes?.start ?? '';
            const end = recapLoc?.pertes?.end ?? '';
            // const diff = (start !== '' && end !== '') ? Math.abs(parseFloat(start) - parseFloat(end)).toFixed(3) : '';
            const diff = recapLoc?.pertes?.taux != null ? Math.abs(Number(recapLoc.pertes.taux)).toFixed(3) : '';

            row += `<td class="loss-cell${linkPertes ? ' clickable-cell' : ''}"${linkPertes ? ` data-link-pertes='${linkPertes}'` : ''} title="DIAGNET EXNET: open Packet Loss View"><span class="value">${start !== '' ? Number(start).toFixed(3) : '-'}</span>`;
            if (recapLoc && diff) {
                row += `<span class="trend" style="color:${UIUtils.trendColorToCss(recapLoc.pertes?.trend_color)}">${recapLoc.pertes?.trend_icon ?? ''}${diff}</span>`;
            }
            row += `</td>`;
        });

        return row + `</tr>`;
    };

    // Helper function to build total row from totals data
    const buildTotalRow = (totalsData, label = 'Total') => {
        const safeTotals = totalsData || {};
        const trafficTotals = safeTotals.traffic || {};
        const pertesTotals = safeTotals.pertes || {};
        let row = `<tr class="total-row"><td class="location-cell">${label}</td>`;

        // Traffic columns
        const totNow = trafficTotals['5min']?.traffic_end ?? '';
        row += `<td class="traffic-cell"><span class="value">${totNow !== '' ? Number(totNow).toFixed(2) : '-'}</span></td>`;

        intervals.forEach(interval => {
            const tot = trafficTotals[interval];
            const start = parseFloat(tot?.traffic_start);
            const end = parseFloat(tot?.traffic_end);
            // const diff = (!isNaN(start) && !isNaN(end)) ? Math.abs(start - end).toFixed(2) : '';
            const diff = tot?.taux_traffic != null ? Math.abs(tot?.taux_traffic).toFixed(2) : '';

            row += `<td class="traffic-cell"><span class="value">${!isNaN(start) ? start.toFixed(2) : '-'}</span>`;
            if (tot && diff) {
                row += `<span class="trend" style="color:${UIUtils.trendColorToCss(tot.trend_color)}">${tot.trend_icon ?? ''}${diff}</span>`;
            }
            row += `</td>`;
        });

        // Pertes columns
        const totNowPertes = pertesTotals['5min']?.pertes_end ?? '';
        row += `<td class="loss-cell separator-col"><span class="value">${totNowPertes !== '' ? Number(totNowPertes).toFixed(3) : '-'}</span></td>`;

        intervals.forEach(interval => {
            const tot = pertesTotals[interval];
            const start = parseFloat(tot?.pertes_start);
            const end = parseFloat(tot?.pertes_end);
            // const diff = (!isNaN(start) && !isNaN(end)) ? Math.abs(start - end).toFixed(3) : '';
            const diff = tot?.taux_pertes != null ? Math.abs(tot?.taux_pertes).toFixed(3) : '';

            row += `<td class="loss-cell"><span class="value">${!isNaN(start) ? start.toFixed(3) : '-'}</span>`;
            if (tot && diff) {
                row += `<span class="trend" style="color:${UIUtils.trendColorToCss(tot.trend_color)}">${tot.trend_icon ?? ''}${diff}</span>`;
            }
            row += `</td>`;
        });

        return row + `</tr>`;
    };

    // Build totals row for TCP+DNS metrics (used by International + GI section)
    const buildTcpDnsTotalRow = (totalsData, label = 'Total') => {
        const safeTotals = totalsData || {};
        let row = `<tr class="total-row"><td class="location-cell">${label}</td>`;

        const metrics = ['latence', 'tcp_no_response', 'tcp_block_wp_up', 'dns_malformed', 'dns_no_response'];
        // Now for each metric, show the 5min 'end' value, then starts for intervals
        metrics.forEach((metric, metricIndex) => {
            const separatorClass = metricIndex > 0 ? ' separator-col' : '';
            const decimalPlaces = metric.startsWith('dns') ? 3 : 2;

            const nowVal = safeTotals[metric]?.['5min'] ? safeTotals[metric]['5min'][`${metric}_end`] : '';
            row += `<td class="tcp-cell${separatorClass}"><span class="value">${nowVal !== '' ? Number(nowVal).toFixed(decimalPlaces) : '-'}</span></td>`;

            intervals.forEach(interval => {
                const info = safeTotals[metric]?.[interval];
                const start = info ? info[`${metric}_start`] : '';
                const taux = info ? info[`taux_${metric}`] : '';
                row += `<td class="tcp-cell"><span class="value">${start !== '' ? Number(start).toFixed(decimalPlaces) : '-'}</span>`;
                if (info && taux !== undefined && taux !== null && taux !== '') {
                    const trendColor = info.trend_color || 'neutral';
                    const trendIcon = info.trend_icon || '';
                    const tauxValue = Math.abs(Number(taux)).toFixed(decimalPlaces);
                    row += `<span class="trend" style="color:${UIUtils.trendColorToCss(trendColor)}">${trendIcon}${tauxValue}</span>`;
                }
                row += `</td>`;
            });
        });

        return row + `</tr>`;
    };


    // Helper function to build TCP metrics table row (for international+gi section)
    const buildTcpMetricsRow = (loc, combinedNames, recapData) => {
        const locationName = combinedNames[loc] || loc;
        let row = `<tr><td class="location-cell">${locationName}</td>`;

        // Get links for this location
        const links = recapData['5min'] && recapData['5min'][loc]?.link;

        // TCP Metrics: latence, tcp_no_response, tcp_block_wp_up
        const tcpMetrics = ['latence', 'tcp_no_response', 'tcp_block_wp_up'];
        const intervals = ['5min', '1day', '1week'];

        tcpMetrics.forEach((metric, metricIndex) => {
            const decimalPlaces = metric === 'latence' ? 2 : 2; // 2 décimales pour packets/sec et latence
            const separatorClass = metricIndex > 0 ? ' separator-col' : '';

            // Determine link attribute based on metric
            let linkAttr = '';
            let linkValue = '';
            // links.tcp_tts is for latency view
            if (metric === 'latence' && links?.tcp_tts) {
                linkAttr = 'data-link-tcp-tts';
                linkValue = links.tcp_tts;
            }
            // links.tcp_service_access is for TCP Service Access (tcp_no_response and tcp_block_wp_up)
            else if ((metric === 'tcp_no_response' || metric === 'tcp_block_wp_up') && links?.tcp_service_access) {
                linkAttr = 'data-link-tcp-service-access';
                linkValue = links.tcp_service_access;
            }

            // NOW column (only for 5min data)
            const nowData = recapData['5min'] && recapData['5min'][loc]?.tcp_metrics?.[metric];
            const valNow = nowData?.end ?? '';

            row += `<td class="tcp-cell${separatorClass}${linkValue ? ' clickable-cell' : ''}"${linkValue ? ` ${linkAttr}='${linkValue}'` : ''} title="${linkValue ? 'DIAGNET EXNET: open TCP ' + (metric === 'latence' ? 'TTS' : 'Service Access') + ' View' : ''}"><span class="value">${valNow !== '' ? Number(valNow).toFixed(decimalPlaces) : '-'}</span></td>`;

            intervals.forEach(interval => {
                const intervalData = recapData[interval] && recapData[interval][loc]?.tcp_metrics?.[metric];
                const val = intervalData?.start ?? '';

                row += `<td class="tcp-cell${linkValue ? ' clickable-cell' : ''}"${linkValue ? ` ${linkAttr}='${linkValue}'` : ''} title="${linkValue ? 'DIAGNET EXNET: open TCP ' + (metric === 'latence' ? 'TTS' : 'Service Access') + ' View' : ''}"><span class="value">${val !== '' ? Number(val).toFixed(decimalPlaces) : '-'}</span>`;

                // Add trend for interval data if available
                if (intervalData && intervalData.taux != null) {
                    const trendColor = intervalData.trend_color || 'neutral';
                    const trendIcon = intervalData.trend_icon || '';
                    const tauxValue = Math.abs(Number(intervalData.taux)).toFixed(decimalPlaces);
                    row += `<span class="trend" style="color:${UIUtils.trendColorToCss(trendColor)}">${trendIcon}${tauxValue}</span>`;
                }
                row += `</td>`;
            });
        });

        // DNS metrics (dns_malformed, dns_no_response)
        const dnsMetrics = ['dns_malformed', 'dns_no_response'];
        dnsMetrics.forEach((metric, idx) => {
            const separatorClass = ' separator-col';
            const decimalPlaces = 3; // DNS percentages should be 3 decimals

            // External link for DNS summary if available
            const dnsLinkValue = links?.dns;
            const dnsLinkAttr = dnsLinkValue ? ` data-link-dns='${dnsLinkValue}'` : '';
            const dnsClickableClass = dnsLinkValue ? ' clickable-cell' : '';

            const nowData = recapData['5min'] && recapData['5min'][loc]?.dns_metrics?.[metric];
            const valNow = nowData?.end ?? '';
            row += `<td class="tcp-cell${separatorClass}${dnsClickableClass}"${dnsLinkAttr} title="${dnsLinkValue ? 'DIAGNET EXNET: open DNS Summary View' : ''}"><span class="value">${valNow !== '' ? Number(valNow).toFixed(decimalPlaces) : '-'}</span></td>`;

            intervals.forEach(interval => {
                const intervalData = recapData[interval] && recapData[interval][loc]?.dns_metrics?.[metric];
                const val = intervalData?.start ?? '';

                row += `<td class="tcp-cell${dnsClickableClass}"${dnsLinkAttr} title="${dnsLinkValue ? 'DIAGNET EXNET: open DNS Summary View' : ''}"><span class="value">${val !== '' ? Number(val).toFixed(decimalPlaces) : '-'}</span>`;
                if (intervalData && intervalData.taux != null) {
                    const trendColor = intervalData.trend_color || 'neutral';
                    const trendIcon = intervalData.trend_icon || '';
                    const tauxValue = Math.abs(Number(intervalData.taux)).toFixed(decimalPlaces);
                    row += `<span class="trend" style="color:${UIUtils.trendColorToCss(trendColor)}">${trendIcon}${tauxValue}</span>`;
                }
                row += `</td>`;
            });
        });

        return row + `</tr>`;
    };

    const tableHeader = `<table class="data-table"><thead><tr><th rowspan="2">Location</th><th colspan="4">Traffic (Gbits/s)</th><th class="separator-col" colspan="4">Pertes (%)</th></tr><tr><th>Now</th><th>5min</th><th>1D</th><th>1W</th><th class="separator-col">Now</th><th>5min</th><th>1D</th><th>1W</th></tr></thead><tbody>`;

    // === Sortie Internationale ===
    let intlTable = tableHeader;
    Object.keys(data.names || {}).forEach(loc => {
        intlTable += buildTableRow(loc, data.names, data.recap);
    });
    intlTable += buildTotalRow(data.totals_international);
    intlTable += `</tbody></table>`;

    const intlDiv = DOM.tableWrapperInternational;
    if (intlDiv) {
        intlDiv.innerHTML = intlTable;
        TableManager.delegateTbodyClick(intlDiv.querySelector('tbody'), 'clickable-cell', 'data-link-total', 'TD');
    }

    // === Traffic Utilisateur (GI) ===
    let giTable = tableHeader;
    Object.keys(data.gi_names || {}).forEach(loc => {
        giTable += buildTableRow(loc, data.gi_names, data.recap);
    });
    giTable += buildTotalRow(data.totals_gi);
    giTable += `</tbody></table>`;

    const giDiv = DOM.tableWrapperGi;
    if (giDiv) {
        giDiv.innerHTML = giTable;
        TableManager.delegateTbodyClick(giDiv.querySelector('tbody'), 'clickable-cell', 'data-link-total', 'TD');
    }

    // === International + GI (TCP Metrics) ===
    const tcpMetricsHeader = `<table class="data-table"><thead><tr><th rowspan="2">Location</th><th colspan="4">Latence (ms)</th><th class="separator-col" colspan="4">TCP No Response (%)</th><th class="separator-col" colspan="4">TCP Block WP Up (%)</th><th class="separator-col" colspan="4">DNS Malformed (%)</th><th class="separator-col" colspan="4">DNS No Response (%)</th></tr><tr><th>Now</th><th>5min</th><th>1D</th><th>1W</th><th class="separator-col">Now</th><th>5min</th><th>1D</th><th>1W</th><th class="separator-col">Now</th><th>5min</th><th>1D</th><th>1W</th><th class="separator-col">Now</th><th>5min</th><th>1D</th><th>1W</th><th class="separator-col">Now</th><th>5min</th><th>1D</th><th>1W</th></tr></thead><tbody>`;

    let internationalGiTable = tcpMetricsHeader;

    // Combine names from international and gi with _gi suffix for gi locations
    const combinedNames = {
        ...data.names,
        ...Object.fromEntries(
            Object.entries(data.gi_names).map(([key, value]) => [key, value + '_gi'])
        )
    };

    // Add rows for all locations from international and gi
    const allLocations = [...new Set([...Object.keys(data.names || {}), ...Object.keys(data.gi_names || {})])];

    allLocations.forEach(loc => {
        internationalGiTable += buildTcpMetricsRow(loc, combinedNames, data.recap);
    });

    // Add totals row for combined TCP+DNS metrics (use combined international+GI totals)
    internationalGiTable += buildTcpDnsTotalRow(data.totals_international_gi, 'Total');
    internationalGiTable += `</tbody></table>`;

    const internationalGiDiv = DOM.tableWrapperInternationalGi;
    if (internationalGiDiv) {
        internationalGiDiv.innerHTML = internationalGiTable;
        TableManager.delegateTbodyClick(internationalGiDiv.querySelector('tbody'), 'clickable-cell', 'data-link-total', 'TD');
    }

    // === TDD ===
    let tddTable = tableHeader;
    Object.keys(data.gi_names || {}).forEach(loc => {
        const modifiedNames = Object.fromEntries(
            Object.entries(data.gi_names).map(([k, v]) => [k, v + ' GI'])
        );
        tddTable += buildTableRow(loc, modifiedNames, data.tdd_recap);
    });
    tddTable += buildTotalRow(data.totals_tdd);
    tddTable += `</tbody></table>`;

    const tddDiv = DOM.tableWrapperTdd;
    if (tddDiv) {
        tddDiv.innerHTML = tddTable;
        TableManager.delegateTbodyClick(tddDiv.querySelector('tbody'), 'clickable-cell', 'data-link-total', 'TD');
    }

    // === Roaming Utilisateur ===
    const roamingIntervals = ['now', '5min', '1day', '1week'];
    roamingIntervals.forEach(interval => {
        let roamingTable = `<table class="data-table"><thead><tr><th>Ville</th><th>Roaming IN UP Gp</th><th>Roaming IN UP S8</th><th>Roaming OUT UP</th></tr></thead><tbody>`;

        data.roaming_cities.forEach(city => {
            roamingTable += `<tr><td class="location-cell">${city.charAt(0).toUpperCase() + city.slice(1)}</td>`;

            data.roaming_types.forEach(type => {
                const location = `${city}_gi_roaming_${type}`;
                const recap = data.roaming_recap[interval === 'now' ? '5min' : interval][location];
                const cellLinkTotal = recap?.link?.total && recap.link.total !== '#' ? recap.link.total : null;
                const cellLinkPertes = recap?.link?.pertes && recap.link.pertes !== '#' ? recap.link.pertes : null;

                if (interval === 'now') {
                    const end = recap?.total?.end ?? '';
                    const endp = recap?.pertes?.end ?? '';
                    roamingTable += `<td class="roaming-cell">
                        <div class="metric${cellLinkTotal ? ' clickable-cell' : ''}"${cellLinkTotal ? ` data-link-total='${cellLinkTotal}'` : ''} title="DIAGNET EXNET: open Traffic View"><span>Traffic:</span><span class="value">${end !== '' ? Number(end).toFixed(2) : '-'}</span></div>
                        <div class="metric${cellLinkPertes ? ' clickable-cell' : ''}"${cellLinkPertes ? ` data-link-pertes='${cellLinkPertes}'` : ''} title="DIAGNET EXNET: open Packet Loss View"><span>Pertes:</span><span class="value">${endp !== '' ? Number(endp).toFixed(3) : '-'}</span></div>
                    </td>`;
                } else {
                    const start = recap?.total?.start ?? '';
                    const end = recap?.total?.end ?? '';
                    const startp = recap?.pertes?.start ?? '';
                    const endp = recap?.pertes?.end ?? '';
                    // const diffTraffic = (start !== '' && end !== '') ? Math.abs(parseFloat(start) - parseFloat(end)).toFixed(2) : '';
                    const diffTraffic = recap?.total?.taux != null ? Math.abs(Number(recap.total.taux)).toFixed(2) : '';
                    // const diffPertes = (startp !== '' && endp !== '') ? Math.abs(parseFloat(startp) - parseFloat(endp)).toFixed(3) : '';
                    const diffPertes = recap?.pertes?.taux != null ? Math.abs(Number(recap.pertes.taux)).toFixed(3) : '';

                    roamingTable += `<td class="roaming-cell">
                        <div class="metric${cellLinkTotal ? ' clickable-cell' : ''}"${cellLinkTotal ? ` data-link-total='${cellLinkTotal}'` : ''} title="DIAGNET EXNET: open Traffic View"><span>Traffic:</span><span class="value">${start !== '' ? Number(start).toFixed(2) : ''}</span><span class="trend" style="color:${UIUtils.trendColorToCss(recap?.total?.trend_color)}">${recap?.total?.trend_icon ?? ''}${diffTraffic}</span></div>
                        <div class="metric${cellLinkPertes ? ' clickable-cell' : ''}"${cellLinkPertes ? ` data-link-pertes='${cellLinkPertes}'` : ''} title="DIAGNET EXNET: open Packet Loss View"><span>Pertes:</span><span class="value">${startp !== '' ? Number(startp).toFixed(3) : ''}</span><span class="trend" style="color:${UIUtils.trendColorToCss(recap?.pertes?.trend_color)}">${recap?.pertes?.trend_icon ?? ''}${diffPertes}</span></div>
                    </td>`;
                }
            });
            roamingTable += `</tr>`;
        });

        roamingTable += `</tbody></table>`;
        const roamingDiv = DOM.getTableWrapperRoaming(interval);
        if (roamingDiv) {
            roamingDiv.innerHTML = roamingTable;
            const tbody = roamingDiv.querySelector('tbody');
            if (tbody) TableManager.delegateTbodyClick(tbody, 'metric', 'data-link', 'DIV');
        }
    });

    // === Alertes ===
    // Ensure alertes data structure exists
    if (!data.alertes) {
        data.alertes = { traffic: [], pertes: [] };
    }
    if (!Array.isArray(data.alertes.traffic)) {
        data.alertes.traffic = [];
    }
    if (!Array.isArray(data.alertes.pertes)) {
        data.alertes.pertes = [];
    }

    const allAlertes = [...(data.alertes?.traffic || []), ...(data.alertes?.pertes || [])];
    allAlertes.sort((a, b) => (b.creation_time || b.date).localeCompare(a.creation_time || a.date));

    // Store dashboard data globally for filter functionality
    window.lastDashboardData = data;

    // Récupérer les alertes ignorées via AlertesManager
    const dismissedAlertes = AlertManager.getDismissedAlertes();

    // Filtrer les alertes à afficher pour exclure celles qui sont ignorées
    const finalAlertesToShow = allAlertes.filter(a => !dismissedAlertes.includes(a.id));

    AlertManager.updateBadge(finalAlertesToShow);
    AlertManager.checkNewAlertes(finalAlertesToShow);
    // AlertManager.displayAlertes(data.alertes);

    // === Timestamp ===
    if (DOM.lastUpdateValue && data.current_time) {
        DOM.lastUpdateValue.textContent = data.current_time;
    }
}
