// QWebChannel Client Wrapper
let bridge = null;
let selectedBaud = 115200;

// QWebChannel is only present inside the Qt WebEngine view. Guard `qt` so this
// script never crashes (and click handlers below always register) when the page
// is opened in a plain browser or before the channel transport exists.
if (typeof QWebChannel !== 'undefined' && typeof qt !== 'undefined' && qt.webChannelTransport) {
  try {
    new QWebChannel(qt.webChannelTransport, (channel) => {
      bridge = channel.objects.bridge;
      window.bridge = bridge;
      console.log('QWebChannel Bridge Connected Successfully!');
      initBridgeListeners();
      refreshPortList();
    });
  } catch (err) {
    console.warn('QWebChannel setup failed, running without backend bridge:', err);
  }
} else {
  console.warn('No Qt WebChannel transport — running in UI-only mode (buttons show guidance toasts).');
}

// Pending UI states resolved by bridge signals (fail-safe timeouts below)
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

function initBridgeListeners() {
  if (!bridge) return;

  bridge.portConnected.connect((port, baud) => {
    clearConnectLoading();
    const ribbonText = document.getElementById('ribbon-text');
    if (ribbonText) ribbonText.innerText = 'STM32 Optical Bridge • ' + port + ' (' + baud + ' bps)';
    const ribbonDot = document.getElementById('ribbon-dot');
    if (ribbonDot) ribbonDot.className = 'w-2 h-2 rounded-full bg-secondary status-pulse';

    const footerHealthDot = document.getElementById('footer-health-dot');
    if (footerHealthDot) footerHealthDot.className = 'w-2 h-2 rounded-full bg-secondary status-pulse';
    const footerHealth = document.getElementById('footer-health-text');
    if (footerHealth) {
      footerHealth.innerText = 'ONLINE';
      footerHealth.className = 'font-mono-caption text-mono-caption font-semibold text-secondary';
    }
    const footerPortLabel = document.getElementById('footer-port-label');
    if (footerPortLabel) footerPortLabel.innerText = port;

    // Synchronize Hardware View Badges
    const headerLinkBadge = document.getElementById('hw-header-link-badge');
    if (headerLinkBadge) {
      headerLinkBadge.className = 'bg-secondary/10 text-secondary border border-secondary/20 px-2 py-0.5 rounded-full font-mono-caption text-mono-caption font-semibold flex items-center gap-1';
      headerLinkBadge.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-secondary"></span> LINK LOCKED';
    }
    const portReadyBadge = document.getElementById('hw-port-ready-badge');
    if (portReadyBadge) {
      portReadyBadge.className = 'flex items-center gap-1 bg-secondary/10 text-secondary border border-secondary/20 px-2 py-0.5 rounded-full font-label-sm text-label-sm';
      portReadyBadge.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-secondary"></span> Port Active';
    }
    const linkReadyBadge = document.getElementById('hw-link-ready-badge');
    if (linkReadyBadge) {
      linkReadyBadge.className = 'flex items-center gap-1 bg-secondary/10 text-secondary border border-secondary/20 px-2 py-0.5 rounded-full font-label-sm text-label-sm';
      linkReadyBadge.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-secondary"></span> Link Synced';
    }

    const connBtn = document.querySelector('#view-hardware button.bg-primary-container');
    if (connBtn) {
      connBtn.innerText = 'Disconnect';
      connBtn.className = 'flex-1 h-[32px] bg-error hover:bg-on-error-container text-white font-title-sm text-title-sm font-semibold rounded-lg shadow-sm flex items-center justify-center gap-1.5 cursor-pointer';
    }

    if (window.UIFeedback) {
      window.UIFeedback.showModal({
        variant: 'success',
        title: 'Link connected',
        message: 'STM32 optical bridge is ready for Ping / Chat / File transfer.',
        details: port + ' @ ' + baud + ' bps • 8-N-1',
      });
    }
  });

  bridge.errorOccurred.connect((message) => {
    const wasConnecting = !!pendingConnectBtn;
    const port = pendingConnectPort;
    clearConnectLoading();
    clearPingLoading();
    console.error('Bridge error:', message);
    if (wasConnecting && window.UIFeedback) {
      window.UIFeedback.showModal({
        variant: 'error',
        title: 'Connection failed',
        message: 'Could not open the selected port. Check cable, driver, or close other serial tools.',
        details: (port ? port + ' • ' : '') + message,
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
    const ribbonText = document.getElementById('ribbon-text');
    if (ribbonText) ribbonText.innerText = 'STM32 Optical Bridge • Disconnected';
    const ribbonDot = document.getElementById('ribbon-dot');
    if (ribbonDot) ribbonDot.className = 'w-2 h-2 rounded-full bg-outline';

    const footerHealthDot = document.getElementById('footer-health-dot');
    if (footerHealthDot) footerHealthDot.className = 'w-2 h-2 rounded-full bg-outline';
    const footerHealth = document.getElementById('footer-health-text');
    if (footerHealth) {
      footerHealth.innerText = 'OFFLINE';
      footerHealth.className = 'font-mono-caption text-mono-caption font-semibold text-outline';
    }
    const footerPortLabel = document.getElementById('footer-port-label');
    if (footerPortLabel) footerPortLabel.innerText = 'Disconnected';

    // Reset Hardware View Badges
    const headerLinkBadge = document.getElementById('hw-header-link-badge');
    if (headerLinkBadge) {
      headerLinkBadge.className = 'bg-outline/10 text-outline border border-outline/20 px-2 py-0.5 rounded-full font-mono-caption text-mono-caption font-semibold flex items-center gap-1';
      headerLinkBadge.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-outline"></span> DISCONNECTED';
    }
    const portReadyBadge = document.getElementById('hw-port-ready-badge');
    if (portReadyBadge) {
      portReadyBadge.className = 'flex items-center gap-1 bg-outline/10 text-outline border border-outline/20 px-2 py-0.5 rounded-full font-label-sm text-label-sm';
      portReadyBadge.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-outline"></span> Port Standby';
    }
    const linkReadyBadge = document.getElementById('hw-link-ready-badge');
    if (linkReadyBadge) {
      linkReadyBadge.className = 'flex items-center gap-1 bg-outline/10 text-outline border border-outline/20 px-2 py-0.5 rounded-full font-label-sm text-label-sm';
      linkReadyBadge.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-outline"></span> Link Standby';
    }

    const connBtn = document.querySelector('#view-hardware button.bg-error');
    if (connBtn) {
      connBtn.innerText = 'Connect';
      connBtn.className = 'flex-1 h-[32px] bg-primary-container hover:bg-primary text-white font-title-sm text-title-sm font-semibold rounded-lg shadow-sm flex items-center justify-center gap-1.5 cursor-pointer';
    }

    // User-initiated disconnect is silent-ish (toast); unexpected drop while
    // connecting is already covered by errorOccurred modal above.
    if (!wasConnecting) toast('info', 'Disconnected', 'Serial link closed.');
  });

  bridge.pingResult.connect((rtt, seq) => {
    clearPingLoading();
    const rttElement = document.querySelector('#hw-rtt-value');
    if (rttElement) rttElement.innerText = rtt.toFixed(1) + ' ms';
    toast('success', 'Ping echo received', 'Seq #' + seq + ' • RTT ' + rtt.toFixed(1) + ' ms');
    console.log('Ping Echo Received: seq #' + seq + ' | RTT: ' + rtt.toFixed(1) + ' ms');
  });

  // Silent automatic health checks: refresh RTT display, never toast (no spam).
  bridge.healthResult.connect((rtt) => {
    const rttElement = document.querySelector('#hw-rtt-value');
    if (rttElement) rttElement.innerText = rtt.toFixed(1) + ' ms';
  });

  // TX & RX Live Blinker Animation
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
}

function refreshPortList(triggerBtn) {
  if (!bridge) return;
  if (triggerBtn && window.UIFeedback) window.UIFeedback.setButtonLoading(triggerBtn, true, 'Scanning...');
  bridge.scanPorts((portsJson) => {
    let ports = [];
    try { ports = JSON.parse(portsJson); } catch (e) { ports = []; }
    const select = document.querySelector('#view-hardware select');
    if (select) {
      select.innerHTML = '';
      ports.forEach(p => {
        const opt = document.createElement('option');
        opt.value = p;
        opt.innerText = p.includes('MOCK') ? p + ' (Virtual Loopback)' : p + ' - STM32F407 CDC';
        select.appendChild(opt);
      });
    }
    if (triggerBtn && window.UIFeedback) window.UIFeedback.setButtonLoading(triggerBtn, false);
    if (triggerBtn) {
      if (ports.length === 0) toast('warning', 'No ports found', 'Check USB cable, STM32 driver, or mock backend.');
      else toast('info', 'Port list refreshed', 'Found ' + ports.length + ' port(s).');
    }
  });
}

// Global button actions delegate
document.addEventListener('click', (e) => {
  const btn = e.target.closest('button');
  if (!btn) return;

  // Global feedback layer (modal / toasts / settings) owns its buttons — never hijack them.
  if (btn.closest('#modal-overlay, #toast-container, #settings-overlay')) return;

  // Window Controls (Traffic Lights)
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

  // Baudrate Selector Buttons
  const baudContainer = btn.closest('#baudrate-selector');
  if (baudContainer) {
    const baudVal = parseInt(btn.innerText.trim(), 10);
    if (!isNaN(baudVal)) {
      selectedBaud = baudVal;
      baudContainer.querySelectorAll('button').forEach(b => {
        b.className = 'py-1 text-center font-mono-data text-mono-data rounded-md text-on-surface-variant hover:text-on-surface hover:bg-white/50 transition-all cursor-pointer';
      });
      btn.className = 'py-1 text-center font-mono-data text-mono-data rounded-md bg-white text-primary font-bold shadow-[0_1px_3px_rgba(0,0,0,0.08)] cursor-pointer';
      console.log('Selected Baudrate:', selectedBaud);
    }
    return;
  }

  // Every hardware button below gives immediate visual feedback, even when the
  // Python backend bridge is absent (plain-browser preview): guidance toast
  // instead of dead silence.
  if (btn.innerText.includes('Connect')) {
    if (!bridge) {
      toast('warning', 'Backend not connected', 'Open this UI inside the SpectraLink Desktop app to control hardware.');
      return;
    }
    const select = document.querySelector('#view-hardware select');
    const port = select ? select.value : 'MOCK_COM_LOOPBACK';
    pendingConnectBtn = btn;
    pendingConnectPort = port;
    if (window.UIFeedback) {
      window.UIFeedback.setButtonLoading(btn, true, 'Connecting...');
      if (connectFailTimer) clearTimeout(connectFailTimer);
      connectFailTimer = setTimeout(() => {
        if (pendingConnectBtn) {
          const p = pendingConnectPort;
          clearConnectLoading();
          window.UIFeedback.showModal({
            variant: 'error',
            title: 'Connection timeout',
            message: 'No response from the port within 6s. Check cable or try Rescan.',
            details: p + ' @ ' + selectedBaud + ' bps',
          });
        }
      }, 6000);
    }
    bridge.connectPort(port, selectedBaud);
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
          toast('error', 'Ping timeout', 'No PONG within 1.5s — check link / connection.');
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
