"""Builds the unified single-page application HTML for QWebEngineView."""

import os
import re

REPLACEMENTS = [
    ("â¢", "•"),
    ("â»â¹", "⁻⁹"),
    ("â»Â¹â°", "⁻¹⁰"),
    ("10â»â¹", "10⁻⁹"),
    ("10â»Â¹â°", "10⁻¹⁰"),
    ("Î»:", "λ:"),
    ("Î»", "λ"),
    ("Â°C", "°C"),
    ("Â°", "°"),
    ("Â±", "±"),
    (" Ã ", " × "),
    ("Ã—", "×"),
    ("â—", "—"),
    ("â", "—"),
    ("Ã", "×"),
]


def sanitize_text(text: str) -> str:
    res = text
    for old, new in REPLACEMENTS:
        res = res.replace(old, new)
    return res


def extract_main_content(filepath: str) -> str:
    with open(filepath, encoding="utf-8") as f:
        content = f.read()
    match = re.search(r"<main[^>]*>(.*?)</main>", content, re.DOTALL)
    if not match:
        return ""
    return sanitize_text(match.group(1))


def build_spa() -> None:
    p1 = extract_main_content("ui_templates/apple_style/1_1_hardware_setup.html")
    p2 = extract_main_content("ui_templates/apple_style/2_2_file_transfer.html")
    p3 = extract_main_content("ui_templates/apple_style/3_3_realtime_chat.html")
    p4 = extract_main_content("ui_templates/apple_style/4_4_lab_telemetry.html")

    html_template = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta content="width=device-width, initial-scale=1.0" name="viewport"/>
  <title>SpectraLink IR-OWC Workstation</title>
  <!-- Offline Local Fonts & Icons (100% self-contained) -->
  <link rel="stylesheet" href="assets/css/local-fonts.css"/>
  <script src="assets/js/tailwindcss.js"></script>
  <script id="tailwind-config">
    tailwind.config = {{
      darkMode: "class",
      theme: {{
        extend: {{
          colors: {{
            "surface": "#f8f9ff",
            "surface-dim": "#cbdbf5",
            "surface-bright": "#f8f9ff",
            "surface-container-lowest": "#ffffff",
            "surface-container-low": "#eff4ff",
            "surface-container": "#e5eeff",
            "surface-container-high": "#dce9ff",
            "surface-container-highest": "#d3e4fe",
            "on-surface": "#0b1c30",
            "on-surface-variant": "#434655",
            "inverse-surface": "#213145",
            "inverse-on-surface": "#eaf1ff",
            "outline": "#737686",
            "outline-variant": "#c3c6d7",
            "surface-tint": "#0053db",
            "primary": "#004ac6",
            "on-primary": "#ffffff",
            "primary-container": "#2563eb",
            "on-primary-container": "#eeefff",
            "inverse-primary": "#b4c5ff",
            "secondary": "#006c49",
            "on-secondary": "#ffffff",
            "secondary-container": "#6cf8bb",
            "on-secondary-container": "#00714d",
            "tertiary": "#784b00",
            "on-tertiary": "#ffffff",
            "tertiary-container": "#996100",
            "on-tertiary-container": "#ffeedd",
            "error": "#ba1a1a",
            "on-error": "#ffffff",
            "error-container": "#ffdad6",
            "on-error-container": "#93000a",
            "primary-fixed": "#dbe1ff",
            "primary-fixed-dim": "#b4c5ff",
            "on-primary-fixed": "#00174b",
            "on-primary-fixed-variant": "#003ea8",
            "secondary-fixed": "#6ffbbe",
            "secondary-fixed-dim": "#4edea3",
            "on-secondary-fixed": "#002113",
            "on-secondary-fixed-variant": "#005236",
            "background": "#f8f9ff",
            "on-background": "#0b1c30",
            "surface-variant": "#d3e4fe"
          }},
          spacing: {{
            "space-xs": "0.25rem",
            "margin": "1.25rem",
            "space-lg": "1.25rem",
            "space-xl": "1.75rem",
            "space-md": "0.75rem",
            "space-sm": "0.5rem",
            "gutter": "1rem"
          }},
          fontFamily: {{
            "headline-md": ["Plus Jakarta Sans"],
            "display-lg": ["Plus Jakarta Sans"],
            "body-md": ["Inter"],
            "body-lg": ["Inter"],
            "mono-caption": ["Inter"],
            "headline-lg": ["Plus Jakarta Sans"],
            "mono-data": ["Inter"],
            "label-sm": ["Inter"],
            "label-md": ["Inter"],
            "title-sm": ["Inter"]
          }}
        }}
      }}
    }}
  </script>
  <style>
    .material-symbols-outlined {{
      font-variation-settings: 'FILL' 0, 'wght' 400, 'GRAD' 0, 'opsz' 20;
      font-size: 18px;
      line-height: 1;
      display: inline-block;
      vertical-align: middle;
    }}
    .traffic-dot:hover span {{
      opacity: 0.6;
    }}
    @keyframes pulse-subtle {{
      0%, 100% {{ opacity: 1; transform: scale(1); }}
      50% {{ opacity: 0.5; transform: scale(0.92); }}
    }}
    .status-pulse {{
      animation: pulse-subtle 2s infinite ease-in-out;
    }}
    .view-panel {{
      display: none;
    }}
    .view-panel.active {{
      display: flex;
    }}
  </style>
  <script src="qwebchannel.js"></script>
