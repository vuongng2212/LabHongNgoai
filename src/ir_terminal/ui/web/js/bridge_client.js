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

    const footerHealth = document.getElementById('footer-health-text');
    if (footerHealth) footerHealth.innerText = 'HEALTH 100% ONLINE';

    const connBtn = document.querySelector('#view-hardware button.bg-primary-container');
    if (connBtn) {
      connBtn.innerText = 'Disconnect';
      connBtn.className = 'flex-1 h-[32px] bg-error hover:bg-on-error-container text-white font-title-sm text-title-sm font-semibold rounded-lg shadow-sm flex items-center justify-center gap-1.5';
    }
  });

  bridge.portDisconnected.connect(() => {
    const ribbonText = document.getElementById('ribbon-text');
    if (ribbonText) ribbonText.innerText = 'STM32 Optical Bridge • Disconnected';
    const ribbonDot = document.getElementById('ribbon-dot');
    if (ribbonDot) ribbonDot.className = 'w-2 h-2 rounded-full bg-outline';

    const footerHealth = document.getElementById('footer-health-text');
    if (footerHealth) footerHealth.innerText = 'OFFLINE';

    const connBtn = document.querySelector('#view-hardware button.bg-error');
    if (connBtn) {
      connBtn.innerText = 'Connect';
      connBtn.className = 'flex-1 h-[32px] bg-primary-container hover:bg-primary text-white font-title-sm text-title-sm font-semibold rounded-lg shadow-sm flex items-center justify-center gap-1.5';
    }
  });

  bridge.pingResult.connect((rtt, seq) => {
    const rttElement = document.querySelector('#view-hardware .font-headline-md');
    if (rttElement) rttElement.innerText = rtt.toFixed(1) + ' ms';
    console.log('Ping Echo Received: seq #' + seq + ' | RTT: ' + rtt.toFixed(1) + ' ms');
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
  if (btn.innerText.includes('Connect') && bridge) {
    const select = document.querySelector('#view-hardware select');
    const port = select ? select.value : 'MOCK_COM_LOOPBACK';
    bridge.connectPort(port, selectedBaud);
  } else if (btn.innerText.includes('Disconnect') && bridge) {
    bridge.disconnectPort();
  } else if (btn.innerText.includes('Ping Test') && bridge) {
    bridge.sendPing();
  } else if (btn.innerText.includes('Rescan') && bridge) {
    refreshPortList();
  }
});
