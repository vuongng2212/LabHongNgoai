(function () {
  let defaultDir = '';
  let debugExpanded = false;
  let physicalBaud = 115200;
  let opticalBaud = 9600;
  let autoScroll = true;
  const debugLogs = [];
  const MAX_DEBUG_LOGS = 300;

  function $(id) { return document.getElementById(id); }

  function showOverlay() {
    const overlay = $('settings-overlay');
    if (overlay) {
      overlay.classList.remove('hidden');
      overlay.classList.add('flex');
    }
  }

  function hideOverlay() {
    const overlay = $('settings-overlay');
    if (overlay) {
      overlay.classList.add('hidden');
      overlay.classList.remove('flex');
    }
    const err = $('settings-error');
    if (err) { err.classList.add('hidden'); err.innerText = ''; }
  }

  function showError(message) {
    const err = $('settings-error');
    if (err) {
      err.innerText = message;
      err.classList.remove('hidden');
    }
  }

  function toast(type, title, message) {
    if (window.UIFeedback) window.UIFeedback.showToast(type, title, message);
  }

  function applyUnlimitedHint() {
    const hint = $('file-limit-hint');
    if (hint) {
      hint.innerText = 'Drop file to transmit across infrared beam (Unlimited size)';
    }
  }

  function fillForm(config) {
    const dirInput = $('settings-dir');
    if (!config) return;
    if (dirInput && config.transfers_dir) dirInput.value = config.transfers_dir;
    if (config.default_transfers_dir) defaultDir = config.default_transfers_dir;
    applyUnlimitedHint();
  }

  function loadSettings() {
    if (!window.bridge || typeof window.bridge.getSettings !== 'function') {
      toast('warning', 'Backend not connected', 'Settings need the SpectraLink Desktop app.');
      return;
    }
    window.bridge.getSettings((raw) => {
      try {
        fillForm(JSON.parse(raw));
      } catch (e) {
        showError('Could not parse settings from backend.');
      }
    });
  }

  function openSettings() {
    showOverlay();
    loadSettings();
    updateBaudDisplay();
    const dirInput = $('settings-dir');
    if (dirInput) dirInput.focus();
  }

  function persistDir(dir, successTitle) {
    if (!window.bridge || typeof window.bridge.saveSettings !== 'function') {
      showError('Backend not connected.');
      return;
    }
    const payload = JSON.stringify({ transfers_dir: dir });
    window.bridge.saveSettings(payload, (raw) => {
      let result = null;
      try { result = JSON.parse(raw); } catch (e) { result = null; }
      if (result && result.ok) {
        if (result.default_transfers_dir) defaultDir = result.default_transfers_dir;
        hideOverlay();
        applyUnlimitedHint();
        toast('success', successTitle, 'Unlimited transfer size • ' + (result.transfers_dir || dir));
      } else {
        showError((result && result.error) || 'Failed to save settings.');
      }
    });
  }

  function saveSettings() {
    const dirInput = $('settings-dir');
    const dir = dirInput ? dirInput.value.trim() : '';
    if (!dir) {
      showError('Type or browse a receive directory before saving.');
      return;
    }
    persistDir(dir, 'Settings saved');
  }

  function resetToDefault() {
    const dirInput = $('settings-dir');
    const applyDefault = (dir) => {
      if (!dir) { showError('Could not resolve the default directory.'); return; }
      if (dirInput) dirInput.value = dir;
      persistDir(dir, 'Reset to default');
    };
    if (defaultDir) { applyDefault(defaultDir); return; }
    if (!window.bridge || typeof window.bridge.getSettings !== 'function') {
      showError('Backend not connected.');
      return;
    }
    window.bridge.getSettings((raw) => {
      try {
        const config = JSON.parse(raw);
        if (config && config.default_transfers_dir) defaultDir = config.default_transfers_dir;
        applyDefault(defaultDir);
      } catch (e) {
        showError('Could not resolve the default directory.');
      }
    });
  }

  function browseDirectory() {
    if (!window.bridge || typeof window.bridge.browseTransfersDir !== 'function') {
      toast('warning', 'Backend not connected', 'Directory picker needs the Desktop app.');
      return;
    }
    window.bridge.browseTransfersDir((path) => {
      if (path) {
        const dirInput = $('settings-dir');
        if (dirInput) dirInput.value = path;
      }
    });
  }

  function updateBaudDisplay() {
    const phys = $('debug-physical-baud');
    if (phys) phys.innerText = physicalBaud + ' bps';
    const opt = $('debug-optical-baud');
    if (opt) opt.innerText = opticalBaud + ' bps';
  }

  function toggleDebugPanel() {
    debugExpanded = !debugExpanded;
    const panel = $('settings-debug-panel');
    const chevron = $('debug-toggle-chevron');
    if (panel) {
      if (debugExpanded) {
        panel.classList.remove('hidden');
        panel.classList.add('flex');
        if (chevron) chevron.classList.add('rotate-180');
        updateBaudDisplay();
        const consoleEl = $('debug-log-console');
        if (consoleEl && autoScroll) {
          consoleEl.scrollTop = consoleEl.scrollHeight;
        }
      } else {
        panel.classList.add('hidden');
        panel.classList.remove('flex');
        if (chevron) chevron.classList.remove('rotate-180');
      }
    }
  }

  function formatTimestamp(date) {
    const d = date || new Date();
    const pad2 = (n) => String(n).padStart(2, '0');
    const pad3 = (n) => String(n).padStart(3, '0');
    return pad2(d.getHours()) + ':' + pad2(d.getMinutes()) + ':' + pad2(d.getSeconds()) + '.' + pad3(d.getMilliseconds());
  }

  function decodeRawBytes(hexStr, direction) {
    const parts = (hexStr || '').trim().split(/\s+/).filter(Boolean);
    if (!parts.length) {
      return { label: 'EMPTY', tone: 'neutral' };
    }
    const bytes = parts.map((p) => parseInt(p, 16));
    if (bytes.length >= 2 && bytes[0] === 0x55 && bytes[1] === 0xAA) {
      if (bytes.length >= 6) {
        const seq = bytes[2];
        const cmd = bytes[3];
        const len = bytes[4] | (bytes[5] << 8);
        const opcodeMap = {
          0x01: 'DATA_CHUNK',
          0x02: 'DATA_TEXT',
          0x10: 'ACK',
          0x11: 'NACK',
          0x20: 'PING',
          0x21: 'PONG',
          0x30: 'TRANSFER_START',
          0x31: 'TRANSFER_END',
          0x32: 'TRANSFER_PAUSE',
          0x34: 'START_LARGE',
          0x35: 'CHUNK_LARGE',
          0x36: 'END_LARGE',
        };
        const opName = opcodeMap[cmd] || ('CMD 0x' + cmd.toString(16).toUpperCase());
        const hasEof = bytes.length >= 12 && bytes[bytes.length - 2] === 0xAA && bytes[bytes.length - 1] === 0x55;
        if (hasEof) {
          return {
            label: 'FRAME: ' + opName + ' [seq #' + seq + ', ' + len + 'B]',
            tone: 'success',
          };
        }
        return {
          label: 'PARTIAL: ' + opName + ' [seq #' + seq + ', ' + len + 'B]',
          tone: 'warning',
        };
      }
      return { label: 'SOF DETECTED', tone: 'warning' };
    }

    let ascii = '';
    for (let i = 0; i < bytes.length; i++) {
      const b = bytes[i];
      if (b >= 32 && b <= 126) ascii += String.fromCharCode(b);
      else if (b === 10 || b === 13) ascii += ' ';
      else { ascii = ''; break; }
    }
    ascii = ascii.trim();
    if (/^(SET_BAUD|BAUD_OK|NOTIFY_BAUD|BAUD_ERR):/.test(ascii)) {
      return {
        label: 'CTRL: ' + ascii,
        tone: 'info',
      };
    }

    if (direction === 'RX') {
      return {
        label: 'UNPARSED / BROKEN RAW BYTES',
        tone: 'error',
      };
    }
    return {
      label: 'RAW BYTES (' + bytes.length + 'B)',
      tone: 'neutral',
    };
  }

  function getDecodeBadgeStyle(tone) {
    if (tone === 'success') {
      return 'border-emerald-500/50 bg-emerald-950/70 text-emerald-300';
    }
    if (tone === 'warning') {
      return 'border-amber-500/50 bg-amber-950/70 text-amber-300';
    }
    if (tone === 'info') {
      return 'border-sky-500/50 bg-sky-950/70 text-sky-300';
    }
    if (tone === 'error') {
      return 'border-rose-500/60 bg-rose-950/80 text-rose-300 font-semibold';
    }
    return 'border-slate-700 bg-slate-800/60 text-slate-300';
  }

  function appendDebugLog(direction, hexStr) {
    const raw = (hexStr || '').trim();
    if (!raw) return;
    const parts = raw.split(/\s+/).filter(Boolean);
    const time = formatTimestamp();
    const byteCount = parts.length;
    const decode = decodeRawBytes(raw, direction);

    const entry = {
      time: time,
      dir: direction,
      byteCount: byteCount,
      hex: raw,
      decode: decode,
    };

    debugLogs.push(entry);
    if (debugLogs.length > MAX_DEBUG_LOGS) {
      debugLogs.shift();
    }

    const counter = $('debug-log-count');
    if (counter) counter.innerText = debugLogs.length + ' logs';

    const consoleEl = $('debug-log-console');
    if (!consoleEl) return;

    const emptyMsg = $('debug-log-empty');
    if (emptyMsg) emptyMsg.style.display = 'none';

    const dirBadgeClass = direction === 'TX'
      ? 'bg-blue-600/30 text-blue-300 border border-blue-500/40'
      : 'bg-amber-600/30 text-amber-300 border border-amber-500/40';

    const decodeBadgeClass = getDecodeBadgeStyle(decode.tone);

    const row = document.createElement('div');
    row.className = 'flex flex-col gap-0.5 border-b border-slate-800/80 pb-1.5 pt-0.5';
    row.innerHTML =
      '<div class="flex items-center justify-between gap-2">' +
        '<div class="flex items-center gap-1.5 flex-wrap">' +
          '<span class="text-slate-400 font-mono text-[10px]">' + time + '</span>' +
          '<span class="px-1.5 py-0.2 rounded text-[10px] font-bold ' + dirBadgeClass + '">[' + direction + ']</span>' +
          '<span class="text-slate-400 font-mono text-[10px]">(' + byteCount + ' B)</span>' +
        '</div>' +
        '<span class="px-1.5 py-0.2 rounded text-[10px] font-mono border ' + decodeBadgeClass + '">' + decode.label + '</span>' +
      '</div>' +
      '<div class="font-mono text-[11px] text-slate-200 break-all select-text pl-1">' + raw + '</div>';

    consoleEl.appendChild(row);

    while (consoleEl.children.length > MAX_DEBUG_LOGS + 1) {
      if (consoleEl.firstChild && consoleEl.firstChild !== emptyMsg) {
        consoleEl.removeChild(consoleEl.firstChild);
      } else {
        break;
      }
    }

    if (autoScroll) {
      consoleEl.scrollTop = consoleEl.scrollHeight;
    }
  }

  function clearDebugLogs() {
    debugLogs.length = 0;
    const consoleEl = $('debug-log-console');
    if (consoleEl) {
      consoleEl.innerHTML = '<div id="debug-log-empty" class="text-slate-500 py-8 text-center italic">Waiting for serial traffic (TX/RX)...</div>';
    }
    const counter = $('debug-log-count');
    if (counter) counter.innerText = '0 logs';
    toast('info', 'Logs cleared', 'Live serial log history cleared.');
  }

  function copyDebugLogs() {
    if (!debugLogs.length) {
      toast('info', 'No logs', 'Serial log history is empty.');
      return;
    }
    const text = debugLogs.map((entry) => {
      return '[' + entry.time + '] [' + entry.dir + '] (' + entry.byteCount + ' B) [' + entry.decode.label + '] ' + entry.hex;
    }).join('\n');

    if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
      navigator.clipboard.writeText(text).then(() => {
        toast('success', 'Logs copied', debugLogs.length + ' entries copied to clipboard.');
      }).catch(() => {
        fallbackCopy(text);
      });
    } else {
      fallbackCopy(text);
    }
  }

  function fallbackCopy(text) {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.left = '-9999px';
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    try {
      document.execCommand('copy');
      toast('success', 'Logs copied', debugLogs.length + ' entries copied to clipboard.');
    } catch (err) {
      toast('error', 'Copy failed', 'Could not copy logs to clipboard.');
    }
    document.body.removeChild(ta);
  }

  function setupBridgeDebugListeners() {
    if (!window.bridge) return;
    if (window.bridge.rawTxData && typeof window.bridge.rawTxData.connect === 'function') {
      window.bridge.rawTxData.connect((hex) => {
        appendDebugLog('TX', hex);
      });
    }
    if (window.bridge.rawRxData && typeof window.bridge.rawRxData.connect === 'function') {
      window.bridge.rawRxData.connect((hex) => {
        appendDebugLog('RX', hex);
      });
    }
    if (window.bridge.opticalBaudChanged && typeof window.bridge.opticalBaudChanged.connect === 'function') {
      window.bridge.opticalBaudChanged.connect((baud) => {
        opticalBaud = baud;
        updateBaudDisplay();
      });
    }
    if (window.bridge.portConnected && typeof window.bridge.portConnected.connect === 'function') {
      window.bridge.portConnected.connect((tx, rx, baud) => {
        if (baud) opticalBaud = baud;
        updateBaudDisplay();
      });
    }
    if (window.bridge.getLaunchConfig && typeof window.bridge.getLaunchConfig === 'function') {
      window.bridge.getLaunchConfig((raw) => {
        try {
          const cfg = JSON.parse(raw);
          if (cfg.physical_baudrate) physicalBaud = cfg.physical_baudrate;
          if (cfg.default_optical_baudrate) opticalBaud = cfg.default_optical_baudrate;
          else if (cfg.baud) opticalBaud = cfg.baud;
          updateBaudDisplay();
        } catch (e) {}
      });
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    document.addEventListener('click', (e) => {
      const target = e.target;
      if (!target || !target.closest) return;
      if (target.closest('#btn-top-settings')) { openSettings(); return; }
      if (target.closest('#settings-close') || target.closest('#settings-cancel')) { hideOverlay(); return; }
      if (target.closest('#settings-backdrop')) { hideOverlay(); return; }
      if (target.closest('#settings-save')) { saveSettings(); return; }
      if (target.closest('#settings-browse')) { browseDirectory(); return; }
      if (target.closest('#settings-reset')) { resetToDefault(); return; }
      if (target.closest('#btn-toggle-debug')) { toggleDebugPanel(); return; }
      if (target.closest('#btn-clear-debug-logs')) { clearDebugLogs(); return; }
      if (target.closest('#btn-copy-debug-logs')) { copyDebugLogs(); return; }
    });

    const scrollChk = $('chk-autoscroll-debug');
    if (scrollChk) {
      scrollChk.addEventListener('change', (e) => {
        autoScroll = !!e.target.checked;
      });
    }

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') hideOverlay();
      if (e.key === 'Enter' && $('settings-overlay') && !$('settings-overlay').classList.contains('hidden')) {
        const active = document.activeElement;
        if (active && active.id === 'settings-dir') saveSettings();
      }
    });

    let bridgeRetries = 0;
    function pollBridge() {
      if (window.bridge) {
        setupBridgeDebugListeners();
        if (typeof window.bridge.getSettings === 'function') {
          window.bridge.getSettings((raw) => {
            try {
              const config = JSON.parse(raw);
              const dirInput = $('settings-dir');
              if (dirInput && config.transfers_dir) dirInput.value = config.transfers_dir;
              if (config.default_transfers_dir) defaultDir = config.default_transfers_dir;
            } catch (e) {}
            applyUnlimitedHint();
          });
        }
      } else if (bridgeRetries < 20) {
        bridgeRetries++;
        setTimeout(pollBridge, 300);
      }
    }
    setTimeout(pollBridge, 300);
  });

  window.TransferSettings = {
    open: openSettings,
    close: hideOverlay,
    save: saveSettings,
    reset: resetToDefault,
    toggleDebug: toggleDebugPanel,
    clearDebugLogs: clearDebugLogs,
    copyDebugLogs: copyDebugLogs,
    appendDebugLog: appendDebugLog,
    decodeRawBytes: decodeRawBytes,
  };
})();
