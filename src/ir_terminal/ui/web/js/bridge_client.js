let bridge = null;
let selectedBaud = 115200;
let portSelectionInitialized = false;

const DEFAULT_BAUDRATES = [9600, 38400, 57600, 115200];

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

function toast(type, title, message) {
  if (window.UIFeedback) window.UIFeedback.showToast(type, title, message);
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

function baudButtonClass(isActive) {
  const base = 'flex-1 min-w-[64px] py-1 text-center font-mono-data text-mono-data rounded-md cursor-pointer';
  if (isActive) return base + ' bg-white text-primary font-semibold shadow-[0_1px_3px_rgba(0,0,0,0.08)]';
  return base + ' text-on-surface-variant hover:text-on-surface hover:bg-white/50 transition-colors';
}

function renderBaudrateSelector(baudrates, activeBaud) {
  const container = document.getElementById('baudrate-selector-container');
  if (!container) return;
  const parsed = (Array.isArray(baudrates) ? baudrates : [])
    .map((value) => parseInt(value, 10))
    .filter((value) => Number.isFinite(value) && value > 0);
  const options = parsed.length ? Array.from(new Set(parsed)) : DEFAULT_BAUDRATES.slice();
  const requested = parseInt(activeBaud, 10);
  const active = options.includes(requested) ? requested : (options.includes(115200) ? 115200 : options[0]);
  selectedBaud = active;
  container.innerHTML = '';
  options.forEach((baud) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.dataset.baud = String(baud);
    btn.innerText = String(baud);
    btn.className = baudButtonClass(baud === active);
    container.appendChild(btn);
  });
  const source = document.getElementById('baudrate-source-label');
  if (source) source.innerText = options.length + ' RATES • DIP SWITCH';
}

