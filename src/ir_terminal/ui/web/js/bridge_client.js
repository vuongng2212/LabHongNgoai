let bridge = null;
let selectedBaud = 9600;
let pendingBaud = null;
let currentBaudOptions = [2400, 4800, 9600, 19200];
let portSelectionInitialized = false;

const DEFAULT_BAUDRATES = [2400, 4800, 9600, 19200];
const FIXED_PHYSICAL_BAUD = 115200;

if (typeof QWebChannel !== 'undefined' && typeof qt !== 'undefined' && qt.webChannelTransport) {
  try {
    new QWebChannel(qt.webChannelTransport, (channel) => {
      bridge = channel.objects.bridge;
      window.bridge = bridge;
      console.log('QWebChannel Bridge Connected Successfully!');
      initBridgeListeners();
      applyLaunchConfig();
    });
  } catch (err) {
    console.warn('QWebChannel setup failed, running without backend bridge:', err);
  }
} else {
  console.warn('No Qt WebChannel transport — running in UI-only mode (buttons show guidance toasts).');
}

let pendingConnectBtn = null;
let pendingConnectPort = '';
let connectFailTimer = null;
let pendingPingBtns = [];
let pingFailTimer = null;

function toast(type, title, message, opts) {
  if (window.UIFeedback) window.UIFeedback.showToast(type, title, message, opts);
}

function clearConnectLoading() {
  if (pendingConnectBtn && window.UIFeedback) window.UIFeedback.setButtonLoading(pendingConnectBtn, false);
  pendingConnectBtn = null;
  if (connectFailTimer) { clearTimeout(connectFailTimer); connectFailTimer = null; }
}

function clearPingLoading() {
  if (window.UIFeedback) pendingPingBtns.forEach((b) => window.UIFeedback.setButtonLoading(b, false));
  pendingPingBtns = [];
  if (pingFailTimer) { clearTimeout(pingFailTimer); pingFailTimer = null; }
}

function baudButtonClass(isActive, isPending) {
  const base = 'flex-1 min-w-[64px] py-1 text-center font-mono-data text-mono-data rounded-md cursor-pointer';
  if (isActive) return base + ' bg-white text-primary font-semibold shadow-[0_1px_3px_rgba(0,0,0,0.08)]';
  if (isPending) return base + ' bg-[#E0F2FE] text-primary font-semibold ring-1 ring-primary/30';
  return base + ' text-on-surface-variant hover:text-on-surface hover:bg-white/50 transition-colors';
}

function renderBaudrateSelector(baudrates, activeBaud, pendingValue) {
  const container = document.getElementById('baudrate-selector-container');
  if (!container) return;
  const parsed = (Array.isArray(baudrates) ? baudrates : [])
    .map((value) => parseInt(value, 10))
    .filter((value) => Number.isFinite(value) && value > 0);
  const options = parsed.length ? Array.from(new Set(parsed)) : DEFAULT_BAUDRATES.slice();
  currentBaudOptions = options;
  const requested = parseInt(activeBaud, 10);
  const active = options.includes(requested) ? requested : (options.includes(9600) ? 9600 : options[0]);
  const requestedPending = parseInt(pendingValue, 10);
  const pending = options.includes(requestedPending) && requestedPending !== active ? requestedPending : null;
  selectedBaud = active;
  pendingBaud = pending;
  container.innerHTML = '';
  options.forEach((baud) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.dataset.baud = String(baud);
    btn.innerText = String(baud);
    btn.className = baudButtonClass(baud === active, baud === pending);
    if (baud === pending) btn.dataset.pending = 'true';
    container.appendChild(btn);
  });
  const source = document.getElementById('baudrate-source-label');
  if (source) source.innerText = options.length + ' OPTICAL RATES • DIP SWITCH';
  updateBaudrateDisplays(active);
}

function updateBaudrateDisplays(baud) {
  setText('footer-baud-label', baud ? baud + ' bps' : '--');
  const physical = document.getElementById('hw-physical-baud');
  if (physical) physical.innerText = FIXED_PHYSICAL_BAUD + ' bps FIXED';
}

