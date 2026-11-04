let selectedFilePath = '';

function initFileController() {
  const historyTable = document.querySelector('#file-history-body');
  const searchInput = document.querySelector('#view-file input[placeholder*="Filter transmissions"]');

  const activeFileName = document.getElementById('file-active-name');
  const activeFileSize = document.getElementById('file-active-size');
  const activeStatusBadge = document.getElementById('file-active-status');
  const progressBar = document.getElementById('file-progress-bar');
  const progressPct = document.getElementById('file-progress-pct');
  const speedLabel = document.getElementById('file-speed');
  const etaLabel = document.getElementById('file-eta');
  const packetsLabel = document.getElementById('file-packets');

  function arqLabel(state) {
    const key = String(state || '').toUpperCase();
    if (key.startsWith('ARQ ')) return key;
    const map = {
      IDLE: 'ARQ IDLE',
      HANDSHAKING: 'ARQ SYNC',
      SYNC: 'ARQ SYNC',
      TRANSFERRING: 'ARQ TX',
      STREAMING: 'ARQ TX',
      TX: 'ARQ TX',
      RECEIVING: 'ARQ RX',
      RX: 'ARQ RX',
      FINALIZING: 'ARQ FINALIZE',
      COMPLETED: 'ARQ COMPLETE',
      COMPLETE: 'ARQ COMPLETE',
      FAILED: 'ARQ FAILED',
      ABORTED: 'ARQ ABORTED',
      READY: 'ARQ READY'
    };
    return map[key] || 'ARQ ' + key;
  }

  function arqTone(state) {
    const key = String(state || '').toUpperCase();
    if (key === 'COMPLETED' || key === 'COMPLETE') return 'success';
    if (key === 'FAILED') return 'error';
    if (key === 'ABORTED' || key === 'IDLE') return 'neutral';
    if (key === 'FINALIZING') return 'warning';
    return 'info';
  }

  function setActiveStatus(label, tone) {
    const resolvedTone = tone || arqTone(label);
    const resolvedLabel = arqLabel(label);
    if (window.UIBadge) {
      window.UIBadge.set('file-active-status', { tone: resolvedTone, label: resolvedLabel });
      return;
    }
    if (!activeStatusBadge) return;
    activeStatusBadge.dataset.tone = resolvedTone;
    const labelElem = activeStatusBadge.querySelector('.ui-badge__label');
    if (labelElem) labelElem.textContent = resolvedLabel;
  }

  const history = window.TransferHistory
    ? window.TransferHistory.create({ table: historyTable, searchInput: searchInput })
    : null;

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

    if (btn.closest('#modal-overlay, #toast-container, #settings-overlay')) return;

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
          setActiveStatus('ABORTED');
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
    setActiveStatus('READY');
    if (progressBar) progressBar.style.width = '0%';
    if (progressPct) progressPct.innerText = '0%';
    if (window.UIFeedback) window.UIFeedback.showToast('info', 'File selected', fname + ' ready for optical transmission.');
    console.log('Selected file for optical transmission:', path);
  }

  function tryWireBridge() {
    if (window.SpectraLinkBridge) window.SpectraLinkBridge.onReady('file', wireBridgeSignals);
    else if (!window.bridge) setTimeout(tryWireBridge, 300);
    else wireBridgeSignals();
  }

  function wireBridgeSignals() {
    let progressFrame = null;
    let pendingProgress = null;

    function renderProgress(pct, speedKbps, etaSec, curChunk, totalChunks) {
      if (progressBar) progressBar.style.width = `${pct}%`;
      if (progressPct) progressPct.innerText = `${pct}%`;
      if (speedLabel) speedLabel.innerHTML = `${speedKbps.toFixed(1)} <span class="text-label-sm font-label-sm font-normal text-on-surface-variant">KB/s</span>`;
      if (etaLabel) etaLabel.innerText = `${Math.ceil(etaSec)}s`;
      if (packetsLabel) packetsLabel.innerHTML = `${curChunk}<span class="text-label-sm font-label-sm font-normal text-outline">/${totalChunks}</span>`;
      setActiveStatus(pct >= 100 ? 'COMPLETED' : 'TRANSFERRING');
    }

    window.bridge.transferProgress.connect((pct, speedKbps, etaSec, curChunk, totalChunks) => {
      pendingProgress = [pct, speedKbps, etaSec, curChunk, totalChunks];
      if (progressFrame !== null) return;
      progressFrame = requestAnimationFrame(() => {
        progressFrame = null;
        const next = pendingProgress;
        pendingProgress = null;
        if (next) renderProgress(next[0], next[1], next[2], next[3], next[4]);
      });
    });

    window.bridge.transferCompleted.connect((filename, bytesSent, elapsedSec, crcHex) => {
      const newRec = {
        filename: filename,
        size_bytes: bytesSent,
        crc32_hex: crcHex,
        status: 'SUCCESS',
        direction: 'TX',
        duration_sec: elapsedSec,
        timestamp: 'Just now'
      };
      if (history) history.pushSuccess(newRec);
      setActiveStatus('COMPLETED');
      if (window.UIFeedback) {
        window.UIFeedback.showToast(
          'success',
          'Transfer complete',
          `${filename} verified over optical link in ${elapsedSec.toFixed(1)}s • CRC-32 ${crcHex}`,
          { durationMs: 2800 }
        );
      }
    });

    window.bridge.transferFailed.connect((reason) => {
      console.error(`File Transfer Failed: ${reason}`);
      setActiveStatus('FAILED');
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

    window.bridge.transferStateChanged.connect((state) => {
      setActiveStatus(state);
    });

    if (history) history.reload();
  }

  tryWireBridge();
}

document.addEventListener('DOMContentLoaded', () => {
  setTimeout(initFileController, 100);
});
