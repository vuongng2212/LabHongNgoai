"""Component-based modular Web UI builder with Pixel-Perfect Design Matching for IR-OWC Lab Terminal."""

import os
import re

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
    res = res.replace('<select class="', '<select class="appearance-none ')
    return res


def extract_section(filepath: str, pattern: str) -> str:
    with open(filepath, encoding="utf-8") as f:
        content = f.read()
    match = re.search(pattern, content, re.DOTALL)
    if not match:
        return ""
    return sanitize(match.group(1))


def build_modular_components() -> None:
    comp_dir = "src/ir_terminal/ui/web/components"
    js_dir = "src/ir_terminal/ui/web/js"
    os.makedirs(comp_dir, exist_ok=True)
    os.makedirs(js_dir, exist_ok=True)

    # Helper to load existing component or extract from template
    def load_component(filename: str, template_path: str) -> str:
        dest_path = f"{comp_dir}/{filename}"
        if os.path.exists(dest_path):
            with open(dest_path, encoding="utf-8") as f:
                return sanitize(f.read())
        extracted = extract_section(template_path, r"<main[^>]*>(.*?)</main>")
        with open(dest_path, "w", encoding="utf-8") as f:
            f.write(extracted)
        return extracted

    v1 = load_component("view_hardware.html", "ui_templates/apple_style/1_1_hardware_setup.html")
    v2 = load_component("view_file.html", "ui_templates/apple_style/2_2_file_transfer.html")
    v3 = load_component("view_chat.html", "ui_templates/apple_style/3_3_realtime_chat.html")
    v4 = load_component("view_telemetry.html", "ui_templates/apple_style/4_4_lab_telemetry.html")

    header_path = f"{comp_dir}/chrome_header.html"
    if os.path.exists(header_path):
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
      <span id="ribbon-dot" class="w-2 h-2 rounded-full bg-secondary status-pulse"></span>
      <span id="ribbon-text" class="font-mono-data text-mono-data text-on-surface font-medium">STM32 Optical Bridge • COM4 (Ready)</span>
    </div>
  </div>

  <!-- Center Title with Icon -->
  <div class="flex items-center gap-2">
    <span class="material-symbols-outlined text-primary text-[16px]">terminal</span>
    <span id="window-center-title" class="font-title-sm text-title-sm text-on-surface font-semibold">SpectraLink IR-OWC — Hardware & Optical Link Setup</span>
  </div>

  <!-- Right Actions -->
  <div class="flex items-center gap-space-xs">
    <div class="flex items-center bg-surface-container-high/40 text-on-surface-variant px-2 py-0.5 rounded text-mono-caption font-mono-caption font-bold">
      SESSION #4029-A
    </div>
    <button id="btn-top-refresh" class="p-1 rounded hover:bg-surface-container-high text-on-surface-variant transition-colors" title="Refresh Telemetry">
      <span class="material-symbols-outlined text-[16px]">refresh</span>
    </button>
    <button class="p-1 rounded hover:bg-surface-container-high text-on-surface-variant transition-colors" title="Device Settings">
      <span class="material-symbols-outlined text-[16px]">settings</span>
    </button>
  </div>
</header>
"""
        with open(header_path, "w", encoding="utf-8") as f:
            f.write(chrome_header)

    sidebar_path = f"{comp_dir}/sidebar_nav.html"
    if os.path.exists(sidebar_path):
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
      <span class="material-symbols-outlined text-[14px]">adjust</span>
      Calibrate Optics
    </button>
  </div>
</aside>
"""
        with open(sidebar_path, "w", encoding="utf-8") as f:
            f.write(sidebar_nav)

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

  <!-- Scripts -->
  <script src="js/app.js"></script>
  <script src="js/bridge_client.js"></script>
  <script src="js/chat_controller.js"></script>
  <script src="js/file_controller.js"></script>
  <script src="js/telemetry_controller.js"></script>
</body>
</html>
"""

    with open("src/ir_terminal/ui/web/index.html", "w", encoding="utf-8") as f:
        f.write(full_index)

    print("Modular component architecture successfully built at src/ir_terminal/ui/web/")


if __name__ == "__main__":
    build_modular_components()