function loadBaudrateOptions(fallbackList, activeBaud) {
  if (!bridge || typeof bridge.getAvailableBaudrates !== 'function') {
    renderBaudrateSelector(fallbackList, activeBaud, pendingBaud);
    return;
  }
  bridge.getAvailableBaudrates((baudratesJson) => {
    let values = [];
    try { values = JSON.parse(baudratesJson); } catch (e) { values = []; }
    renderBaudrateSelector(Array.isArray(values) && values.length ? values : fallbackList, activeBaud, pendingBaud);
  });
}

function fillPortSelect(select, entries, preferred) {
  if (!select) return '';
  const names = entries.map((entry) => entry.name);
  select.innerHTML = '';
  entries.forEach((entry) => {
    const opt = document.createElement('option');
    opt.value = entry.name;
    opt.innerText = entry.label;
    opt.title = entry.label;
    select.appendChild(opt);
  });
  const target = preferred && names.includes(preferred) ? preferred : (names[0] || '');
  if (target) select.value = target;
  return target;
}

function pickRxPort(names, txPort) {
  return txPort;
}

function parsePortEntries(ports) {
  return ports.map((p) => {
    if (typeof p === 'string') {
      return { name: p, label: p.includes('MOCK') ? p + ' (Virtual Loopback)' : p };
    }
    const name = p.device || p.name || '';
    return { name: name, label: p.label || p.description || name };
  }).filter((entry) => entry.name);
}

function fallbackEntries() {
  return [{ name: 'MOCK_COM_LOOPBACK', label: 'MOCK_COM_LOOPBACK (Virtual Loopback)' }];
}

function setText(id, text) {
  const el = document.getElementById(id);
  if (el && el.textContent !== text) el.textContent = text;
}

function setTone(id, tone) {
  const el = document.getElementById(id);
  if (el && el.dataset.tone !== tone) el.dataset.tone = tone;
}

function setRibbonText(text) {
  const el = document.getElementById('ribbon-text');
  if (!el) return;
  if (el.textContent !== text) el.textContent = text;
  if (el.title !== text) el.title = text;
}

function setConnectedUi(txPort, rxPort, baud) {
  const summary = 'TX: ' + txPort + ' • RX: ' + rxPort;
  setRibbonText('STM32 Optical Bridge • ' + summary + ' • UART ' + FIXED_PHYSICAL_BAUD + ' fixed • Optical ' + baud + ' bps');
  setTone('ribbon-badge', 'success');

  const footerHealthDot = document.getElementById('footer-health-dot');
  if (footerHealthDot) footerHealthDot.className = 'w-2 h-2 rounded-full bg-secondary shrink-0';
  setText('footer-health-text', 'ONLINE');
  const footerHealth = document.getElementById('footer-health-text');
  if (footerHealth) footerHealth.className = 'font-mono-caption text-mono-caption font-medium text-secondary truncate';
  setText('footer-port-label', summary);
  updateBaudrateDisplays(baud);

  if (window.UIBadge) {
    window.UIBadge.set('hw-header-link-badge', { tone: 'success', label: 'LINK CONNECTED' });
    window.UIBadge.set('hw-tx-status', { tone: 'success', label: 'TX OPEN' });
    window.UIBadge.set('hw-rx-status', { tone: 'success', label: 'RX OPEN' });
    window.UIBadge.set('hw-rtt-state', { tone: 'neutral', label: 'READY' });
    window.UIBadge.set('chat-link-state', { tone: 'success', label: 'LINK CONNECTED' });
  }

  const connBtn = document.getElementById('btn-connect');
  if (connBtn) {
    connBtn.dataset.state = 'connected';
    if (connBtn.textContent !== 'Disconnect') connBtn.textContent = 'Disconnect';
  }
}

function setConnectingUi(txPort, rxPort) {
  setRibbonText('STM32 Optical Bridge • Opening ' + txPort + ' / ' + rxPort);
  setTone('ribbon-badge', 'info');
  if (window.UIBadge) {
    window.UIBadge.set('hw-header-link-badge', { tone: 'info', label: 'CONNECTING' });
    window.UIBadge.set('chat-link-state', { tone: 'neutral', label: 'LINK STANDBY' });
  }
}

