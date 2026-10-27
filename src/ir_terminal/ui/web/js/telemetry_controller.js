let snifferRecordsCache = [];
let activeFilter = 'ALL';
let speedHistory = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0];
let rttHistory = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0];

function initTelemetryController() {
  const viewTelemetry = document.getElementById('view-telemetry');
  if (!viewTelemetry) return;

  const speedValueElem = document.getElementById('kpi-speed-value');
  const speedBarElem = document.getElementById('kpi-speed-bar');

  const lossValueElem = document.getElementById('kpi-loss-value');
  const lossPktsElem = document.getElementById('kpi-loss-pkts');

  const rttValueElem = document.getElementById('kpi-rtt-value');

  const chunksValueElem = document.getElementById('kpi-chunks-value');

  const frameCountBadge = document.getElementById('sniffer-frame-count');
  const snifferTableBody = document.getElementById('sniffer-table-body');
  const searchInput = document.getElementById('sniffer-search');
  const filterButtons = viewTelemetry.querySelectorAll('#sniffer-filter-bar button');
  const collapseBtn = viewTelemetry.querySelector('button[title*="Collapse"]');
  const snifferTableContainer = document.getElementById('sniffer-table-container');

  const speedCanvas = document.getElementById('live-speed-chart');
  const rttCanvas = document.getElementById('live-rtt-chart');

  function drawCanvasCharts() {
    if (window.SpectraLinkCharts) {
      window.SpectraLinkCharts.drawCharts(speedCanvas, rttCanvas, speedHistory, rttHistory);
    }
  }

  setTimeout(drawCanvasCharts, 50);
  window.addEventListener('resize', drawCanvasCharts);

  if (collapseBtn && snifferTableContainer) {
    collapseBtn.addEventListener('click', () => {
      if (snifferTableContainer.style.display === 'none') {
        snifferTableContainer.style.display = 'block';
        collapseBtn.innerHTML = '<span class="material-symbols-outlined text-[18px]">expand_more</span>';
      } else {
        snifferTableContainer.style.display = 'none';
        collapseBtn.innerHTML = '<span class="material-symbols-outlined text-[18px]">expand_less</span>';
      }
    });
  }

  document.addEventListener('click', (e) => {
    const btn = e.target.closest('button');
    if (!btn) return;

    if (btn.closest('#modal-overlay, #toast-container, #settings-overlay')) return;

    if (btn.innerText.includes('Export CSV')) {
      if (!window.bridge) {
        if (window.UIFeedback) window.UIFeedback.showToast('warning', 'Backend not connected', 'CSV export needs the SpectraLink Desktop app.');
        return;
      }
      if (window.UIFeedback) window.UIFeedback.setButtonLoading(btn, true, 'Exporting...');
      window.bridge.exportTelemetryCsv((savedPath) => {
        if (window.UIFeedback) window.UIFeedback.setButtonLoading(btn, false);
        if (savedPath) {
          console.log('Telemetry CSV exported successfully to:', savedPath);
          if (window.UIFeedback) {
            window.UIFeedback.showModal({
              variant: 'success',
              title: 'Telemetry exported',
              message: 'CSV report is ready for lab documentation.',
              details: savedPath,
            });
          }
        }
      });
    } else if (btn.innerText.includes('Reset Counters')) {
      if (!window.bridge) {
        if (window.UIFeedback) window.UIFeedback.showToast('warning', 'Backend not connected', 'Reset needs the SpectraLink Desktop app.');
        return;
      }
      const doReset = () => {
        window.bridge.resetTelemetryCounters();
        snifferRecordsCache = [];
        speedHistory = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0];
        rttHistory = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0];
        renderFilteredSnifferTable();
        updateKpis(0.0, 0.0, 0.0, 0, 0);
        drawCanvasCharts();
        if (window.UIFeedback) window.UIFeedback.showToast('info', 'Counters reset', 'Telemetry metrics and sniffer buffer cleared.');
      };
      if (window.UIFeedback) {
        window.UIFeedback.confirmModal({
          title: 'Reset counters?',
          message: 'Throughput, loss, RTT history and captured sniffer frames will be cleared.',
          confirmLabel: 'Reset',
          danger: true,
        }).then((ok) => { if (ok) doReset(); });
      } else {
        doReset();
      }
    }
  });

  if (filterButtons) {
    filterButtons.forEach(btn => {
      btn.addEventListener('click', () => {
        filterButtons.forEach(b => {
          b.className = 'px-2 py-1 rounded text-on-surface-variant hover:text-on-surface font-mono-caption text-[10px] cursor-pointer';
        });
        btn.className = 'px-2 py-1 rounded bg-surface-container-lowest text-primary font-semibold shadow-xs font-mono-caption text-[10px] cursor-pointer';
        activeFilter = btn.innerText.trim();
        renderFilteredSnifferTable();
      });
    });
  }

  if (searchInput) {
    searchInput.addEventListener('input', () => {
      renderFilteredSnifferTable();
    });
  }

  function updateKpis(speedKbps, lossPct, rttMs, chunksCount, framesCount) {
    if (speedValueElem) speedValueElem.innerText = speedKbps.toFixed(2);
    if (speedBarElem) {
      const pct = Math.min(Math.max((speedKbps / 12.0) * 100, 0), 100);
      speedBarElem.style.width = `${pct}%`;
    }

    if (lossValueElem) lossValueElem.innerText = `${lossPct.toFixed(2)}%`;
    if (lossPktsElem) lossPktsElem.innerText = lossPct > 0 ? `(${lossPct.toFixed(1)}% drop)` : '(0 pkts)';

    if (rttValueElem) rttValueElem.innerText = rttMs > 0 ? Math.round(rttMs) : '--';
    if (chunksValueElem) chunksValueElem.innerText = chunksCount.toLocaleString();
    if (frameCountBadge) frameCountBadge.innerText = `${framesCount.toLocaleString()} frames captured`;

    speedHistory.push(speedKbps);
    if (speedHistory.length > 20) speedHistory.shift();
    if (rttMs > 0) {
      rttHistory.push(rttMs);
      if (rttHistory.length > 20) rttHistory.shift();
    }
    drawCanvasCharts();
  }

  function appendSnifferRow(rec) {
    snifferRecordsCache.push(rec);
    if (snifferRecordsCache.length > 200) snifferRecordsCache.shift();

    if (!matchesFilter(rec)) return;
    if (!snifferTableBody) return;

    const emptyRow = snifferTableBody.querySelector('td[colspan="7"]');
    if (emptyRow) {
      snifferTableBody.innerHTML = '';
    }

    const row = createSnifferRowElement(rec);
    snifferTableBody.prepend(row);

    while (snifferTableBody.children.length > 100) {
      snifferTableBody.removeChild(snifferTableBody.lastChild);
    }
  }

  function matchesFilter(rec) {
    const query = searchInput ? searchInput.value.trim().toLowerCase() : '';
    if (query) {
      const matchesSearch = rec.frame_id.toLowerCase().includes(query) ||
                            rec.type_str.toLowerCase().includes(query) ||
                            rec.payload_preview.toLowerCase().includes(query);
      if (!matchesSearch) return false;
    }

    if (activeFilter === 'ALL') return true;
    if (activeFilter === 'SYN/ACK') return rec.type_str.includes('ACK') || rec.type_str.includes('PING') || rec.type_str.includes('PONG');
    if (activeFilter === 'BURST_DATA') return rec.type_str.includes('BURST') || rec.type_str.includes('CHAT') || rec.type_str.includes('XFER');
    if (activeFilter === 'CRC_OK') return rec.is_valid !== false;
    if (activeFilter.includes('ERROR')) return rec.is_valid === false;
    return true;
  }

  function createSnifferRowElement(rec) {
    const row = document.createElement('tr');
    row.className = 'hover:bg-surface-container-low/50 transition-colors';

    let typeTone = 'text-on-surface';
    if (rec.type_str.includes('ACK') || rec.type_str.includes('PONG')) {
      typeTone = 'text-secondary';
    } else if (rec.type_str.includes('ERR') || rec.type_str.includes('NACK')) {
      typeTone = 'text-error';
    } else if (rec.type_str.includes('PING')) {
      typeTone = 'text-primary';
    }
    const checksumTone = rec.is_valid === false ? 'text-error' : 'text-secondary';

    row.innerHTML = `
      <td class="py-1.5 px-space-md text-on-surface-variant font-mono-data">${escapeHtml(rec.time_str)}</td>
      <td class="py-1.5 px-space-sm font-medium text-on-surface font-mono-data">${escapeHtml(rec.frame_id)}</td>
      <td class="py-1.5 px-space-sm text-on-surface-variant font-mono-data">${escapeHtml(rec.carrier || '880nm NIR')}</td>
      <td class="py-1.5 px-space-sm font-mono-data font-medium ${typeTone}">${escapeHtml(rec.type_str)}</td>
      <td class="py-1.5 px-space-sm text-on-surface font-mono"><span class="text-outline">0x55_PRE</span> ${escapeHtml(rec.payload_preview)}</td>
      <td class="py-1.5 px-space-sm font-mono-data ${checksumTone}">${escapeHtml(rec.checksum_status || 'CRC32 Valid')}</td>
      <td class="py-1.5 px-space-md text-right text-on-surface-variant font-mono-data">${(rec.latency_ms || 32.0).toFixed(1)} ms</td>
    `;
    return row;
  }

  function renderFilteredSnifferTable() {
    if (!snifferTableBody) return;
    snifferTableBody.innerHTML = '';
    const filtered = snifferRecordsCache.filter(matchesFilter);

    if (filtered.length === 0) {
      const emptyRow = document.createElement('tr');
      emptyRow.innerHTML = `
        <td colspan="7" class="py-8 text-center text-on-surface-variant text-mono-data">
          <div class="flex flex-col items-center justify-center gap-1">
            <span class="material-symbols-outlined text-[20px] text-outline">memory</span>
            <span>Waiting for optical frames...</span>
            <span class="text-[10px] text-outline">Transmitted and received frames will appear here in real time.</span>
          </div>
        </td>
      `;
      snifferTableBody.appendChild(emptyRow);
      return;
    }

    const fragment = document.createDocumentFragment();
    filtered.slice(-100).reverse().forEach(rec => {
      fragment.appendChild(createSnifferRowElement(rec));
    });
    snifferTableBody.appendChild(fragment);
  }

  function escapeHtml(str) {
    if (window.SpectraLinkUtils) return window.SpectraLinkUtils.escapeHtml(str);
    if (!str) return '';
    return String(str);
  }

  function tryWireBridge() {
    if (window.SpectraLinkBridge) window.SpectraLinkBridge.onReady('telemetry', wireBridgeSignals);
    else if (!window.bridge) setTimeout(tryWireBridge, 300);
    else wireBridgeSignals();
  }

  function wireBridgeSignals() {
    window.bridge.telemetryUpdated.connect((speedKbps, lossPct, rttMs, chunksCount, framesCount) => {
      updateKpis(speedKbps, lossPct, rttMs, chunksCount, framesCount);
    });

    window.bridge.snifferFrameLogged.connect((timeStr, frameId, carrier, typeStr, payloadPreview, checksumStatus, latencyMs) => {
      appendSnifferRow({
        time_str: timeStr,
        frame_id: frameId,
        carrier: carrier,
        type_str: typeStr,
        payload_preview: payloadPreview,
        checksum_status: checksumStatus,
        latency_ms: latencyMs,
        is_valid: true
      });
    });

    if (typeof window.bridge.getTelemetryStats === 'function') {
      window.bridge.getTelemetryStats((statsJson) => {
        try {
          const stats = JSON.parse(statsJson);
          if (stats) {
            updateKpis(
              stats.throughput_kbps || 0.0,
              stats.packet_loss_rate || 0.0,
              stats.rtt_ms || 0.0,
              stats.total_chunks_transferred || 0,
              stats.total_frames_captured || 0
            );
          }
        } catch (e) {
          console.error('Failed to parse telemetry stats:', e);
        }
      });
    }

    if (typeof window.bridge.getSnifferRecords === 'function') {
      window.bridge.getSnifferRecords((recordsJson) => {
        try {
          const records = JSON.parse(recordsJson);
          if (Array.isArray(records) && records.length > 0) {
            snifferRecordsCache = records;
          } else {
            snifferRecordsCache = [];
          }
          renderFilteredSnifferTable();
        } catch (e) {
          console.error('Failed to parse sniffer records:', e);
          renderFilteredSnifferTable();
        }
      });
    } else {
      renderFilteredSnifferTable();
    }
  }

  tryWireBridge();
  renderFilteredSnifferTable();
}

document.addEventListener('DOMContentLoaded', () => {
  setTimeout(initTelemetryController, 100);
});