function loadBaudrateOptions(fallbackList, activeBaud) {
  if (!bridge || typeof bridge.getAvailableBaudrates !== 'function') {
    renderBaudrateSelector(fallbackList, activeBaud);
    return;
  }
  bridge.getAvailableBaudrates((baudratesJson) => {
    let values = [];
    try { values = JSON.parse(baudratesJson); } catch (e) { values = []; }
    renderBaudrateSelector(Array.isArray(values) && values.length ? values : fallbackList, activeBaud);
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
  const alternatives = names.filter((name) => name !== txPort);
  return alternatives[0] || txPort;
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

function setConnectedUi(txPort, rxPort, baud) {
  const summary = 'TX: ' + txPort + ' • RX: ' + rxPort;
  const ribbonText = document.getElementById('ribbon-text');
  if (ribbonText) ribbonText.innerText = 'STM32 Optical Bridge • ' + summary + ' (' + baud + ' bps)';
  const ribbonDot = document.getElementById('ribbon-dot');
  if (ribbonDot) ribbonDot.className = 'w-2 h-2 rounded-full bg-secondary';

  const footerHealthDot = document.getElementById('footer-health-dot');
  if (footerHealthDot) footerHealthDot.className = 'w-2 h-2 rounded-full bg-secondary';
  const footerHealth = document.getElementById('footer-health-text');
  if (footerHealth) {
    footerHealth.innerText = 'ONLINE';
    footerHealth.className = 'font-mono-caption text-mono-caption font-medium text-secondary';
  }
  const footerPortLabel = document.getElementById('footer-port-label');
  if (footerPortLabel) footerPortLabel.innerText = summary;
  const footerBaudLabel = document.getElementById('footer-baud-label');
  if (footerBaudLabel) footerBaudLabel.innerText = baud + ' bps';

  const headerLinkBadge = document.getElementById('hw-header-link-badge');
  if (headerLinkBadge) {
    headerLinkBadge.className = 'flex items-center gap-1.5 font-mono-caption text-mono-caption font-medium text-secondary';
    headerLinkBadge.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-secondary"></span> LINK CONNECTED';
  }
  const rttState = document.getElementById('hw-rtt-state');
  if (rttState) {
    rttState.innerText = 'READY';
    rttState.className = 'text-secondary font-semibold';
  }
  const chatLinkState = document.getElementById('chat-link-state');
  if (chatLinkState) {
    chatLinkState.innerText = 'LINK: CONNECTED';
    chatLinkState.className = 'text-secondary font-medium';
  }

  const connBtn = document.getElementById('btn-connect');
  if (connBtn) {
    connBtn.innerText = 'Disconnect';
    connBtn.className = 'flex-1 h-[32px] bg-error hover:bg-on-error-container text-white font-title-sm text-title-sm font-semibold rounded-lg shadow-sm flex items-center justify-center gap-1.5 active:scale-[0.98] transition-all cursor-pointer';
  }
}

function setDisconnectedUi() {
  const ribbonText = document.getElementById('ribbon-text');
  if (ribbonText) ribbonText.innerText = 'STM32 Optical Bridge • Disconnected';
  const ribbonDot = document.getElementById('ribbon-dot');
  if (ribbonDot) ribbonDot.className = 'w-2 h-2 rounded-full bg-outline';

  const footerHealthDot = document.getElementById('footer-health-dot');
  if (footerHealthDot) footerHealthDot.className = 'w-2 h-2 rounded-full bg-outline';
  const footerHealth = document.getElementById('footer-health-text');
  if (footerHealth) {
    footerHealth.innerText = 'OFFLINE';
    footerHealth.className = 'font-mono-caption text-mono-caption font-medium text-outline';
  }
  const footerPortLabel = document.getElementById('footer-port-label');
  if (footerPortLabel) footerPortLabel.innerText = 'Disconnected';
  const footerBaudLabel = document.getElementById('footer-baud-label');
  if (footerBaudLabel) footerBaudLabel.innerText = '--';

  const headerLinkBadge = document.getElementById('hw-header-link-badge');
  if (headerLinkBadge) {
    headerLinkBadge.className = 'flex items-center gap-1.5 font-mono-caption text-mono-caption font-medium text-outline';
    headerLinkBadge.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-outline"></span> DISCONNECTED';
  }
  const rttState = document.getElementById('hw-rtt-state');
  if (rttState) {
    rttState.innerText = 'IDLE';
    rttState.className = 'text-outline font-semibold';
  }
  const chatLinkState = document.getElementById('chat-link-state');
  if (chatLinkState) {
    chatLinkState.innerText = 'LINK: STANDBY';
    chatLinkState.className = 'text-outline font-medium';
  }

  const connBtn = document.getElementById('btn-connect');
  if (connBtn) {
    connBtn.innerText = 'Connect';
    connBtn.className = 'flex-1 h-[32px] bg-primary-container hover:bg-primary text-on-primary font-title-sm text-title-sm font-semibold rounded-lg shadow-sm flex items-center justify-center gap-1.5 active:scale-[0.98] transition-all cursor-pointer';
  }
}

function initBridgeListeners() {
  if (!bridge) return;

  bridge.portConnected.connect((txPort, rxPort, baud) => {
    clearConnectLoading();
    setConnectedUi(txPort, rxPort, baud);
    if (window.UIFeedback) {
      window.UIFeedback.showModal({
        variant: 'success',
        title: 'Link connected',
        message: 'STM32 optical bridge is ready for Ping / Chat / File transfer.',
        details: 'TX: ' + txPort + ' • RX: ' + rxPort + ' @ ' + baud + ' bps • 8-N-1',
      });
    }
  });

  bridge.errorOccurred.connect((message) => {
    const wasConnecting = !!pendingConnectBtn;
    const ports = pendingConnectPort;
    clearConnectLoading();
    clearPingLoading();
    console.error('Bridge error:', message);
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
    setDisconnectedUi();
    if (!wasConnecting) toast('info', 'Disconnected', 'Serial link closed.');
  });

  bridge.pingResult.connect((rtt, seq) => {
    clearPingLoading();
    const rttElement = document.getElementById('hw-rtt-value');
    if (rttElement) rttElement.innerText = rtt.toFixed(1) + ' ms';
    const rttState = document.getElementById('hw-rtt-state');
    if (rttState) {
      rttState.innerText = 'MEASURED';
      rttState.className = 'text-secondary font-semibold';
    }
    toast('success', 'Ping echo received', 'Seq #' + seq + ' • RTT ' + rtt.toFixed(1) + ' ms');
    console.log('Ping Echo Received: seq #' + seq + ' | RTT: ' + rtt.toFixed(1) + ' ms');
  });

  bridge.healthResult.connect((rtt) => {
    const rttElement = document.getElementById('hw-rtt-value');
    if (rttElement) rttElement.innerText = rtt.toFixed(1) + ' ms';
  });

  let txTimeout = null;
  let rxTimeout = null;
  const txDot = document.getElementById('hw-tx-dot');
  const rxDot = document.getElementById('hw-rx-dot');

  bridge.rawTxData.connect(() => {
    if (txDot) {
      txDot.className = 'w-2.5 h-2.5 rounded-full bg-primary shadow-sm ring-2 ring-primary/30';
      if (txTimeout) clearTimeout(txTimeout);
      txTimeout = setTimeout(() => {
        txDot.className = 'w-2.5 h-2.5 rounded-full bg-primary/40';
      }, 120);
    }
  });

  bridge.rawRxData.connect(() => {
    if (rxDot) {
      rxDot.className = 'w-2.5 h-2.5 rounded-full bg-secondary shadow-sm ring-2 ring-secondary/30';
      if (rxTimeout) clearTimeout(rxTimeout);
      rxTimeout = setTimeout(() => {
        rxDot.className = 'w-2.5 h-2.5 rounded-full bg-secondary/40';
      }, 120);
    }
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
      ribbon.innerText = cfg.display_name + role + ' • Waiting for link';
    }
    const baudList = Array.isArray(cfg.supported_baudrates) ? cfg.supported_baudrates : DEFAULT_BAUDRATES;
    loadBaudrateOptions(baudList, cfg.default_baudrate || cfg.baud || selectedBaud);
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
      selectedBaud = baudVal;
      baudContainer.querySelectorAll('button').forEach((b) => {
        b.className = baudButtonClass(parseInt(b.dataset.baud, 10) === selectedBaud);
      });
      console.log('Selected Baudrate:', selectedBaud);
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
          window.UIFeedback.showModal({
            variant: 'error',
            title: 'Connection timeout',
            message: 'No response from the ports within 6s. Check cable or try Rescan. If Ping fails, swap TX and RX.',
            details: ports + ' @ ' + selectedBaud + ' bps',
          });
        }
      }, 6000);
    }
    bridge.connectDualPorts(txPort, rxPort, selectedBaud);
  } else if (btn.innerText.includes('Disconnect')) {
    if (!bridge) return;
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
      if (pingFailTimer) clearTimeout(pingFailTimer);
      pingFailTimer = setTimeout(() => {
        if (pendingPingBtns.length) {
          clearPingLoading();
          toast('error', 'Ping timeout', 'No PONG within 1.5s — check link, or swap TX / RX ports.');
        }
      }, 1500);
    }
    bridge.sendPing();
  } else if (btn.innerText.includes('Rescan') || btn.id === 'btn-top-refresh') {
    if (!bridge) {
      toast('warning', 'Backend not connected', 'Port scan needs the Desktop app. Open via SpectraLink.');
      return;
    }
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
