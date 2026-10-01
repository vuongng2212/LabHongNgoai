// File Transfer Viewport Controller with dynamic telemetry updates & SQLite history loading
let selectedFilePath = '';
let transferHistoryCache = [];

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
      }
      return;
    }

    if (!btn) return;

    if (btn.innerText.includes('Browse') || btn.innerText.includes('Select File')) {
      if (window.bridge) {
        window.bridge.openFileDialog((path) => {
          if (path) {
            handleFileSelected(path);
          }
        });
      }
    } else if (btn.innerText.includes('Transmit') || btn.innerText.includes('Send File') || btn.innerText.includes('Start Transfer')) {
      if (window.bridge && selectedFilePath) {
        window.bridge.startFileTransfer(selectedFilePath);
      } else if (!selectedFilePath && window.bridge) {
        window.bridge.openFileDialog((path) => {
          if (path) {
            handleFileSelected(path);
            window.bridge.startFileTransfer(path);
          }
        });
      }
    } else if (btn.innerText.includes('Cancel') || btn.innerText.includes('Abort')) {
      if (window.bridge) {
        window.bridge.abortFileTransfer();
        if (activeStatusBadge) {
          activeStatusBadge.innerText = 'ABORTED';
          activeStatusBadge.className = 'text-mono-caption font-mono-caption bg-error text-white px-2 py-0.5 rounded-full font-bold';
        }
      }
    }
  });

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
    console.log('Selected file for optical transmission:', path);
  }

  // Filter history search
  if (searchInput) {
    searchInput.addEventListener('input', () => {
      renderFilteredHistoryTable();
    });
  }

  if (window.bridge) {
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

      console.log(`Transfer Progress: ${pct}% | ${speedKbps.toFixed(1)} KB/s | Chunk ${curChunk}/${totalChunks}`);
    });

    window.bridge.transferCompleted.connect((filename, bytesSent, elapsedSec, crcHex) => {
      console.log(`File Transfer Complete: ${filename} (${bytesSent}B in ${elapsedSec.toFixed(2)}s) CRC=${crcHex}`);
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
    });

    window.bridge.transferFailed.connect((reason) => {
      console.error(`File Transfer Failed: ${reason}`);
      if (activeStatusBadge) {
        activeStatusBadge.innerText = 'FAILED';
        activeStatusBadge.className = 'text-mono-caption font-mono-caption bg-error text-white px-2 py-0.5 rounded-full font-bold';
      }
    });

    // Load persisted file history from local SQLite database
    if (typeof window.bridge.getFileHistory === 'function') {
      window.bridge.getFileHistory((historyJson) => {
        try {
          const transfers = JSON.parse(historyJson);
          if (Array.isArray(transfers) && transfers.length > 0) {
            transferHistoryCache = transfers;
            renderFilteredHistoryTable();
          }
        } catch (err) {
          console.error('Failed to parse file history JSON:', err);
        }
      });
    }
  }

  function renderFilteredHistoryTable() {
    if (!historyTable) return;
    const query = searchInput ? searchInput.value.trim().toLowerCase() : '';
    historyTable.innerHTML = '';

    const filtered = transferHistoryCache.filter(item => {
      if (!query) return true;
      return (item.filename && item.filename.toLowerCase().includes(query)) ||
             (item.crc32_hex && item.crc32_hex.toLowerCase().includes(query)) ||
             (item.status && item.status.toLowerCase().includes(query));
    });

    filtered.forEach(item => {
      const row = document.createElement('tr');
      row.className = 'hover:bg-surface-container-high/40 transition-colors h-11';
      
      const sizeStr = item.size_bytes >= 1048576 ? 
        `${(item.size_bytes / 1048576).toFixed(1)} MB` : 
        `${(item.size_bytes / 1024).toFixed(1)} KB`;

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
            <button class="w-7 h-7 flex items-center justify-center rounded hover:bg-surface-container-high text-on-surface-variant transition-colors" title="Verified Checksum">
              <span class="material-symbols-outlined text-[16px] text-secondary">verified</span>
            </button>
          </div>
        </td>
      `;
      historyTable.appendChild(row);
    });
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
