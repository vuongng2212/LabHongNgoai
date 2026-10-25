"""Component-based modular Web UI builder with Pixel-Perfect Design Matching for IR-OWC Lab Terminal."""

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = PROJECT_ROOT / "src" / "ir_terminal" / "ui" / "web"
COMPONENTS_DIR = WEB_DIR / "components"

EXPLICIT_REPLACEMENTS = [
    # Corrupted BER formulas
    ("10—»—¹", "10<sup>-9</sup>"),
    ("10—»—¶", "10<sup>-6</sup>"),
    ("10—»Â¹—°", "10<sup>-10</sup>"),
    ("10â»â¹", "10<sup>-9</sup>"),
    ("10â»â¶", "10<sup>-6</sup>"),
    ("10â»Â¹â°", "10<sup>-10</sup>"),
    ("10<sup>-9</sup>⁰", "10<sup>-10</sup>"),
    ("10⁻⁹", "10<sup>-9</sup>"),
    ("10⁻¹⁰", "10<sup>-10</sup>"),
    ("10⁻⁶", "10<sup>-6</sup>"),
    ("BER < 10—»—¹", "BER &lt; 10<sup>-9</sup>"),
    ("BER &lt; 10—»—¹", "BER &lt; 10<sup>-9</sup>"),
    ("BER > 10—»—¶", "BER &gt; 10<sup>-6</sup>"),
    ("BER &gt; 10—»—¶", "BER &gt; 10<sup>-6</sup>"),
    # Corrupted bullets
    ("—¢", "•"),
    ("â¢", "•"),
    ("â€¢", "•"),
    ("—€¢", "•"),
    # Corrupted dashes
    ("—€—", "—"),
    ("â€”", "—"),
    ("â—", "—"),
    ("â€“", "–"),
    # Corrupted greek and math symbols
    ("Î»:", "λ:"),
    ("Î»", "λ"),
    ("Â°C", "°C"),
    ("Â°", "°"),
    ("Â±", "±"),
    (" Ã ", " × "),
    ("Ã—", "×"),
    ("Ã", "×"),
]


def sanitize(text: str) -> str:
    res = text
    for old, new in EXPLICIT_REPLACEMENTS:
        res = res.replace(old, new)

    # General regex cleanup for any remaining patterns
    res = re.sub(r"10[—â»⁻]+[¹⁹]", "10<sup>-9</sup>", res)
    res = re.sub(r"10[—â»⁻]+[¶⁶]", "10<sup>-6</sup>", res)
    res = re.sub(r"10[—â»⁻]+[⁰°]", "10<sup>-10</sup>", res)
    res = re.sub(r"[—â€]+¢", "•", res)
    res = re.sub(r"[\x80-\x9F]", "", res)

    # Ensure all select dropdowns have appearance-none for clean custom caret
    # (idempotent: skip selects that already carry the class).
    def _add_appearance(match: re.Match[str]) -> str:
        classes = match.group(1)
        if "appearance-none" in classes:
            return match.group(0)
        return f'<select class="appearance-none {classes}"'

    res = re.sub(r'<select class="([^"]*)"', _add_appearance, res)
    return res


