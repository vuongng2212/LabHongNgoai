// QWebChannel Client Wrapper
let bridge = null;
let selectedBaud = 115200;

if (typeof QWebChannel !== 'undefined') {
  new QWebChannel(qt.webChannelTransport, (channel) => {
    bridge = channel.objects.bridge;
    console.log('QWebChannel Bridge Connected Successfully!');
    initBridgeListeners();
    refreshPortList();
  });
}

function initBridgeListeners() {
  if (!bridge) return;

  bridge.portConnected.connect((port, baud) => {
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
  });

  bridge.portDisconnected.connect(() => {
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
  });

  bridge.pingResult.connect((rtt, seq) => {
    const rttElement = document.querySelector('#hw-rtt-value');
    if (rttElement) rttElement.innerText = rtt.toFixed(1) + ' ms';
    console.log('Ping Echo Received: seq #' + seq + ' | RTT: ' + rtt.toFixed(1) + ' ms');
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

function refreshPortList() {
  if (!bridge) return;
  bridge.scanPorts((portsJson) => {
    const ports = JSON.parse(portsJson);
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
  });
}

// Global button actions delegate
document.addEventListener('click', (e) => {
  const btn = e.target.closest('button');
  if (!btn) return;

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

  if (btn.innerText.includes('Connect') && bridge) {
    const select = document.querySelector('#view-hardware select');
    const port = select ? select.value : 'MOCK_COM_LOOPBACK';
    bridge.connectPort(port, selectedBaud);
  } else if (btn.innerText.includes('Disconnect') && bridge) {
    bridge.disconnectPort();
  } else if ((btn.innerText.includes('Ping Test') || btn.innerText.includes('Calibrate Optics')) && bridge) {
    bridge.sendPing();
  } else if ((btn.innerText.includes('Rescan') || btn.id === 'btn-top-refresh') && bridge) {
    refreshPortList();
  }
});
