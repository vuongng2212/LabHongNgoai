---
name: Modern Precision
colors:
  surface: '#f8f9ff'
  surface-dim: '#cbdbf5'
  surface-bright: '#f8f9ff'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#eff4ff'
  surface-container: '#e5eeff'
  surface-container-high: '#dce9ff'
  surface-container-highest: '#d3e4fe'
  on-surface: '#0b1c30'
  on-surface-variant: '#434655'
  inverse-surface: '#213145'
  inverse-on-surface: '#eaf1ff'
  outline: '#737686'
  outline-variant: '#c3c6d7'
  surface-tint: '#0053db'
  primary: '#004ac6'
  on-primary: '#ffffff'
  primary-container: '#2563eb'
  on-primary-container: '#eeefff'
  inverse-primary: '#b4c5ff'
  secondary: '#006c49'
  on-secondary: '#ffffff'
  secondary-container: '#6cf8bb'
  on-secondary-container: '#00714d'
  tertiary: '#784b00'
  on-tertiary: '#ffffff'
  tertiary-container: '#996100'
  on-tertiary-container: '#ffeedd'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#dbe1ff'
  primary-fixed-dim: '#b4c5ff'
  on-primary-fixed: '#00174b'
  on-primary-fixed-variant: '#003ea8'
  secondary-fixed: '#6ffbbe'
  secondary-fixed-dim: '#4edea3'
  on-secondary-fixed: '#002113'
  on-secondary-fixed-variant: '#005236'
  tertiary-fixed: '#ffddb8'
  tertiary-fixed-dim: '#ffb95f'
  on-tertiary-fixed: '#2a1700'
  on-tertiary-fixed-variant: '#653e00'
  background: '#f8f9ff'
  on-background: '#0b1c30'
  surface-variant: '#d3e4fe'
typography:
  display-lg:
    fontFamily: Plus Jakarta Sans
    fontSize: 32px
    fontWeight: '600'
    lineHeight: 40px
    letterSpacing: -0.02em
  headline-lg:
    fontFamily: Plus Jakarta Sans
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
    letterSpacing: -0.015em
  headline-md:
    fontFamily: Plus Jakarta Sans
    fontSize: 18px
    fontWeight: '600'
    lineHeight: 24px
    letterSpacing: -0.01em
  title-sm:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '600'
    lineHeight: 20px
    letterSpacing: -0.005em
  body-lg:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
    letterSpacing: 0em
  body-md:
    fontFamily: Inter
    fontSize: 13px
    fontWeight: '400'
    lineHeight: 18px
    letterSpacing: 0em
  label-md:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '500'
    lineHeight: 16px
    letterSpacing: 0.01em
  label-sm:
    fontFamily: Inter
    fontSize: 11px
    fontWeight: '500'
    lineHeight: 14px
    letterSpacing: 0.02em
  mono-data:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '500'
    lineHeight: 16px
    letterSpacing: -0.01em
  mono-caption:
    fontFamily: Inter
    fontSize: 10px
    fontWeight: '600'
    lineHeight: 12px
    letterSpacing: 0.04em
rounded:
  sm: 0.25rem
  DEFAULT: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  full: 9999px
spacing:
  gutter: 1rem
  margin: 1.25rem
  space-xs: 0.25rem
  space-sm: 0.5rem
  space-md: 0.75rem
  space-lg: 1.25rem
  space-xl: 1.75rem
---

## Brand & Style

This design system embodies the calculated elegance of high-end desktop engineering software, merging native macOS aesthetics with high-density utility. Designed for power users, hardware engineers, and systems analysts, it projects an atmosphere of surgical precision, quiet authority, and computational clarity.

The visual style synthesizes modern desktop minimalism with tactical tactile refinement:
- **Calibrated Cleanliness:** Pristine neutral canvas surfaces contrasted with crisp geometric outlines and micro-calibrated data readouts.
- **System Restraint:** High density without visual fatigue. Visual accents are reserved for actionable states, vital statuses, and dynamic telemetry.
- **Native Desktop Lineage:** Window geometry, native control ergonomics, and precise typography inherit the tactile predictability of top-tier desktop operating systems while offering specialized modular density.

## Colors

The palette is engineered around an architectural grayscale foundation, allowing structural hierarchies to emerge through pure luminance and delicate border contrasts. 

- **Canvas & Shell:** The root desktop background utilizes macOS System Gray (`#F5F5F7`), establishing a muted, anti-glare workbench. Elevated content windows and cards operate in pure White (`#FFFFFF`).
- **Primary Accent (`#2563EB`):** A vibrant cobalt blue driving directional flow, focused inputs, and primary control surfaces. It carries a focused photonic micro-glow (`rgba(37, 99, 235, 0.15)`) on active focus states.
- **Functional Semantics:**
  - **Success / Operational (`#10B981`):** Emerald green indicating active telemetry, stable connections, and completed processes.
  - **Warning / Degraded (`#F59E0B`):** Warm calibrated amber for telemetry warnings, thermal thresholds, and unsaved state transitions.
  - **Critical / Fault (`#EF4444`):** Crisp signal red for critical system exceptions, hardware faults, and destructive triggers.
- **Borders & Partitions:** Subtle borders (`rgba(0, 0, 0, 0.06)`) define structural boundaries without adding optical weight.

## Typography

