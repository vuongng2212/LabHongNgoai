// File Transfer Viewport Controller with dynamic telemetry updates & hard-drive history loading
let selectedFilePath = '';
let transferHistoryCache = [];
let renderedRows = [];

function initFileController() {
  const historyTable = document.querySelector('#view-file tbody');
  const searchInput = document.querySelector('#view-file input[placeholder*="Filter transmissions"]');

  // Active transmission card elements
  const activeFileName = document.querySelector('#view-file .text-title-sm.font-bold.truncate');
  const activeFileSize = document.querySelector('#view-file .text-mono-data.font-semibold.text-on-surface-variant');
  const activeStatusBadge = document.querySelector('#view-file .text-mono-caption.bg-primary-container');
  const progressBar = document.querySelector('#view-file .w-full.h-2\\.5 .h-full, #view-file [style*="width:"]');
  const progressPct = document.querySelector('#view-file .text-primary.font-bold.text-mono-data');
  const speedLabel = document.querySelector('#view-file .grid-cols-3 > div:nth-child(1) .text-headline-md');
  const etaLabel = document.querySelector('#view-file .grid-cols-3 > div:nth-child(2) .text-headline-md');
  const packetsLabel = document.querySelector('#view-file .grid-cols-3 > div:nth-child(3) .text-headline-md');

  // Wire Dropzone & Browse Click
  document.addEventListener('click', (e) => {
    const dropzone = e.target.closest('#view-file .border-dashed');
    const btn = e.target.closest('button');

    if (dropzone && (!btn || btn.innerText.includes('Browse'))) {
      if (window.bridge) {
        window.bridge.openFileDialog((path) => {
          if (path) {
            handleFileSelected(path);
          }
        });
      } else if (window.UIFeedback) {
        window.UIFeedback.showToast('warning', 'Backend not connected', 'File picker needs the SpectraLink Desktop app.');
      }
      return;
    }

    if (!btn) return;

    // Global feedback layer (confirm modal / toasts) owns its buttons —
    // ignore them here so modal actions like Cancel/Abort can't retrigger.
    if (btn.closest('#modal-overlay, #toast-container')) return;

    // Row-level icon actions (e.g. open storage location) carry data-action.
    const actionBtn = btn.closest('[data-action]');
    if (actionBtn) {
      handleRowAction(actionBtn);
      return;
    }

    if (btn.innerText.includes('Browse') || btn.innerText.includes('Select File')) {
      if (window.bridge) {
        window.bridge.openFileDialog((path) => {
          if (path) {
            handleFileSelected(path);
          }
        });
      } else if (window.UIFeedback) {
        window.UIFeedback.showToast('warning', 'Backend not connected', 'File picker needs the SpectraLink Desktop app.');
      }
    } else if (btn.innerText.includes('Transmit') || btn.innerText.includes('Send File') || btn.innerText.includes('Start Transfer')) {
      if (!window.bridge) {
        if (window.UIFeedback) window.UIFeedback.showToast('warning', 'Backend not connected', 'File transfer needs the SpectraLink Desktop app link.');
        return;
      }
      if (selectedFilePath) {
        if (window.UIFeedback) window.UIFeedback.showToast('info', 'Transmission started', 'Streaming over 880nm beam (Stop-and-Wait ARQ).');
        window.bridge.startFileTransfer(selectedFilePath);
      } else {
        if (window.UIFeedback) window.UIFeedback.showToast('warning', 'No file selected', 'Choose a file before starting transmission.');
        window.bridge.openFileDialog((path) => {
          if (path) {
            handleFileSelected(path);
            window.bridge.startFileTransfer(path);
          }
        });
      }
    } else if (btn.innerText.includes('Cancel') || btn.innerText.includes('Abort')) {
      if (window.bridge) {
        const doAbort = () => {
          window.bridge.abortFileTransfer();
          if (activeStatusBadge) {
            activeStatusBadge.innerText = 'ABORTED';
            activeStatusBadge.className = 'text-mono-caption font-mono-caption bg-error text-white px-2 py-0.5 rounded-full font-bold';
          }
          if (window.UIFeedback) window.UIFeedback.showToast('info', 'Transfer aborted', 'Optical transmission cancelled by user.');
        };
        if (window.UIFeedback) {
          window.UIFeedback.confirmModal({
            title: 'Abort transfer?',
            message: 'The current optical transmission will be cancelled. Already-sent chunks are discarded.',
            confirmLabel: 'Abort',
            danger: true,
          }).then((ok) => { if (ok) doAbort(); });
        } else {
          doAbort();
        }
      }
    }
  });

  function handleRowAction(actionBtn) {
    if (actionBtn.dataset.action !== 'open-location') return;
    const item = renderedRows[Number(actionBtn.dataset.idx)];
    const path = item && item.file_path;
    if (!window.bridge) {
      if (window.UIFeedback) window.UIFeedback.showToast('warning', 'Backend not connected', 'Storage location needs the SpectraLink Desktop app.');
      return;
    }
    if (!path) {
      if (window.UIFeedback) window.UIFeedback.showToast('info', 'Location not recorded yet', 'History is syncing — try again in a moment.');
      return;
    }
    window.bridge.openTransferLocation(path, (openedDir) => {
      if (!window.UIFeedback) return;
      if (openedDir) {
        window.UIFeedback.showToast('success', 'Storage folder opened', openedDir);
      } else {
        window.UIFeedback.showModal({
          variant: 'info',
          title: 'File no longer there',
          message: 'The recorded file was moved or deleted. Nothing to open.',
          details: path,
        });
      }
    });
  }

  function reloadHistoryFromBridge() {
    if (!window.bridge || typeof window.bridge.getFileHistory !== 'function') {
      renderFilteredHistoryTable();
      return;
    }
    window.bridge.getFileHistory((historyJson) => {
      try {
        const transfers = JSON.parse(historyJson);
        transferHistoryCache = Array.isArray(transfers) ? transfers : [];
      } catch (err) {
        console.error('Failed to parse file history JSON:', err);
        transferHistoryCache = [];
      }
      renderFilteredHistoryTable();
    });
  }

  function handleFileSelected(path) {
    selectedFilePath = path;
    const fname = path.split('/').pop().split('\\').pop();
    if (activeFileName) activeFileName.innerText = fname;
    if (activeStatusBadge) {
      activeStatusBadge.innerText = 'READY';
      activeStatusBadge.className = 'text-mono-caption font-mono-caption bg-primary-container text-on-primary-container px-2 py-0.5 rounded-full font-bold';
    }
    if (progressBar) progressBar.style.width = '0%';
    if (progressPct) progressPct.innerText = '0%';
    if (window.UIFeedback) window.UIFeedback.showToast('info', 'File selected', fname + ' ready for optical transmission.');
    console.log('Selected file for optical transmission:', path);
  }

  // Filter history search
  if (searchInput) {
    searchInput.addEventListener('input', () => {
      renderFilteredHistoryTable();
    });
  }

  // Bridge connects asynchronously (QWebChannel); retry until ready, then wire once.
  let bridgeWired = false;
  function tryWireBridge() {
    if (bridgeWired) return;
    if (!window.bridge) {
      setTimeout(tryWireBridge, 300);
      return;
    }
    bridgeWired = true;
    wireBridgeSignals();
  }

  function wireBridgeSignals() {
    window.bridge.transferProgress.connect((pct, speedKbps, etaSec, curChunk, totalChunks) => {
      if (progressBar) progressBar.style.width = `${pct}%`;
      if (progressPct) progressPct.innerText = `${pct}%`;
      if (speedLabel) speedLabel.innerHTML = `${speedKbps.toFixed(1)} <span class="text-label-sm font-label-sm font-normal text-on-surface-variant">KB/s</span>`;
      if (etaLabel) etaLabel.innerText = `${Math.ceil(etaSec)}s`;
      if (packetsLabel) packetsLabel.innerHTML = `${curChunk}<span class="text-label-sm font-label-sm font-normal text-outline">/${totalChunks}</span>`;

      if (activeStatusBadge) {
        activeStatusBadge.innerText = pct >= 100 ? 'COMPLETED' : 'STREAMING';
        activeStatusBadge.className = pct >= 100 ? 
          'text-mono-caption font-mono-caption bg-secondary text-white px-2 py-0.5 rounded-full font-bold' :
          'text-mono-caption font-mono-caption bg-primary-container text-on-primary-container px-2 py-0.5 rounded-full font-bold animate-pulse';
      }
    });

    window.bridge.transferCompleted.connect((filename, bytesSent, elapsedSec, crcHex) => {
      const newRec = {
        filename: filename,
        size_bytes: bytesSent,
        crc32_hex: crcHex,
        status: 'SUCCESS',
        duration_sec: elapsedSec,
        timestamp: 'Just now'
      };
      transferHistoryCache.unshift(newRec);
      renderFilteredHistoryTable();
      // Re-sync from hard-drive store so the new row gains its recorded file_path
      // (used by the open-location row action) without changing signals.
      reloadHistoryFromBridge();
      if (window.UIFeedback) {
        window.UIFeedback.showModal({
          variant: 'success',
          title: 'Transfer complete',
          message: `${filename} verified over optical link in ${elapsedSec.toFixed(1)}s.`,
          details: `CRC-32 ${crcHex} • ${bytesSent} bytes`,
        });
      }
    });

    window.bridge.transferFailed.connect((reason) => {
      console.error(`File Transfer Failed: ${reason}`);
      if (activeStatusBadge) {
        activeStatusBadge.innerText = 'FAILED';
        activeStatusBadge.className = 'text-mono-caption font-mono-caption bg-error text-white px-2 py-0.5 rounded-full font-bold';
      }
      if (window.UIFeedback) {
        window.UIFeedback.showModal({
          variant: 'error',
          title: 'Transfer failed',
          message: 'Optical transmission did not complete (timeout / blocked beam / disconnect).',
          details: reason,
          actions: [
            { id: 'close', label: 'Close', kind: 'secondary' },
            { id: 'retry', label: 'Retry', kind: 'primary', onClick: (close) => {
              close();
              if (selectedFilePath && window.bridge) window.bridge.startFileTransfer(selectedFilePath);
            } },
          ],
        });
      }
    });

    // Load persisted file history from local hard-drive store
    reloadHistoryFromBridge();
  }

  tryWireBridge();

  function renderFilteredHistoryTable() {
    if (!historyTable) return;
    const query = searchInput ? searchInput.value.trim().toLowerCase() : '';
    historyTable.innerHTML = '';
    renderedRows = [];

    const filtered = transferHistoryCache.filter(item => {
      if (!query) return true;
      return (item.filename && item.filename.toLowerCase().includes(query)) ||
             (item.crc32_hex && item.crc32_hex.toLowerCase().includes(query)) ||
             (item.status && item.status.toLowerCase().includes(query));
    });

    if (filtered.length === 0) {
      const emptyRow = document.createElement('tr');
      emptyRow.innerHTML = `
        <td colspan="7" class="py-8 text-center text-on-surface-variant text-body-md">
          <div class="flex flex-col items-center justify-center gap-1">
            <span class="material-symbols-outlined text-[24px] text-outline">folder_off</span>
            <span class="font-medium text-on-surface">No optical file transfers recorded yet</span>
            <span class="text-label-sm text-outline">Transferred files will be logged here with verified CRC-32 checksums.</span>
          </div>
        </td>
      `;
      historyTable.appendChild(emptyRow);
      return;
    }

    // Batch all rows into a DocumentFragment: identical DOM, one reflow.
    const fragment = document.createDocumentFragment();
    filtered.forEach(item => {
      const row = document.createElement('tr');
      row.className = 'hover:bg-surface-container-high/40 transition-colors h-11';

      const sizeStr = item.size_bytes >= 1048576 ?
        `${(item.size_bytes / 1048576).toFixed(1)} MB` :
        `${(item.size_bytes / 1024).toFixed(1)} KB`;

      const rowIdx = renderedRows.length;
      renderedRows.push(item);
      row.innerHTML = `
        <td class="px-space-md">
          <div class="flex items-center gap-2">
            <span class="material-symbols-outlined text-[18px] text-primary">description</span>
            <span class="font-mono-data font-semibold text-on-surface truncate max-w-[200px]">${escapeHtml(item.filename)}</span>
          </div>
        </td>
        <td class="px-space-md font-mono-data text-on-surface-variant">${sizeStr}</td>
        <td class="px-space-md">
          <span class="text-mono-caption font-mono-caption bg-surface-container px-2 py-0.5 rounded text-on-surface font-semibold">880nm NIR</span>
        </td>
        <td class="px-space-md text-mono-caption font-mono-caption text-on-surface-variant">${escapeHtml(item.timestamp || 'Today')}</td>
        <td class="px-space-md">
          <span class="text-mono-caption font-mono-caption font-mono text-outline-variant bg-surface-container-lowest px-1.5 py-0.5 rounded border border-outline-variant/20">CRC32: ${escapeHtml(item.crc32_hex)}</span>
        </td>
        <td class="px-space-md">
          <div class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-secondary/10 text-secondary text-mono-caption font-mono-caption font-semibold">
            <span class="w-1.5 h-1.5 rounded-full bg-secondary"></span>
            <span>Verified (${escapeHtml(item.status || '100%')})</span>
          </div>
        </td>
        <td class="px-space-md text-right">
          <div class="flex items-center justify-end gap-1">
            <button data-action="open-location" data-idx="${rowIdx}" class="w-7 h-7 flex items-center justify-center rounded hover:bg-surface-container-high text-on-surface-variant hover:text-primary transition-colors" title="Open storage location">
              <span class="material-symbols-outlined text-[16px]">folder_open</span>
            </button>
            <button class="w-7 h-7 flex items-center justify-center rounded hover:bg-surface-container-high text-on-surface-variant transition-colors" title="Verified Checksum">
              <span class="material-symbols-outlined text-[16px] text-secondary">verified</span>
            </button>
          </div>
        </td>
      `;
      fragment.appendChild(row);
    });
    historyTable.appendChild(fragment);
  }

  function escapeHtml(str) {
    if (!str) return '';
    return String(str).replace(/[&<>'"]/g, tag => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      "'": '&#39;',
      '"': '&quot;'
    }[tag] || tag));
  }
}

document.addEventListener('DOMContentLoaded', () => {
  setTimeout(initFileController, 100);
});