def build_modular_components() -> None:
    COMPONENTS_DIR.mkdir(parents=True, exist_ok=True)

    # Load required view components directly from the project component directory.
    def load_component(filename: str) -> str:
        dest_path = COMPONENTS_DIR / filename
        if not dest_path.exists():
            raise FileNotFoundError(f"Required UI component not found: {dest_path}")
        with open(dest_path, encoding="utf-8") as f:
            return sanitize(f.read())

    v1 = load_component("view_hardware.html")
    v2 = load_component("view_file.html")
    v3 = load_component("view_chat.html")
    v4 = load_component("view_telemetry.html")

    settings_path = COMPONENTS_DIR / "settings_modal.html"
    if settings_path.exists():
        with open(settings_path, encoding="utf-8") as f:
            settings_modal = sanitize(f.read())
    else:
        settings_modal = ""

    feedback_path = COMPONENTS_DIR / "ui_feedback.html"
    if feedback_path.exists():
        with open(feedback_path, encoding="utf-8") as f:
            raw_feedback = f.read()
        # Keep only the mounts (toast stack + modal overlay); drop the header comment.
        body_start = raw_feedback.find("<div")
        feedback_mounts = sanitize(raw_feedback[body_start:] if body_start >= 0 else raw_feedback)
    else:
        feedback_mounts = ""

    header_path = COMPONENTS_DIR / "chrome_header.html"
    if header_path.exists():
        with open(header_path, encoding="utf-8") as f:
            chrome_header = f.read()
    else:
        chrome_header = """<!-- macOS Window Chrome Header -->
<header class="h-[38px] bg-surface-container-lowest border-b border-black/[0.08] px-space-md flex items-center justify-between flex-shrink-0 select-none">
  <!-- Traffic Lights & Ribbon Badge -->
  <div class="flex items-center gap-space-md">
    <div class="flex items-center gap-2 pr-2">
      <button aria-label="Close" class="traffic-dot w-3 h-3 rounded-full bg-[#FF5F56] border border-[#E0443E] flex items-center justify-center group focus:outline-none">
        <span class="text-[8px] text-black font-bold opacity-0 transition-opacity">×</span>
      </button>
      <button aria-label="Minimize" class="traffic-dot w-3 h-3 rounded-full bg-[#FFBD2E] border border-[#DEA123] flex items-center justify-center group focus:outline-none">
        <span class="text-[8px] text-black font-bold opacity-0 transition-opacity">-</span>
      </button>
      <button aria-label="Zoom" class="traffic-dot w-3 h-3 rounded-full bg-[#27C93F] border border-[#1AAB29] flex items-center justify-center group focus:outline-none">
        <span class="text-[7px] text-black font-bold opacity-0 transition-opacity">+</span>
      </button>
    </div>
    <div class="h-4 w-px bg-outline-variant/40"></div>
    <div class="flex items-center gap-space-xs bg-surface-container-low px-2 py-0.5 rounded-md border border-outline-variant/50">
      <span id="ribbon-dot" class="w-2 h-2 rounded-full bg-outline"></span>
      <span id="ribbon-text" class="font-mono-data text-mono-data text-on-surface font-medium">STM32 Optical Bridge • Disconnected</span>
    </div>
  </div>

  <!-- Center Title with Icon -->
  <div class="flex items-center gap-2">
    <span class="material-symbols-outlined text-primary text-[16px]">terminal</span>
    <span id="window-center-title" class="font-title-sm text-title-sm text-on-surface font-semibold">SpectraLink IR-OWC — Hardware & Optical Link Setup</span>
  </div>

  <!-- Right Actions -->
  <div class="flex items-center gap-space-xs">
    <span id="chrome-instance-label" class="font-mono-caption text-mono-caption text-on-surface-variant border border-outline-variant/50 px-2 py-0.5 rounded">IR-OWC</span>
    <button id="btn-top-refresh" class="p-1 rounded hover:bg-surface-container-high text-on-surface-variant transition-colors" title="Refresh Telemetry">
      <span class="material-symbols-outlined text-[16px]">refresh</span>
    </button>
    <button id="btn-top-settings" class="p-1 rounded hover:bg-surface-container-high text-on-surface-variant transition-colors" title="Transfer Settings">
      <span class="material-symbols-outlined text-[16px]">settings</span>
    </button>
  </div>
</header>
"""
        header_path.write_text(chrome_header, encoding="utf-8")

    sidebar_path = COMPONENTS_DIR / "sidebar_nav.html"
    if sidebar_path.exists():
        with open(sidebar_path, encoding="utf-8") as f:
            sidebar_nav = f.read()
    else:
        sidebar_nav = """<!-- Left Navigation Sidebar -->
<aside class="w-64 bg-surface-container-low/70 border-r border-black/[0.08] flex flex-col justify-between p-space-md select-none flex-shrink-0">
  <div class="space-y-space-md">
    <!-- Brand Block -->
    <div class="flex items-center gap-space-sm px-1 py-1">
      <div class="w-8 h-8 rounded-lg bg-primary-container text-white flex items-center justify-center shadow-sm">
        <span class="material-symbols-outlined text-[20px] text-white">radar</span>
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
        <a data-target="view-hardware" class="nav-item flex items-center gap-space-sm px-space-md py-space-xs rounded-lg bg-primary-container text-white font-medium shadow-sm transition-all cursor-pointer">
          <span class="material-symbols-outlined text-[18px]">tune</span>
          <span class="font-label-md text-label-md">Hardware & Link</span>
        </a>
        <a data-target="view-file" class="nav-item flex items-center gap-space-sm px-space-md py-space-xs rounded-lg text-on-surface-variant hover:text-on-surface hover:bg-surface-container-highest/60 transition-colors cursor-pointer">
          <span class="material-symbols-outlined text-[18px]">swap_horiz</span>
          <span class="font-label-md text-label-md">File Transfer</span>
        </a>
        <a data-target="view-chat" class="nav-item flex items-center gap-space-sm px-space-md py-space-xs rounded-lg text-on-surface-variant hover:text-on-surface hover:bg-surface-container-highest/60 transition-colors cursor-pointer">
          <span class="material-symbols-outlined text-[18px]">forum</span>
          <span class="font-label-md text-label-md">Realtime Chat</span>
        </a>
        <a data-target="view-telemetry" class="nav-item flex items-center gap-space-sm px-space-md py-space-xs rounded-lg text-on-surface-variant hover:text-on-surface hover:bg-surface-container-highest/60 transition-colors cursor-pointer">
          <span class="material-symbols-outlined text-[18px]">terminal</span>
          <span class="font-label-md text-label-md">Lab Diagnostics</span>
        </a>
      </nav>
    </div>
  </div>

  <!-- Sidebar Footer / Hardware Status Widget -->
  <div class="bg-surface-container-lowest rounded-xl p-space-md border border-black/[0.06] shadow-sm space-y-2">
    <div class="flex items-center justify-between">
      <div class="flex items-center gap-1.5">
        <span id="footer-health-dot" class="w-2 h-2 rounded-full bg-outline"></span>
        <span id="footer-health-text" class="font-mono-caption text-mono-caption font-medium text-outline">OFFLINE</span>
      </div>
      <span class="text-[10px] font-mono text-outline tracking-widest">UART</span>
    </div>
    <div class="text-[11px] font-mono leading-relaxed text-on-surface-variant border-t border-black/[0.06] pt-2 flex flex-col gap-1">
      <div class="flex items-center justify-between gap-2">
        <span class="text-outline">Ports:</span>
        <span id="footer-port-label" class="font-medium text-on-surface truncate text-right">Disconnected</span>
      </div>
      <div class="flex items-center justify-between">
        <span class="text-outline">Baud:</span>
        <span id="footer-baud-label" class="font-medium text-on-surface">--</span>
      </div>
    </div>
    <button id="btn-calibrate" class="w-full mt-1 flex items-center justify-center gap-1 py-1 px-2 rounded-md bg-surface-container-low hover:bg-surface-container text-primary font-label-sm text-label-sm border border-outline-variant/40 transition-colors">
      <span class="material-symbols-outlined text-[14px]">adjust</span>
      Calibrate Optics
    </button>
  </div>
</aside>
"""
        sidebar_path.write_text(sidebar_nav, encoding="utf-8")

    # 5. Assemble index.html
    full_index = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta content="width=device-width, initial-scale=1.0" name="viewport"/>
  <title>SpectraLink IR-OWC Workstation</title>
  <!-- Offline Local Fonts & Full Glyph TTF Icons -->
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
          borderRadius: {{
            "DEFAULT": "0.25rem",
            "lg": "0.5rem",
            "xl": "0.75rem",
            "2xl": "0.875rem",
            "full": "9999px"
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
            "headline-md": ["Plus Jakarta Sans", "sans-serif"],
            "display-lg": ["Plus Jakarta Sans", "sans-serif"],
            "body-md": ["Inter", "sans-serif"],
            "body-lg": ["Inter", "sans-serif"],
            "mono-caption": ["Inter", "monospace"],
            "headline-lg": ["Plus Jakarta Sans", "sans-serif"],
            "mono-data": ["Inter", "monospace"],
            "label-sm": ["Inter", "sans-serif"],
            "label-md": ["Inter", "sans-serif"],
            "title-sm": ["Inter", "sans-serif"]
          }},
          fontSize: {{
            "headline-md": ["18px", {{ lineHeight: "24px", letterSpacing: "-0.01em", fontWeight: "600" }}],
            "display-lg": ["32px", {{ lineHeight: "40px", letterSpacing: "-0.02em", fontWeight: "600" }}],
            "body-md": ["13px", {{ lineHeight: "18px", letterSpacing: "0em", fontWeight: "400" }}],
            "body-lg": ["14px", {{ lineHeight: "20px", letterSpacing: "0em", fontWeight: "400" }}],
            "mono-caption": ["10px", {{ lineHeight: "12px", letterSpacing: "0.04em", fontWeight: "600" }}],
            "headline-lg": ["24px", {{ lineHeight: "32px", letterSpacing: "-0.015em", fontWeight: "600" }}],
            "mono-data": ["12px", {{ lineHeight: "16px", letterSpacing: "-0.01em", fontWeight: "500" }}],
            "label-sm": ["11px", {{ lineHeight: "14px", letterSpacing: "0.02em", fontWeight: "500" }}],
            "label-md": ["12px", {{ lineHeight: "16px", letterSpacing: "0.01em", fontWeight: "500" }}],
            "title-sm": ["14px", {{ lineHeight: "20px", letterSpacing: "-0.005em", fontWeight: "600" }}]
          }}
        }}
      }}
    }}
  </script>
  <style>
    /* Sleek Native Scrollbar */
    ::-webkit-scrollbar {{
      width: 6px;
      height: 6px;
    }}
    ::-webkit-scrollbar-track {{
      background: transparent;
    }}
    ::-webkit-scrollbar-thumb {{
      background: rgba(0, 0, 0, 0.12);
      border-radius: 9999px;
    }}
    ::-webkit-scrollbar-thumb:hover {{
      background: rgba(0, 0, 0, 0.25);
    }}
    .traffic-dot:hover span {{
      opacity: 0.6;
    }}
    .view-panel {{
      display: none;
    }}
    .view-panel.active {{
      display: flex;
    }}
    /* Reusable UI Feedback System: button spinner + toast/modal motion */
    @keyframes ui-spin {{
      to {{ transform: rotate(360deg); }}
    }}
    .ui-spinner {{
      width: 14px;
      height: 14px;
      border-radius: 9999px;
      border: 2px solid rgba(255, 255, 255, 0.45);
      border-top-color: #ffffff;
      animation: ui-spin 0.7s linear infinite;
      display: inline-block;
      flex-shrink: 0;
    }}
    @keyframes toast-slide-in {{
      from {{ opacity: 0; transform: translateX(16px); }}
      to {{ opacity: 1; transform: translateX(0); }}
    }}
    .toast-item {{
      animation: toast-slide-in 0.18s ease-out;
    }}
    .toast-leaving {{
      opacity: 0;
      transform: translateX(16px);
      transition: opacity 0.18s ease, transform 0.18s ease;
    }}
    @keyframes modal-pop-in {{
      from {{ opacity: 0; transform: scale(0.96) translateY(6px); }}
      to {{ opacity: 1; transform: scale(1) translateY(0); }}
    }}
    #modal-overlay.flex #modal-card {{
      animation: modal-pop-in 0.16s ease-out;
    }}
    #settings-overlay.flex #settings-card {{
      animation: modal-pop-in 0.16s ease-out;
    }}
  </style>
  <script src="qwebchannel.js"></script>
