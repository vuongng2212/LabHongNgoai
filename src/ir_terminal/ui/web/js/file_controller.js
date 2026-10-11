// File Transfer Viewport Controller with dynamic telemetry updates & hard-drive history loading
// History table rendering/filtering lives in transfer_history.js (window.TransferHistory).
let selectedFilePath = '';

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

  const history = window.TransferHistory
    ? window.TransferHistory.create({ table: historyTable, searchInput: searchInput })
    : null;

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
      if (history) history.handleRowAction(actionBtn);
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

  // Bridge connects asynchronously (QWebChannel); shared helper wires once ready.
  function tryWireBridge() {
    if (window.SpectraLinkBridge) window.SpectraLinkBridge.onReady('file', wireBridgeSignals);
    else if (!window.bridge) setTimeout(tryWireBridge, 300);
    else wireBridgeSignals();
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
      if (history) history.pushSuccess(newRec);
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
    if (history) history.reload();
  }

  tryWireBridge();
}

document.addEventListener('DOMContentLoaded', () => {
  setTimeout(initFileController, 100);
});
