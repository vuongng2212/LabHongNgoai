// File Transfer Viewport Controller
let selectedFilePath = '';

function initFileController() {
  const browseBtn = document.querySelector('#view-file button:has(.material-symbols-outlined:contains("folder_open")), #view-file button');
  const startBtn = document.querySelector('#view-file button.bg-primary-container');
  const dropzone = document.querySelector('#view-file .border-dashed');
  const progressBar = document.querySelector('#view-file .bg-primary-container.h-full');
  const progressPercent = document.querySelector('#view-file .text-display-lg, #view-file .font-display-lg');
  const speedElem = document.querySelector('#view-file .font-mono-data:has-text("KB/s")');
  const historyTable = document.querySelector('#view-file tbody');

  // Wire file browse
  document.addEventListener('click', (e) => {
    const btn = e.target.closest('button');
    if (!btn) return;

    if (btn.innerText.includes('Browse') || btn.innerText.includes('Select File')) {
      if (bridge) {
        bridge.openFileDialog((path) => {
          if (path) {
            selectedFilePath = path;
            const fname = path.split('/').pop().split('\\\\').pop();
            const label = document.querySelector('#view-file .font-title-sm');
            if (label) label.innerText = `Selected: ${fname}`;
            console.log('User selected file:', path);
          }
        });
      }
    } else if (btn.innerText.includes('Transmit') || btn.innerText.includes('Send File') || btn.innerText.includes('Start Transfer')) {
      if (bridge && selectedFilePath) {
        bridge.startFileTransfer(selectedFilePath);
      } else if (!selectedFilePath && bridge) {
        bridge.openFileDialog((path) => {
          if (path) {
            selectedFilePath = path;
            bridge.startFileTransfer(selectedFilePath);
          }
        });
      }
    } else if (btn.innerText.includes('Abort') || btn.innerText.includes('Cancel')) {
      if (bridge) bridge.abortFileTransfer();
    }
  });

  if (bridge) {
    bridge.transferProgress.connect((pct, speedKbps, etaSec, curChunk, totalChunks) => {
      const pBar = document.querySelector('#view-file .h-2 .bg-primary-container, #view-file [style*="width:"]');
      if (pBar) pBar.style.width = `${pct}%`;

      const pctLabel = document.querySelector('#view-file .font-headline-lg, #view-file .font-display-lg');
      if (pctLabel) pctLabel.innerText = `${pct}%`;

      console.log(`Transfer Progress: ${pct}% | ${speedKbps.toFixed(1)} KB/s | Chunk ${curChunk}/${totalChunks}`);
    });

    bridge.transferCompleted.connect((filename, bytesSent, elapsedSec, crcHex) => {
      console.log(`File Transfer Complete: ${filename} (${bytesSent}B in ${elapsedSec.toFixed(2)}s) CRC=${crcHex}`);
      if (historyTable) {
        const row = document.createElement('tr');
        row.className = 'border-b border-black/[0.04] hover:bg-surface-container-low/50 transition-colors';
        row.innerHTML = `
          <td class="py-2.5 px-3 font-medium text-on-surface flex items-center gap-2">
            <span class="material-symbols-outlined text-secondary text-[16px]">check_circle</span>
            <span class="truncate max-w-[160px]">${escapeHtml(filename)}</span>
          </td>
          <td class="py-2.5 px-3 font-mono-data text-on-surface-variant text-[12px]">${(bytesSent / 1024).toFixed(1)} KB</td>
          <td class="py-2.5 px-3 font-mono-data text-on-surface-variant text-[11px]">${crcHex}</td>
          <td class="py-2.5 px-3"><span class="px-2 py-0.5 rounded bg-[#E8F8F0] text-secondary font-semibold text-[10px]">SUCCESS</span></td>
          <td class="py-2.5 px-3 font-mono-data text-outline text-[11px]">${elapsedSec.toFixed(1)}s</td>
        `;
        historyTable.prepend(row);
      }
    });

    bridge.transferFailed.connect((reason) => {
      console.error(`File Transfer Failed: ${reason}`);
      alert(`Transfer Failed: ${reason}`);
    });
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