function setDisconnectedUi() {
  pendingBaud = null;
  renderBaudrateSelector(currentBaudOptions, selectedBaud, null);
  setRibbonText('STM32 Optical Bridge • Disconnected');
  setTone('ribbon-badge', 'neutral');

  const footerHealthDot = document.getElementById('footer-health-dot');
  if (footerHealthDot) footerHealthDot.className = 'w-2 h-2 rounded-full bg-outline shrink-0';
  setText('footer-health-text', 'OFFLINE');
  const footerHealth = document.getElementById('footer-health-text');
  if (footerHealth) footerHealth.className = 'font-mono-caption text-mono-caption font-medium text-outline truncate';
  setText('footer-port-label', 'Disconnected');
  setText('footer-baud-label', '--');

  if (window.UIBadge) {
    window.UIBadge.set('hw-header-link-badge', { tone: 'neutral', label: 'DISCONNECTED' });
    window.UIBadge.set('hw-tx-status', { tone: 'neutral', label: 'TX IDLE' });
    window.UIBadge.set('hw-rx-status', { tone: 'neutral', label: 'RX IDLE' });
    window.UIBadge.set('hw-rtt-state', { tone: 'neutral', label: 'IDLE' });
    window.UIBadge.set('chat-link-state', { tone: 'neutral', label: 'LINK STANDBY' });
  }

  const connBtn = document.getElementById('btn-connect');
  if (connBtn) {
    connBtn.dataset.state = 'disconnected';
    if (connBtn.textContent !== 'Connect') connBtn.textContent = 'Connect';
  }
}