</head>
<body class="bg-[#F5F5F7] text-on-surface antialiased min-h-screen flex flex-col font-body-md text-body-md select-none overflow-hidden">
  <!-- Window Container with Native macOS Frame -->
  <div class="w-full h-screen bg-[#F5F5F7] flex flex-col overflow-hidden">
    {chrome_header}

    <!-- App Body: Left Navigation + Primary Content Canvas -->
    <div class="flex flex-1 overflow-hidden">
      {sidebar_nav}

      <!-- Main Operational Viewports -->
      <div class="flex-1 flex flex-col overflow-hidden bg-[#F5F5F7]">
        <!-- View 1: Hardware Setup -->
        <div id="view-hardware" class="view-panel active flex-1 overflow-y-auto p-space-lg flex-col gap-space-lg bg-[#F5F5F7]">
          {v1}
        </div>

        <!-- View 2: File Transfer -->
        <div id="view-file" class="view-panel flex-1 bg-[#F5F5F7] overflow-y-auto p-space-xl flex-col gap-space-lg">
          {v2}
        </div>

        <!-- View 3: Realtime Chat -->
        <div id="view-chat" class="view-panel flex-1 bg-[#F5F5F7] overflow-y-auto p-space-lg flex-col gap-space-md">
          {v3}
        </div>

        <!-- View 4: Lab Diagnostics -->
        <div id="view-telemetry" class="view-panel flex-1 bg-[#F5F5F7] overflow-y-auto p-space-lg flex-col gap-space-lg">
          {v4}
        </div>
      </div>
    </div>
  </div>

  {settings_modal}

  <!-- Reusable UI Feedback mounts (see components/ui_feedback.html) -->
  {feedback_mounts}

  <!-- Scripts -->
  <script src="js/ui_feedback.js"></script>
  <script src="js/shared/utils.js"></script>
  <script src="js/shared/bridge.js"></script>
  <script src="js/app.js"></script>
  <script src="js/bridge_client.js"></script>
  <script src="js/chat_controller.js"></script>
  <script src="js/transfer_history.js"></script>
  <script src="js/file_controller.js"></script>
  <script src="js/telemetry_charts.js"></script>
  <script src="js/telemetry_controller.js"></script>
  <script src="js/settings_controller.js"></script>
</body>
</html>
"""

    with open(WEB_DIR / "index.html", "w", encoding="utf-8") as f:
        f.write(full_index)

    print("Modular component architecture successfully built at src/ir_terminal/ui/web/")


if __name__ == "__main__":
    build_modular_components()