</head>
<body class="bg-[#F5F5F7] text-on-surface antialiased min-h-screen flex flex-col font-body-md text-body-md select-none overflow-hidden">
  <!-- Window Container with Native macOS Frame -->
  <div class="w-full h-screen bg-[#F5F5F7] flex flex-col overflow-hidden">
    <!-- Window Title Bar Chrome -->
    <header class="h-[38px] bg-surface-container-lowest border-b border-black/[0.08] px-space-md flex items-center justify-between flex-shrink-0 select-none">
      <!-- Traffic Light Controls & Session Indicator -->
      <div class="flex items-center gap-space-md">
        <div class="flex items-center gap-2 pr-2">
          <button aria-label="Close window" class="traffic-dot w-3 h-3 rounded-full bg-[#FF5F56] border border-[#E0443E] flex items-center justify-center group focus:outline-none">
            <span class="text-[8px] text-black font-bold opacity-0 transition-opacity">×</span>
          </button>
          <button aria-label="Minimize window" class="traffic-dot w-3 h-3 rounded-full bg-[#FFBD2E] border border-[#DEA123] flex items-center justify-center group focus:outline-none">
            <span class="text-[8px] text-black font-bold opacity-0 transition-opacity">-</span>
          </button>
          <button aria-label="Zoom window" class="traffic-dot w-3 h-3 rounded-full bg-[#27C93F] border border-[#1AAB29] flex items-center justify-center group focus:outline-none">
            <span class="text-[7px] text-black font-bold opacity-0 transition-opacity">+</span>
          </button>
        </div>
        <div class="h-4 w-px bg-outline-variant/40"></div>
        <!-- Connection Ribbon Badge -->
        <div class="flex items-center gap-space-xs bg-surface-container-low px-2 py-0.5 rounded-md border border-outline-variant/50">
          <span id="ribbon-dot" class="w-2 h-2 rounded-full bg-secondary status-pulse"></span>
          <span id="ribbon-text" class="font-mono-data text-mono-data text-on-surface font-medium">STM32 Optical Bridge • COM4 (Ready)</span>
        </div>
      </div>
      <!-- Center Title -->
      <div class="flex items-center gap-2">
        <span class="material-symbols-outlined text-primary text-[15px]" data-icon="terminal">terminal</span>
        <span id="window-center-title" class="font-title-sm text-title-sm text-on-surface font-semibold">SpectraLink IR-OWC — Hardware & Optical Link Setup</span>
      </div>
      <!-- Right Window Utilities -->
      <div class="flex items-center gap-space-xs">
        <div class="flex items-center bg-surface-container-high/40 text-on-surface-variant px-2 py-0.5 rounded text-mono-caption font-mono-caption font-bold">
          SESSION #4029-A
        </div>
        <button id="btn-top-refresh" class="p-1 rounded hover:bg-surface-container-high text-on-surface-variant transition-colors" title="Refresh Telemetry">
          <span class="material-symbols-outlined text-[16px]" data-icon="refresh">refresh</span>
        </button>
        <button class="p-1 rounded hover:bg-surface-container-high text-on-surface-variant transition-colors" title="Device Settings">
          <span class="material-symbols-outlined text-[16px]" data-icon="settings">settings</span>
        </button>
      </div>
    </header>

    <!-- App Body: Left Navigation + Primary Content Canvas -->
    <div class="flex flex-1 overflow-hidden">
      <!-- Left Sidebar Navigation -->
      <aside class="w-64 bg-surface-container-low/70 border-r border-black/[0.08] flex flex-col justify-between p-space-md select-none flex-shrink-0">
        <div class="space-y-space-md">
          <!-- Brand Block -->
          <div class="flex items-center gap-space-sm px-1 py-1">
            <div class="w-8 h-8 rounded-lg bg-primary-container text-on-secondary flex items-center justify-center shadow-sm">
              <span class="material-symbols-outlined text-[20px]" data-icon="radar">radar</span>
            </div>
            <div class="leading-tight">
              <div class="font-title-sm text-title-sm font-bold text-on-surface">SpectraLink IR-OWC</div>
              <div class="font-mono-caption text-mono-caption text-on-surface-variant">Workstation v2.4.1</div>
            </div>
          </div>
          <!-- Section Navigation Group -->
          <div class="space-y-1">
            <div class="px-space-xs py-1 text-mono-caption font-mono-caption text-outline font-semibold tracking-wider">
              OPTICAL SUBSYSTEMS
            </div>
            <nav class="space-y-1" id="nav-menu">
              <!-- Active Item: Hardware & Link -->
              <a data-target="view-hardware" class="nav-item flex items-center gap-space-sm px-space-md py-space-xs rounded-lg bg-primary-container text-on-primary font-medium shadow-sm transition-all cursor-pointer">
                <span class="material-symbols-outlined text-[18px]" data-icon="tune">tune</span>
                <span class="font-label-md text-label-md">Hardware & Link</span>
              </a>
              <a data-target="view-file" class="nav-item flex items-center gap-space-sm px-space-md py-space-xs rounded-lg text-on-surface-variant hover:text-on-surface hover:bg-surface-container-highest/60 transition-colors cursor-pointer">
                <span class="material-symbols-outlined text-[18px]" data-icon="swap_horiz">swap_horiz</span>
                <span class="font-label-md text-label-md">File Transfer</span>
              </a>
              <a data-target="view-chat" class="nav-item flex items-center gap-space-sm px-space-md py-space-xs rounded-lg text-on-surface-variant hover:text-on-surface hover:bg-surface-container-highest/60 transition-colors cursor-pointer">
                <span class="material-symbols-outlined text-[18px]" data-icon="forum">forum</span>
                <span class="font-label-md text-label-md">Realtime Chat</span>
              </a>
              <a data-target="view-telemetry" class="nav-item flex items-center gap-space-sm px-space-md py-space-xs rounded-lg text-on-surface-variant hover:text-on-surface hover:bg-surface-container-highest/60 transition-colors cursor-pointer">
                <span class="material-symbols-outlined text-[18px]" data-icon="terminal">terminal</span>
                <span class="font-label-md text-label-md">Lab Diagnostics</span>
              </a>
            </nav>
          </div>
        </div>

        <!-- Sidebar Footer / Hardware Status Widget -->
        <div class="bg-surface-container-lowest rounded-xl p-space-md border border-black/[0.06] shadow-sm space-y-2">
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-1.5">
              <span id="footer-health-dot" class="w-2 h-2 rounded-full bg-secondary"></span>
              <span id="footer-health-text" class="font-mono-caption text-mono-caption font-semibold text-secondary">HEALTH 99.8% ONLINE</span>
            </div>
            <span class="text-mono-caption font-mono-caption text-on-surface-variant bg-surface-container-high px-1.5 py-0.5 rounded font-bold">NIR</span>
          </div>
          <div class="text-[11px] font-mono leading-relaxed text-on-surface-variant border-t border-black/[0.04] pt-2">
            <div><span class="text-outline">FW:</span> v2.4.1-ARM (F407)</div>
            <div class="truncate"><span class="text-outline">SN:</span> SL-880-STM407-RX04</div>
            <div><span class="text-outline">λ:</span> 880nm Infrared LOS</div>
          </div>
          <button id="btn-calibrate" class="w-full mt-1 flex items-center justify-center gap-1 py-1 px-2 rounded-md bg-surface-container-low hover:bg-surface-container text-primary font-label-sm text-label-sm border border-outline-variant/40 transition-colors">
            <span class="material-symbols-outlined text-[14px]" data-icon="adjust">adjust</span>
            Calibrate Optics
          </button>
        </div>
      </aside>

      <!-- Main Operational Viewports -->
      <div class="flex-1 flex flex-col overflow-hidden bg-[#F5F5F7]">
        <!-- View 1: Hardware Setup -->
        <div id="view-hardware" class="view-panel active flex-1 overflow-y-auto p-space-lg flex-col gap-space-lg bg-[#F5F5F7]">
          {p1}
        </div>

        <!-- View 2: File Transfer -->
        <div id="view-file" class="view-panel flex-1 bg-[#F5F5F7] overflow-y-auto p-space-xl flex-col gap-space-lg">
          {p2}
        </div>

        <!-- View 3: Realtime Chat -->
        <div id="view-chat" class="view-panel flex-1 bg-[#F5F5F7] overflow-y-auto p-space-lg flex-col gap-space-md">
          {p3}
        </div>

        <!-- View 4: Lab Telemetry -->
        <div id="view-telemetry" class="view-panel flex-1 bg-[#F5F5F7] overflow-y-auto p-space-lg flex-col gap-space-lg">
          {p4}
        </div>
      </div>
    </div>
  </div>

  <!-- Bridge Integration Script -->
  <script>
    let bridge = null;
    let selectedBaud = 115200;

    // Navigation Tab Switching
    document.querySelectorAll('.nav-item').forEach(item => {{
      item.addEventListener('click', () => {{
        document.querySelectorAll('.nav-item').forEach(n => {{
          n.className = 'nav-item flex items-center gap-space-sm px-space-md py-space-xs rounded-lg text-on-surface-variant hover:text-on-surface hover:bg-surface-container-highest/60 transition-colors cursor-pointer';
        }});
        item.className = 'nav-item flex items-center gap-space-sm px-space-md py-space-xs rounded-lg bg-primary-container text-on-primary font-medium shadow-sm transition-all cursor-pointer';

        const target = item.getAttribute('data-target');
        document.querySelectorAll('.view-panel').forEach(p => p.classList.remove('active'));
        const panel = document.getElementById(target);
        if (panel) panel.classList.add('active');

        const titleMap = {{
          'view-hardware': 'SpectraLink IR-OWC — Hardware & Optical Link Setup',
          'view-file': 'SpectraLink IR-OWC — File & Media Transfer Station',
          'view-chat': 'SpectraLink IR-OWC — Realtime Optical Chat Stream',
          'view-telemetry': 'SpectraLink IR-OWC — Lab Diagnostics & Telemetry'
        }};
        document.getElementById('window-center-title').innerText = titleMap[target] || 'SpectraLink IR-OWC';
      }});
    }});

    // Initialize Qt WebChannel
    if (typeof QWebChannel !== 'undefined') {{
      new QWebChannel(qt.webChannelTransport, (channel) => {{
        bridge = channel.objects.bridge;
        console.log('QWebChannel Bridge Connected Successfully!');
        setupBridgeHandlers();
        refreshPortList();
      }});
    }}

    function setupBridgeHandlers() {{
      if (!bridge) return;

      bridge.portConnected.connect((port, baud) => {{
        document.getElementById('ribbon-text').innerText = 'STM32 Optical Bridge • ' + port + ' (' + baud + ' bps)';
        document.getElementById('ribbon-dot').className = 'w-2 h-2 rounded-full bg-secondary status-pulse';
        document.getElementById('footer-health-text').innerText = 'HEALTH 100% ONLINE';
        const connBtn = document.querySelector('#view-hardware button.bg-primary-container');
        if (connBtn) {{
          connBtn.innerText = 'Disconnect';
          connBtn.className = 'flex-1 h-[32px] bg-error hover:bg-on-error-container text-white font-title-sm text-title-sm font-semibold rounded-lg shadow-sm flex items-center justify-center gap-1.5';
        }}
      }});

      bridge.portDisconnected.connect(() => {{
        document.getElementById('ribbon-text').innerText = 'STM32 Optical Bridge • Disconnected';
        document.getElementById('ribbon-dot').className = 'w-2 h-2 rounded-full bg-outline';
        document.getElementById('footer-health-text').innerText = 'OFFLINE';
        const connBtn = document.querySelector('#view-hardware button.bg-error');
        if (connBtn) {{
          connBtn.innerText = 'Connect';
          connBtn.className = 'flex-1 h-[32px] bg-primary-container hover:bg-primary text-on-primary font-title-sm text-title-sm font-semibold rounded-lg shadow-sm flex items-center justify-center gap-1.5';
        }}
      }});

      bridge.pingResult.connect((rtt, seq) => {{
        const rttElement = document.querySelector('#view-hardware .font-headline-md');
        if (rttElement) rttElement.innerText = rtt.toFixed(1) + ' ms';
        console.log('Ping Echo Received: seq #' + seq + ' | RTT: ' + rtt.toFixed(1) + ' ms');
      }});
    }}

    function refreshPortList() {{
      if (!bridge) return;
      bridge.scanPorts((portsJson) => {{
        const ports = JSON.parse(portsJson);
        const select = document.querySelector('#view-hardware select');
        if (select) {{
          select.innerHTML = '';
          ports.forEach(p => {{
            const opt = document.createElement('option');
            opt.value = p;
            opt.innerText = p.includes('MOCK') ? p + ' (Virtual Loopback)' : p + ' - STM32F407 CDC';
            select.appendChild(opt);
          }});
        }}
      }});
    }}

    // Bind Connect button
    document.addEventListener('click', (e) => {{
      const btn = e.target.closest('button');
      if (!btn) return;
      if (btn.innerText.includes('Connect') && bridge) {{
        const select = document.querySelector('#view-hardware select');
        const port = select ? select.value : 'MOCK_COM_LOOPBACK';
        bridge.connectPort(port, selectedBaud);
      }} else if (btn.innerText.includes('Disconnect') && bridge) {{
        bridge.disconnectPort();
      }} else if (btn.innerText.includes('Ping Test') && bridge) {{
        bridge.sendPing();
      }} else if (btn.innerText.includes('Rescan') && bridge) {{
        refreshPortList();
      }}
    }});
  </script>
</body>
</html>
"""

    os.makedirs("src/ir_terminal/ui/web", exist_ok=True)
    with open("src/ir_terminal/ui/web/index.html", "w", encoding="utf-8") as f:
        f.write(html_template)
    print("SPA generated cleanly at src/ir_terminal/ui/web/index.html")


if __name__ == "__main__":
    build_spa()