function initBridgeListeners() {
  if (!bridge) return;

  bridge.portConnected.connect((txPort, rxPort, baud) => {
    clearConnectLoading();
    requestAnimationFrame(() => {
      setConnectedUi(txPort, rxPort, baud);
      toast(
        'success',
        'Link connected',
        'TX: ' + txPort + ' • RX: ' + rxPort + ' @ ' + baud + ' bps • 8-N-1',
        { durationMs: 2800 }
      );
    });
  });

  if (bridge.opticalBaudChanged && bridge.opticalBaudChanged.connect) {
    bridge.opticalBaudChanged.connect((baud) => {
      const value = parseInt(baud, 10);
      if (!Number.isFinite(value)) return;
      selectedBaud = value;
      pendingBaud = null;
      renderBaudrateSelector(currentBaudOptions, value, null);
      updateBaudrateDisplays(value);
    });
  }

  if (bridge.opticalBaudPending && bridge.opticalBaudPending.connect) {
    bridge.opticalBaudPending.connect((baud) => {
      const value = parseInt(baud, 10);
      if (!Number.isFinite(value)) return;
      pendingBaud = value;
      renderBaudrateSelector(currentBaudOptions, selectedBaud, value);
    });
  }

  if (bridge.baudControlStatus && bridge.baudControlStatus.connect) {
    bridge.baudControlStatus.connect((status, detail) => {
      if (status === 'ok') {
        toast('success', 'Optical baudrate confirmed', detail, { durationMs: 2200 });
      } else if (status === 'notify') {
        toast('info', 'Optical baudrate changed', 'Hardware DIP switch selected ' + detail, { durationMs: 3000 });
      } else if (status === 'error') {
        pendingBaud = null;
        renderBaudrateSelector(currentBaudOptions, selectedBaud, null);
        toast('error', 'Optical baudrate rejected', detail);
      } else if (status === 'timeout') {
        pendingBaud = null;
        renderBaudrateSelector(currentBaudOptions, selectedBaud, null);
        toast('warning', 'Optical baudrate timeout', detail + ' — link kept open');
      }
    });
  }

  bridge.errorOccurred.connect((message) => {
    const wasConnecting = !!pendingConnectBtn;
    const ports = pendingConnectPort;
    clearConnectLoading();
    clearPingLoading();
    console.error('Bridge error:', message);
    if (wasConnecting) {
      setTone('ribbon-badge', 'error');
      if (window.UIBadge) window.UIBadge.set('hw-header-link-badge', { tone: 'error', label: 'ERROR' });
    }
    if (wasConnecting && window.UIFeedback) {
      window.UIFeedback.showModal({
        variant: 'error',
        title: 'Connection failed',
        message: 'Could not open the selected TX/RX ports. Check cable, driver, or close other serial tools.',
        details: (ports ? ports + ' • ' : '') + message,
        actions: [
          { id: 'close', label: 'Close', kind: 'secondary' },
          { id: 'retry', label: 'Retry', kind: 'primary', onClick: (close) => { close(); refreshPortList(); } },
        ],
      });
    } else {
      toast('error', 'Link error', message);
    }
  });

  bridge.portDisconnected.connect(() => {
    const wasConnecting = !!pendingConnectBtn;
    clearConnectLoading();
    clearPingLoading();
    setDisconnectedUi();
    if (!wasConnecting) toast('info', 'Disconnected', 'Serial link closed.');
  });

  bridge.pingResult.connect((rtt, seq) => {
    clearPingLoading();
    setText('hw-rtt-value', rtt.toFixed(1) + ' ms');
    if (window.UIBadge) {
      window.UIBadge.set('hw-rtt-state', {
        tone: window.UIBadge.toneForRtt(rtt),
        label: 'MEASURED'
      });
    }
    toast('success', 'Ping echo received', 'Seq #' + seq + ' • RTT ' + rtt.toFixed(1) + ' ms', { durationMs: 2800 });
    console.log('Ping Echo Received: seq #' + seq + ' | RTT: ' + rtt.toFixed(1) + ' ms');
  });

  bridge.healthResult.connect((rtt) => {
    setText('hw-rtt-value', rtt.toFixed(1) + ' ms');
    if (window.UIBadge) {
      window.UIBadge.set('hw-rtt-state', {
        tone: window.UIBadge.toneForRtt(rtt),
        label: 'MEASURED'
      });
    }
  });

  let txTimeout = null;
  let rxTimeout = null;

  bridge.rawTxData.connect(() => {
    if (window.UIBadge) window.UIBadge.setActive('hw-tx-status', true);
    if (txTimeout) clearTimeout(txTimeout);
    txTimeout = setTimeout(() => {
      if (window.UIBadge) window.UIBadge.setActive('hw-tx-status', false);
    }, 120);
  });

  bridge.rawRxData.connect(() => {
    if (window.UIBadge) window.UIBadge.setActive('hw-rx-status', true);
    if (rxTimeout) clearTimeout(rxTimeout);
    rxTimeout = setTimeout(() => {
      if (window.UIBadge) window.UIBadge.setActive('hw-rx-status', false);
    }, 120);
  });

  if (typeof bridge.portsChanged !== 'undefined' && bridge.portsChanged) {
    bridge.portsChanged.connect(() => {
      refreshPortList();
    });
  }
}

function applyLaunchConfig() {
  if (!bridge || typeof bridge.getLaunchConfig !== 'function') {
    loadBaudrateOptions(DEFAULT_BAUDRATES, selectedBaud);
    refreshPortList();
    return;
  }
  bridge.getLaunchConfig((configJson) => {
    let cfg = {};
    try { cfg = JSON.parse(configJson); } catch (e) { cfg = {}; }
    window.launchConfig = cfg;
    if (cfg.window_title) document.title = cfg.window_title;
    const instanceLabel = document.getElementById('chrome-instance-label');
    if (instanceLabel) instanceLabel.innerText = cfg.instance ? 'NODE ' + cfg.instance : 'IR-OWC';
    const ribbon = document.getElementById('ribbon-text');
    if (ribbon && cfg.display_name) {
      const role = cfg.role ? ' • ' + cfg.role : '';
      setRibbonText(cfg.display_name + role + ' • Waiting for link');
    }
    setTone('ribbon-badge', 'neutral');
    const baudList = Array.isArray(cfg.supported_baudrates) ? cfg.supported_baudrates : DEFAULT_BAUDRATES;
    const initialBaud = cfg.default_optical_baudrate || cfg.default_baudrate || cfg.baud || selectedBaud;
    if (cfg.physical_baudrate) window.fixedPhysicalBaud = cfg.physical_baudrate;
    loadBaudrateOptions(baudList, initialBaud);
    refreshPortList(null, cfg.tx_port || cfg.preferred_port || null, cfg.rx_port || null);
  });
}