Typography prioritizes micro-legibility and structural scanning. **Plus Jakarta Sans** provides structural character to titles, view headings, and metric summaries. **Inter** is deployed for body copy, controls, lists, and dense telemetry data.

- **Tabular Numerics:** All readouts, metric feeds, scientific data tables, and input fields mandate tabular lining figures (`font-variant-numeric: tabular-nums`) to prevent horizontal jitter during real-time data streaming.
- **Micro-Hierarchy:** Technical metadata and metric units utilize uppercase styling at 10px and 11px scales with widened tracking (`+0.04em`) to maintain sharp character distinction on high-DPI displays.

## Layout & Spacing

The layout is built for high-productivity desktop software using a three-pane structural split: global activity rail, contextual workspace sidebar, and primary operational viewport.

- **Grid Model:** Context-driven fluid layout anchored to an 8px base rhythm, with 4px sub-increments for high-density control groups and toolbar alignment.
- **Window Architecture:**
  - Outer desktop padding: `1.25rem` (20px).
  - Pane and column gaps: `1rem` (16px).
  - Component padding: strict adherence to the compact scale (`space-xs` to `space-md`), minimizing vertical footprint while ensuring 28px–32px accessible click targets.
- **Responsive Handling:** When horizontal resolution falls below 1024px, the inspector panel folds into an overlay drawer. Below 768px, secondary tool rails collapse into icon-only triggers.

## Elevation & Depth

Visual depth is achieved through delicate surface stratification rather than heavy shadows, preserving a clean, featherweight aesthetic.

- **Level 0 (Canvas Base):** macOS System Gray (`#F5F5F7`), non-elevated, matte.
- **Level 1 (Panels & Toolbars):** `#FFFFFF` with an ultralight structural border: `1px solid rgba(0, 0, 0, 0.06)` and ambient drop shadow: `0 2px 10px rgba(0, 0, 0, 0.04)`.
- **Level 2 (Popovers, Flyouts, Menus):** Pure white `#FFFFFF`, border: `1px solid rgba(0, 0, 0, 0.08)`, shadow: `0 8px 24px rgba(0, 0, 0, 0.08), 0 1px 3px rgba(0, 0, 0, 0.04)`.
- **Level 3 (Modal Dialogs):** Centered floating surfaces with `0 16px 40px rgba(0, 0, 0, 0.12)`, paired with an ultra-soft dimming backdrop (`rgba(0, 0, 0, 0.20)` with `backdrop-filter: blur(8px)`).
- **Focus Rings:** Interactive elements exhibit a distinct 2px outer aura: `0 0 0 3px rgba(37, 99, 235, 0.18)` on active keyboard navigation and input focus.

## Shapes

The geometry strikes a balance between friendly modern curvature and disciplined mechanical alignment:

- **Window Panes & Cards:** Fixed `14px` border radius (`rounded-lg`), mirroring modern macOS desktop application windows.
- **Controls & Inputs:** Radii of `8px` (`rounded-md`) for field inputs, drop-down menus, and tool buttons.
- **Pills & Status Indicators:** Fully rounded (`9999px`) for segmented control indicators, hardware status pills, telemetry chips, and filter badges.
- **Window Controls:** Native circular traffic light buttons (12px diameter) positioned with standard 8px spacing.

## Components

### Window Chrome & Traffic Lights
- **Traffic Lights:** Three 12px circular controls aligned horizontally with 8px gaps: Close (`#FF5F56`), Minimize (`#FFBD2E`), Expand (`#27C93F`). Hovering reveals subtle inner glyph icons (X, -, +) at 30% black opacity.
- **Header Bar:** Integrated title bar (38px height) housing traffic lights, centered document/project metadata, and unified search bar.

### Buttons & Segmented Controls
- **Primary Button:** `#2563EB` solid fill, white text, 8px radius, height 30px. Hover: `#1D4ED8`. Active: subtle inset transform (`scale(0.98)`).
- **Secondary Button:** Pure `#FFFFFF` background, `1px solid rgba(0, 0, 0, 0.12)` border, `#1E293B` text. Hover: `#F8FAFC`.
- **Segmented Control:** Recessed tray (`#E2E8F0`, 4px padding, 8px radius) containing sliding white rounded pill segments. Selected segment carries `0 1px 3px rgba(0, 0, 0, 0.08)` and bold label contrast.

### Form Inputs & Toggles
- **Text Inputs:** Height 30px, white surface, `1px solid rgba(0, 0, 0, 0.12)` border, 8px radius. Active focus transitions to `#2563EB` border with a 3px soft blue halo.
- **Checkboxes & Radios:** 16px size. Unchecked: `1px solid rgba(0, 0, 0, 0.20)` on white. Checked: solid `#2563EB` fill with crisp white geometric check/dot.

### Hardware Status Indicators & Telemetry Readouts
- **Status Pills:** Compact 20px height, rounded-full pill with an embedded 6px pulsing LED indicator (Emerald Green for online, Amber for calibrating, Red for fault).
- **Readout Cards:** Modular cards with a light border, containing a top micro-caption (10px uppercase, tracking `0.04em`), prominent tabular value (18px or 24px Plus Jakarta Sans), and an integrated sparkline or delta badge.

### Lists & Tables
- **Data Tables:** Alternating rows avoided; rows delineated solely by `1px solid rgba(0, 0, 0, 0.04)`. Row height 32px for maximum information density without sacrificing touch/click ergonomics. Hover state applies `#F8FAFC`.