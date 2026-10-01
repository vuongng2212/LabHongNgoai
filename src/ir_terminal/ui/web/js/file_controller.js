// File Transfer Viewport Controller with persistent database history loading
let selectedFilePath = '';

function initFileController() {
  const historyTable = document.querySelector('#view-file tbody');

  // Wire file browse & actions
  document.addEventListener('click', (e) => {
    const btn = e.target.closest('button');
    if (!btn) return;

    if (btn.innerText.includes('Browse') || btn.innerText.includes('Select File')) {
      if (window.bridge) {
        window.bridge.openFileDialog((path) => {
          if (path) {
            selectedFilePath = path;
            const fname = path.split('/').pop().split('\\').pop();
            const label = document.querySelector('#view-file .font-title-sm');
            if (label) label.innerText = `Selected: ${fname}`;
            console.log('User selected file:', path);
          }
        });
      }
    } else if (btn.innerText.includes('Transmit') || btn.innerText.includes('Send File') || btn.innerText.includes('Start Transfer')) {
      if (window.bridge && selectedFilePath) {
        window.bridge.startFileTransfer(selectedFilePath);
      } else if (!selectedFilePath && window.bridge) {
        window.bridge.openFileDialog((path) => {
          if (path) {
            selectedFilePath = path;
            window.bridge.startFileTransfer(selectedFilePath);
          }
        });
      }
    } else if (btn.innerText.includes('Abort') || btn.innerText.includes('Cancel')) {
      if (window.bridge) window.bridge.abortFileTransfer();
    }
  });

  if (window.bridge) {
    window.bridge.transferProgress.connect((pct, speedKbps, etaSec, curChunk, totalChunks) => {
      const pBar = document.querySelector('#view-file .h-2 .bg-primary-container, #view-file [style*="width:"]');
      if (pBar) pBar.style.width = `${pct}%`;

      const pctLabel = document.querySelector('#view-file .font-headline-lg, #view-file .font-display-lg');
      if (pctLabel) pctLabel.innerText = `${pct}%`;

      console.log(`Transfer Progress: ${pct}% | ${speedKbps.toFixed(1)} KB/s | Chunk ${curChunk}/${totalChunks}`);
    });

    window.bridge.transferCompleted.connect((filename, bytesSent, elapsedSec, crcHex) => {
      console.log(`File Transfer Complete: ${filename} (${bytesSent}B in ${elapsedSec.toFixed(2)}s) CRC=${crcHex}`);
      addHistoryRow(filename, bytesSent, crcHex, 'SUCCESS', elapsedSec);
    });

    window.bridge.transferFailed.connect((reason) => {
      console.error(`File Transfer Failed: ${reason}`);
    });

    // Load persisted file history from local hard drive SQLite database
    if (typeof window.bridge.getFileHistory === 'function') {
      window.bridge.getFileHistory((historyJson) => {
        try {
          const transfers = JSON.parse(historyJson);
          if (Array.isArray(transfers) && transfers.length > 0 && historyTable) {
            historyTable.innerHTML = '';
            transfers.forEach(item => {
              addHistoryRow(
                item.filename,
                item.size_bytes,
                item.crc32_hex,
                item.status,
                item.duration_sec
              );
            });
          }
        } catch (err) {
          console.error('Failed to parse file history JSON:', err);
        }
      });
    }
  }

  function addHistoryRow(filename, bytesSent, crcHex, status, elapsedSec) {
    if (!historyTable) return;
    const row = document.createElement('tr');
    row.className = 'border-b border-black/[0.04] hover:bg-surface-container-low/50 transition-colors';
    row.innerHTML = `
      <td class="py-2.5 px-3 font-medium text-on-surface flex items-center gap-2">
        <span class="material-symbols-outlined text-secondary text-[16px]">check_circle</span>
        <span class="truncate max-w-[160px]">${escapeHtml(filename)}</span>
      </td>
      <td class="py-2.5 px-3 font-mono-data text-on-surface-variant text-[12px]">${(bytesSent / 1024).toFixed(1)} KB</td>
      <td class="py-2.5 px-3 font-mono-data text-on-surface-variant text-[11px]">${crcHex}</td>
      <td class="py-2.5 px-3"><span class="px-2 py-0.5 rounded bg-[#E8F8F0] text-secondary font-semibold text-[10px]">${escapeHtml(status)}</span></td>
      <td class="py-2.5 px-3 font-mono-data text-outline text-[11px]">${elapsedSec.toFixed(1)}s</td>
    `;
    historyTable.prepend(row);
  }

  function escapeHtml(str) {
    return str.replace(/[&<>'"]/g, tag => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      "'": '&#39;',
      '"': '&quot;'
    }[tag] || tag));
  }
}

document.addEventListener('DOMContentLoaded', () => {
  setTimeout(initFileController, 200);
});