function refreshPortList(triggerBtn, preferredTx, preferredRx) {
  const cfg = window.launchConfig || {};
  const targetTx = preferredTx || cfg.tx_port || cfg.preferred_port || null;
  const targetRx = preferredRx || cfg.rx_port || null;

  if (!bridge) {
    let entries = fallbackEntries();
    if (targetTx && !entries.some((entry) => entry.name === targetTx)) {
      entries.unshift({ name: targetTx, label: targetTx + ' (Launch Port)' });
    }
    if (targetRx && !entries.some((entry) => entry.name === targetRx)) {
      entries.unshift({ name: targetRx, label: targetRx + ' (Launch Port)' });
    }
    const names = entries.map((entry) => entry.name);
    const txValue = fillPortSelect(document.getElementById('select-port-tx'), entries, targetTx);
    fillPortSelect(document.getElementById('select-port-rx'), entries, targetRx || pickRxPort(names, txValue));
    return;
  }
  if (triggerBtn && window.UIFeedback) window.UIFeedback.setButtonLoading(triggerBtn, true, 'Scanning...');
  bridge.scanPorts((portsJson) => {
    let ports = [];
    try { ports = JSON.parse(portsJson); } catch (e) { ports = []; }
    let entries = parsePortEntries(Array.isArray(ports) ? ports : []);
    if (entries.length === 0) entries = fallbackEntries();

    if (targetTx && !entries.some((entry) => entry.name === targetTx)) {
      entries.unshift({ name: targetTx, label: targetTx + ' (Launch Port)' });
    }
    if (targetRx && !entries.some((entry) => entry.name === targetRx)) {
      entries.unshift({ name: targetRx, label: targetRx + ' (Launch Port)' });
    }

    const names = entries.map((entry) => entry.name);

    const selectTx = document.getElementById('select-port-tx');
    const selectRx = document.getElementById('select-port-rx');
    const currentTx = selectTx ? selectTx.value : '';
    const currentRx = selectRx ? selectRx.value : '';

    let txValue;
    if (targetTx && names.includes(targetTx)) txValue = targetTx;
    else if (portSelectionInitialized && names.includes(currentTx)) txValue = currentTx;
    else txValue = names[0] || '';

    let rxValue;
    if (targetRx && names.includes(targetRx)) rxValue = targetRx;
    else if (portSelectionInitialized && names.includes(currentRx)) rxValue = currentRx;
    else rxValue = pickRxPort(names, txValue);

    fillPortSelect(selectTx, entries, txValue);
    fillPortSelect(selectRx, entries, rxValue);
    portSelectionInitialized = true;

    if (triggerBtn && window.UIFeedback) window.UIFeedback.setButtonLoading(triggerBtn, false);
    if (triggerBtn) {
      if (entries.length <= 1) toast('warning', 'Limited port list', 'Only one port available. It will be used for both TX and RX.');
      else toast('info', 'Port list refreshed', 'Found ' + entries.length + ' port(s).');
    }
  });
}

document.addEventListener('click', (e) => {
  const btn = e.target.closest('button');
  if (!btn) return;

  if (btn.closest('#modal-overlay, #toast-container, #settings-overlay')) return;

  if (btn.classList.contains('traffic-dot')) {
    const label = btn.getAttribute('aria-label');
    if (label === 'Close' && bridge) {
      bridge.closeWindow();
    } else if (label === 'Minimize' && bridge) {
      bridge.minimizeWindow();
    } else if (label === 'Zoom' && bridge) {
      bridge.toggleMaximizeWindow();
    }
    return;
  }

  const baudContainer = btn.closest('#baudrate-selector-container');
  if (baudContainer) {
    const baudVal = parseInt(btn.dataset.baud || btn.innerText.trim(), 10);
    if (!isNaN(baudVal)) {
      const connBtn = document.getElementById('btn-connect');
      const isConnected = connBtn && connBtn.dataset.state === 'connected';
      if (isConnected && bridge && typeof bridge.setOpticalBaud === 'function') {
        pendingBaud = baudVal;
        renderBaudrateSelector(currentBaudOptions, selectedBaud, pendingBaud);
        bridge.setOpticalBaud(baudVal, (resultJson) => {
          let result = {};
          try { result = JSON.parse(resultJson); } catch (e) { result = {}; }
          if (result && result.ok === false) {
            pendingBaud = null;
            renderBaudrateSelector(currentBaudOptions, selectedBaud, null);
            toast('error', 'Optical baudrate change rejected', result.error || 'Unsupported optical baudrate');
          } else {
            console.log('Requested optical baudrate:', baudVal);
          }
        });
      } else {
        selectedBaud = baudVal;
        pendingBaud = null;
        renderBaudrateSelector(currentBaudOptions, selectedBaud, null);
        console.log('Selected optical baudrate for next connect:', selectedBaud);
      }
    }
    return;
  }

  if (btn.innerText.includes('Connect')) {
    if (!bridge) {
      toast('warning', 'Backend not connected', 'Open this UI inside the SpectraLink Desktop app to control hardware.');
      return;
    }
    const txSelect = document.getElementById('select-port-tx');
    const rxSelect = document.getElementById('select-port-rx');
    const txPort = txSelect ? txSelect.value : 'MOCK_COM_LOOPBACK';
    const rxPort = (rxSelect && rxSelect.value) || txPort;
    const summary = 'TX: ' + txPort + ' • RX: ' + rxPort;
    pendingConnectBtn = btn;
    pendingConnectPort = summary;
    if (window.UIFeedback) {
      window.UIFeedback.setButtonLoading(btn, true, 'Connecting...');
      if (connectFailTimer) clearTimeout(connectFailTimer);
      connectFailTimer = setTimeout(() => {
        if (pendingConnectBtn) {
          const ports = pendingConnectPort;
          clearConnectLoading();
          setTone('ribbon-badge', 'error');
          if (window.UIBadge) window.UIBadge.set('hw-header-link-badge', { tone: 'error', label: 'ERROR' });
          window.UIFeedback.showModal({
            variant: 'error',
            title: 'Connection timeout',
            message: 'No response from the ports within 6s. Check cable or try Rescan. If Ping fails, swap TX and RX.',
            details: ports + ' @ ' + selectedBaud + ' bps',
          });
        }
      }, 6000);
    }
    clearPingLoading();
    setConnectingUi(txPort, rxPort);
    bridge.connectDualPorts(txPort, rxPort, selectedBaud);
  } else if (btn.innerText.includes('Disconnect')) {
    if (!bridge) return;
    clearPingLoading();
    bridge.disconnectPort();
    toast('info', 'Disconnecting...', 'Closing serial link.');
  } else if (btn.innerText.includes('Ping Test') || btn.innerText.includes('Ping Link Test') || btn.innerText.includes('Calibrate Optics') || btn.innerText.includes('Ping')) {
    if (!bridge) {
      toast('warning', 'Backend not connected', 'Ping needs the Desktop app link. Open via SpectraLink to test RTT.');
      return;
    }
    if (window.UIFeedback) {
      window.UIFeedback.setButtonLoading(btn, true, 'Pinging...');
      pendingPingBtns.push(btn);
    }
    if (pingFailTimer) clearTimeout(pingFailTimer);
    pingFailTimer = setTimeout(() => {
      if (pendingPingBtns.length) {
        clearPingLoading();
        toast('error', 'Ping timeout', 'No echo received within 1.5s. Check the link or swap TX and RX.');
      }
    }, 1500);
    bridge.sendPing();
  } else if (btn.innerText.includes('Rescan') || btn.id === 'btn-top-refresh') {
    if (!bridge) {
      toast('warning', 'Backend not connected', 'Port scan needs the Desktop app. Open via SpectraLink.');
      return;
    }
    clearPingLoading();
    refreshPortList(btn);
  }
});

document.addEventListener('DOMContentLoaded', () => {
  loadBaudrateOptions(DEFAULT_BAUDRATES, selectedBaud);
  if (!bridge) {
    fillPortSelect(document.getElementById('select-port-tx'), fallbackEntries(), null);
    fillPortSelect(document.getElementById('select-port-rx'), fallbackEntries(), null);
  }
});
