# KYDE Audit Dashboard — Design System: **Editorial Mono**

> **Für die IT / für Claude Code:** Dieses Dokument ist die verbindliche Design-Spezifikation für das KYDE Audit Dashboard im Stil „Editorial Mono". Es ist so geschrieben, dass ein Coding-Agent (z. B. Claude Code) es direkt umsetzen kann. Stack-Annahme: **React + shadcn/ui + Tailwind CSS + Recharts**. Wo immer möglich werden shadcn-Token-Namen verwendet, damit ihr nur eure Theme-Variablen tauschen müsst. Tokens und Code sind sprach-neutral; Prosa ist bewusst knapp gehalten.

---

## 0. Inhaltsverzeichnis
1. [Design-Prinzipien](#1-design-prinzipien)
2. [Foundations: Farben](#2-foundations--farben)
3. [Foundations: Typografie](#3-foundations--typografie)
4. [Foundations: Spacing, Radius, Borders, Elevation](#4-foundations--spacing-radius-borders-elevation)
5. [Daten-Visualisierung (das Kern-Merkmal)](#5-daten-visualisierung--das-kern-merkmal)
6. [Komponenten-Bibliothek](#6-komponenten-bibliothek)
7. [Screen-für-Screen-Spezifikation](#7-screen-für-screen-spezifikation)
8. [Do / Don't](#8-do--dont)
9. [Copy-paste: globals.css + Tailwind + Fonts](#9-copy-paste-globalscss--tailwind--fonts)

---

## 1. Design-Prinzipien

Editorial Mono ist eine **ruhige, redaktionelle** Sprache im Geist von Linear / Vercel. Fünf Regeln, an die sich jede Seite hält:

1. **Borders statt Schatten.** Flächen werden durch 1px-Hairline-Borders (`#e6e6e6`) getrennt, **nicht** durch Schlagschatten. Schatten sind praktisch verboten (Ausnahme: Popover/Dropdown/Tooltip, dezent).
2. **Monochrome Daten.** Diagramme sind monochrom — eine **Blau-Rampe** (`#2563eb` kräftig → hell), der wichtigste Wert am sättigsten; Linien-Charts in neutralem Dunkel (`#171717`). Keine Regenbogen-Paletten. Severity-Rot/Gelb/Grün **niemals** in Diagrammen — nur in Alert-Kontexten.grammen — nur in Alert-Kontexten.
3. **Mono-Labels.** Alle Klein-Labels, Kennzahl-Captions, IDs, Zeitstempel, Achsen und Tabellen-IDs stehen in einer Monospace (`Geist Mono`), UPPERCASE + getrackt für Sektions-Labels.
4. **Ein Akzent, sparsam.** Blau ist der einzige Marken-Akzent in der UI. Gelb (`#ca8a04`) erscheint nur als seltener Marken-Funke (z. B. „DATA INTEGRITY"-Badge), nie als Flächenfarbe.
5. **Severity vs. Status — zwei getrennte Farb-Achsen.** **Severity** (Critical/High/Medium/Low) nutzt die semantische Rot/Orange/Amber/Grün-Skala. **Status/State** (OBSERVED, IN REVIEW, VERIFIED, BLOCKED) ist *kein* Schweregrad und leiht sich **nie** Severity-Farben — eigene ruhige Palette (Neutral/Blau/Grün/Rot, §6.6). KPI-Zahlen bleiben neutral-schwarz (`#0a0a0a`). Im Zweifel: **neutral**.

**Anmutung:** scharfe Ecken (6px), viel Weißraum, hoher Kontrast Text/Fläche, präzise Zahlen mit `font-variant-numeric: tabular-nums`.

---

## 2. Foundations — Farben

Rohe Token-Werte (HEX). Die Mapping-Tabelle nach shadcn folgt in §9.

### Neutrals (Text & Flächen)
| Rolle | HEX | Einsatz |
|---|---|---|
| `page` | `#ffffff` | App-Hintergrund |
| `surface` | `#ffffff` | Karten, Sidebar, Tabellen |
| `border` | `#e6e6e6` | Karten-/Tabellen-/Input-Border (Standard-Trennlinie) |
| `border-subtle` | `#ededed` | Sidebar-Divider, sehr leise Trenner |
| `hover` | `#f6f6f6` | Hover-Fläche (Nav, Tabellenzeile) |
| `active` | `#f3f3f3` | aktive Nav, gedrückte Segmente |
| `track` | `#f7f7f7` | Balken-Hintergrund / leere Tracks |
| `text-strong` | `#0a0a0a` | Überschriften, KPI-Zahlen, primärer Text |
| `text-base` | `#3d3d3d` | Fließtext, Tabellen-Inhalt |
| `text-muted` | `#737373` | Labels, sekundärer Text, Achsen |
| `text-faint` | `#a3a3a3` | Zeitstempel, Platzhalter, tertiär |

### Akzent (Blau — der einzige UI-Akzent)
| Rolle | HEX |
|---|---|
| `primary` | `#2563eb` |
| `primary-hover` | `#1d4ed8` |
| `primary-soft` | `#f0f4fd` (Avatar-/Highlight-Fläche) |
| `primary-foreground` | `#ffffff` |
| `secondary-blue` | `#93b4f5` (zweiter Blauton im Datensystem) |

### Marke (sehr sparsam)
| Rolle | HEX | Einsatz |
|---|---|---|
| `brand-yellow` | `#ca8a04` | nur „DATA INTEGRITY"-Funke / Logo-Akzent |
| `brand-green` | `#15803d` | „VERIFIED" / OK-Status |
| `brand-red` | `#dc2626` | Marker / destruktive Akzente |
| `brand-mist` | `#dae4e5` (Text `#33403f`) | Eyebrow-Chip-Fläche für **Sektions-Labels** — das wiederkehrende Website-Device |

### Severity (nur in Alert-/Status-Kontexten)
„Flat"-Variante: Text-Farbe + leichte Tönung als Fläche + 1px-Border in `fg` @ 13% Deckung.
| Stufe | `fg` (Text/Border) | `bg` (Fläche) |
|---|---|---|
| Critical | `#b42318` | `#fef3f2` |
| High | `#b54708` | `#fef6ee` |
| Medium | `#854d0e` | `#fefaeb` |
| Low | `#157f3b` | `#f0faf3` |

### Status / State (eigene Farbachse — **nicht** Severity)
Lebenszyklus-Zustände. Gleiche Badge-Form wie Severity, aber eigene Palette — niemals Amber/Braun für einen Status.
| Achse | Zustände | `fg` | `bg` | `border` |
|---|---|---|---|---|
| Neutral (Default) | OBSERVED · OPEN · NEW · CLOSED | `#525252` | `#f3f3f3` | `#e6e6e6` |
| Blau (aktiv) | IN REVIEW · MONITORING · CONNECTED | `#2563eb` | `#f0f4fd` | `#d7e2fb` |
| Grün (ok) | VERIFIED · RESOLVED · ENABLED | `#157f3b` | `#f0faf3` | `#cdeed7` |
| Rot (blockiert) | BLOCKED · ESCALATED · FAILED | `#b42318` | `#fef3f2` | `#f7d4cf` |

### Chart-Palette
| Rolle | HEX |
|---|---|
| Linie (primär) | `#171717` |
| Akzent-Serie / Top-Balken | `#2563eb` |
| Blau-Rampe (Rang 1→5, heller werdend) | `#2563eb`, `#5283ec`, `#7ba2f1`, `#a6c1f6`, `#cddffb` |
| Grid-Linien | `#f0f0f0` (gestrichelt `3 4`) |
| Achsen-Text/Ticks | `#a3a3a3` |
| Peak-Marker | `#dc2626` |
| Balken-Track | `#f7f7f7` |

> **Dark-Mode** ist in Editorial Mono optional und nicht Teil dieses Specs. Wenn nötig, separat anfragen — die Light-Variante ist die kanonische.

---

## 3. Foundations — Typografie

**Schriften:** Sans = **Geist**, Mono = **Geist Mono** (Fallback `JetBrains Mono`). Einbindung siehe §9.

- Sans für Überschriften, Fließtext, Buttons, Nav.
- Mono für: Sektions-Labels („eyebrows"), KPI-Zahlen, IDs, Zeitstempel, Achsen, Badge-Text, Tabellen-IDs.

### Type-Scale
| Token | Größe / Weight / Tracking | Font | Einsatz |
|---|---|---|---|
| `display` | 28px / 700 / −0.02em | Sans | Seiten-H1 („Fleet Status") |
| `h2` | 19px / 600 / −0.01em | Sans | Sektions-Überschrift („Recent Activity") |
| `kpi` | 34px / 600 / −0.01em | **Mono** | große Kennzahl |
| `kpi-word` | 26px / 600 | **Mono** | textuelle Kennzahl („VERIFIED") |
| `body` | 14px / 400–500 | Sans | Standardtext, Tabellen |
| `body-sm` | 13.5px / 400 | Sans | sekundär in Zeilen |
| `eyebrow` | 11px / 500 / **0.09em** / UPPERCASE | **Mono** | Karten-/Sektions-Label, `text-muted` |
| `badge` | 11px / 600 / 0.06em / UPPERCASE | **Mono** | Severity-/Status-Badges |
| `mono-meta` | 12–12.5px / 400–500 | **Mono** | IDs, Zeitstempel, Achsen |

Zahlen immer mit `font-variant-numeric: tabular-nums`.

**Eyebrow-Utility** (das prägende Detail) — als Tailwind-Component-Class:
```css
.eyebrow {
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.09em;
  text-transform: uppercase;
  color: var(--text-muted); /* #737373 */
}
```

**Eyebrow-Chip (Marken-Device)** — die `#dae4e5`-Variante von der Website (`↳ LABEL`). Regel: **Sektions-Labels** (Karten-Titel wie „AGENT ACTIVITY", Sektions-Marker) bekommen das Chip; **Feld-/Inline-Labels** (KPI-Mini-Labels, Banner-Eyebrow) bleiben schlicht (`.eyebrow`). So bleibt das Mint präsent, aber nie dominant.
```css
.eyebrow-chip {
  display: inline-flex; align-items: center; gap: 7px;
  font-family: var(--font-mono);
  font-size: 11px; font-weight: 500; letter-spacing: 0.09em; text-transform: uppercase;
  color: var(--brand-mist-foreground);     /* #33403f */
  background: var(--brand-mist);            /* #dae4e5 */
  padding: 4px 9px; border-radius: 5px; white-space: nowrap;
}
.eyebrow-chip::before { content: "↳"; font-weight: 600; opacity: 0.55; }
```

---

## 4. Foundations — Spacing, Radius, Borders, Elevation

- **Spacing-Basis:** 4px-Raster. Gebräuchlich: 6 · 8 · 12 · 14 · 16 · 18 · 22 · 34 · 38.
- **Radius:** Karten/Inputs/Buttons = **6px** (`--radius: 0.375rem`). Kleine Badges = 5px. Pills/Filter = `9999px`. Diagramm-Balken = 1px (quasi scharf).
- **Borders:** Standard `1px solid #e6e6e6`. Sidebar-Divider `1px solid #ededed`.
- **Elevation:** **`box-shadow: none`** auf Karten. Erlaubt nur für schwebende Layer:
  - Popover/Dropdown/Tooltip: `0 8px 28px rgba(0,0,0,.10), 0 0 0 1px rgba(0,0,0,.05)`.
- **Focus-Ring:** `0 0 0 2px #ffffff, 0 0 0 4px rgba(37,99,235,.45)` (Ring in `primary`).

### Layout-Gerüst
- **App-Shell:** feste Sidebar `width: 252px` (Border rechts) + scrollbarer Main-Bereich.
- **Sidebar-Padding:** `22px 16px 16px`.
- **Main-Padding:** `34px 38px`. Optionale Inhalts-Maxbreite ~1400px, links ausgerichtet.
- **Karten-Padding:** `22px` (Stat-Karten `20px 22px`).
- **Grid:** KPI-Reihe = 4 gleiche Spalten (`gap: 16px`). Chart-Reihe = 2 Spalten, leicht asymmetrisch erlaubt (`1.05fr 0.95fr`).

### Density & Rhythm — *die Quelle des „spacey"-Gefühls*

**Prinzip (bewusst halten, nicht zubauen):** Editorial Mono ist **niedrig in der Dichte**. Weißraum ist ein Gestaltungsmittel, kein verschwendeter Platz — ein Gedanke pro Block, Karten dürfen atmen, lieber großzügige Luft als gequetschte Information. Genau das erzeugt die Ruhe; es entsteht nicht zufällig, sondern aus den folgenden Werten.

**Line-Heights (Leading):**
| Rolle | line-height |
|---|---|
| `display` / H1 | 1.05 |
| `h2` | 1.2 |
| `body` / Tabellen | 1.5 |
| Beschreibung / Subtitle | 1.45 |
| `eyebrow` / Labels / KPI-Zahl | 1.0 (tight) |

**Vertikaler Rhythmus (Main-Spalte, von oben nach unten)** — exakt diese Abfolge hält die Seite ruhig:
| von → nach | Abstand |
|---|---|
| Top-Padding (Main-Bereich) | `34px` |
| H1 → Subtitle | `6px` |
| Header-Block → Banner | `22px` |
| Banner → KPI-Reihe | `20px` |
| KPI-Reihe → Chart-Reihe | `16px` |
| Chart-Reihe → „Recent Activity"-Header | `18px` |
| Section-Header → Inhalt | `12px` |
| Karten-Titel (Eyebrow-Chip) → Karten-Inhalt | `18px` |
| KPI-Label → Wert | `14px` |
| Tabellen-/Listenzeile (oben + unten) | `13px` |

**Innen-Rhythmus:**
- Karten-Padding `22px` (Stat-Karten `20px 22px`).
- Nav-Items `padding 9px 12px`, `gap 2px` zwischen Items, Gruppen-Divider `margin 14px 4px`.
- Grid-Gaps `16px` (KPI & Charts).

**Skalierung:** Auf breiten Screens **nicht strecken** — Inhalt linksbündig mit Maxbreite ~1400px; der Weißraum rechts ist gewollt. Auf engeren Screens zuerst Grid-Spalten umbrechen (KPI 4→2, Charts 2→1), aber Paddings und Line-Heights beibehalten — die Dichte bleibt konstant, nur die Spaltenzahl ändert sich.

---

## 5. Daten-Visualisierung — das Kern-Merkmal

Das ist der wichtigste Unterschied zum Alt-Zustand. **Keine bunten Diagramme.** Regeln:

- **Monochrome Blau-Rampe:** Balken-Serien nutzen Abstufungen *eines* Blautons (`#2563eb` kräftig → `#cddffb` hell), absteigend nach Rang. Der wichtigste Balken ist am sättigsten, die folgenden werden heller — **nicht** abrupt schwarz/grau. Beispiel „Top 5 Active Agents": Rang 1 kräftiges Blau, Rang 2–5 zunehmend heller.
- **Liniendiagramm:** Linie `#171717`, Breite 2.5, abgerundete Joins; Flächenfüllung = vertikaler Gradient der Linienfarbe von 14% → 0% Deckung; Grid `#f0f0f0` gestrichelt; Nulllinie durchgezogen; Achsen-Ticks in Mono `#a3a3a3`; optionale „avg"-Linie gestrichelt; **ein** Peak-Marker als Punkt in `#dc2626` (r≈4.5) mit weißem Rand.
- **Gestapelte Balken (z. B. Usage):** **zwei Serien, zwei Blautöne statt Blau+Gelb** → `Prompt` = helles Blau `#a6c1f6`, `Completion` = `#2563eb`. Legende in Mono.
- **Achsen/Grid:** Ticks Mono 11px `#a3a3a3`; Grid dünn, gestrichelt `3 4`, `#f0f0f0`.
- **Tracks:** wo Balken vor leerer Skala sitzen (horizontale Bars), Track-Rechteck `#f7f7f7`.
- **Tooltips:** weiße Karte, 1px-Border, dezenter Popover-Shadow, Werte in Mono.

### Recharts-Theming (Beispiel: horizontaler Top-5-Bar)
```tsx
const RAMP = ["#2563eb", "#5283ec", "#7ba2f1", "#a6c1f6", "#cddffb"];

<BarChart layout="vertical" data={rows} barCategoryGap="38%">
  <CartesianGrid horizontal={false} stroke="#f0f0f0" strokeDasharray="3 4" />
  <XAxis type="number" domain={[0, 600]} tick={{ fontFamily: "var(--font-mono)", fontSize: 11, fill: "#a3a3a3" }}
         tickLine={false} axisLine={false} />
  <YAxis type="category" dataKey="label" width={132}
         tick={{ fontFamily: "var(--font-mono)", fontSize: 11.5, fill: "#a3a3a3" }}
         tickLine={false} axisLine={false} />
  <Bar dataKey="value" radius={1} background={{ fill: "#f7f7f7" }}>
    {rows.map((_, i) => <Cell key={i} fill={RAMP[Math.min(i, RAMP.length - 1)]} />)}
  </Bar>
</BarChart>
```
Linien-Chart analog: `<Area>` mit `stroke="#171717"`, `fill="url(#g)"` (Gradient `#171717` 0.14→0), `<CartesianGrid stroke="#f0f0f0" strokeDasharray="3 4" />`.

### Network Map / Sankey
Links **neutral** (`#e5e5e5`, ~35% Deckung), Knoten-Spalten in zurückhaltenden Grautönen; **maximal eine** Spalte (z. B. „Model"/Terminal) darf `primary`-Blau tragen. Knoten-Labels in Mono. **Nicht** die aktuelle gelb/rot/grün-Knotenfärbung übernehmen.

---

## 6. Komponenten-Bibliothek

Alle Maße sind die kanonischen Werte aus dem Prototyp.

### 6.1 App-Shell / Sidebar
- Container: `width 252px`, `bg #fff`, `border-right 1px #ebebeb`, Flex-Spalte.
- **Logo-Zeile:** Wortmarke `KYDE` (Sans, 22px, 800, −0.02em, `#0a0a0a`) mit kleinem Rauten-Glyph davor; rechts Glocke (`#525252`) mit roter `9+`-Bubble (Mono 9px).
- **DATA-INTEGRITY-Badge:** grüner Pill, `bg #eaf7f0`, `color #15803d`, Mono 11px/600/0.06em, kleiner Punkt links. (Einziger grüner Marken-Funke in der Shell.)
- **Konsolen-Titel:** „Audit Dashboard" (17px/700), darunter „Agent Governance Console" (Mono 12px, `#737373`).
- **Nav-Gruppen** durch Divider (`1px #ededed`, Margin `14px 4px`) getrennt: (1) Fleet Status, Threats & Alerts, Agent Chains, Network Map, Agents, Hosts, Usage & Cost — (2) Users, MCP Servers, AI Providers, Policies — (3) Labels, Pricing, Admin Actions, Settings.
- **Nav-Item:** `padding 9px 12px`, `radius 6–8px`, `gap 12`, Icon 18px (1.7 stroke). Inaktiv: `color #525252`, `weight 500`. Hover: `bg #f6f6f6`. Aktiv: `bg #f3f3f3`, `color #0a0a0a`, `weight 600`, Icon stroke 2. **Kein** linker Akzentbalken, **kein** farbiger Hintergrund — neutral.
- **User-Karte (unten):** 1px-Border, `radius 9–12px`, `padding 11`. Avatar 32px (`radius 8`, `bg #f0f4fd`, `color #2563eb`, Mono 12/700, Initialen). Name Mono 13/600. Badges: `ADMIN` (Severity-Critical fg/bg), `VIEWER` (`#737373` auf `#f1f3f5`), Mono 9px. Rechts Up/Down-Chevrons (`#a3a3a3`).

### 6.2 Seiten-Header
H1 `display` (28/700/−0.02em, `#0a0a0a`) + Untertitel (15px, `#737373`). Optional rechts ein Zeitraum-Select oder „updated 0 seconds ago" (Mono, `#a3a3a3`).

### 6.3 Stat- / KPI-Karte
- `bg #fff`, `border 1px #e6e6e6`, `radius 6`, **kein Schatten**, `padding 20px 22px`, Flex-Spalte, `gap 14`.
- Oben `.eyebrow`-Label. Darunter Wert in **Mono** 34px/600/−0.01em, `#0a0a0a`.
- Status-Wert (z. B. „VERIFIED"): 26px, `#15803d`, mit kleinem Shield-/Check-Icon links.
- **Zahlen bleiben neutral-schwarz** — kein rotes „34". Bedeutung kommt über Label/Kontext, nicht über Einfärben der Zahl.

### 6.4 Alert-Banner (Variante „minimal")
Kein Flächen-Fill. Aufbau in einer Zeile, darunter 1px-Border-Bottom (`#e6e6e6`), `padding-bottom 16px`:
- Roter Punkt (9px, `#b42318`).
- `.eyebrow` „BREACH" in `#b42318` (600).
- Titel: „1 critical incident in progress" (16px/600, `#0a0a0a`).
- Subtext: „Immediate action required · detected 3 min ago" (13.5px, `#737373`).
- Rechts ausgerichteter Solid-Button „View incident →": `bg #b42318`, `color #fff`, `radius 6–7`, `padding 9px 16px`, Sans 13/600, Pfeil-Icon.
Severity bestimmt Punkt-/Eyebrow-/Button-Farbe (Critical→rot). Bei niedrigeren Stufen entsprechende Severity-`fg` verwenden.

### 6.5 Severity-Badge (flat)
`display: inline-flex`, Mono 11px/600/0.06em, UPPERCASE, `padding 3px 8px`, `radius 5`, `color = sev.fg`, `bg = sev.bg`, `border 1px sev.fg@13%`. Werte siehe §2.

### 6.6 Status-Badge (nicht-Severity) — eigene Farbachse
**Status ist kein Schweregrad.** Status-Badges dürfen sich **niemals** Severity-Farben ausleihen (kein Amber/Braun für `OBSERVED`!). Eigene, ruhige 4-Farben-Palette; gleiche Badge-Form wie Severity (Mono 11px/600/0.06em, UPPERCASE, `padding 3px 8px`, `radius 5`, Punkt links optional):

| Klasse | Zustände | `fg` | `bg` | `border` |
|---|---|---|---|---|
| **Neutral** (Default) | `OBSERVED` `OPEN` `NEW` `CLOSED` | `#525252` | `#f3f3f3` | `#e6e6e6` |
| **Blau** (aktiv/laufend) | `IN REVIEW` `MONITORING` `IN PROGRESS` `CONNECTED` | `#2563eb` | `#f0f4fd` | `#d7e2fb` |
| **Grün** (ok/erledigt) | `VERIFIED` `RESOLVED` `ENABLED` `PASSED` | `#157f3b` | `#f0faf3` | `#cdeed7` |
| **Rot** (blockiert/Aktion) | `BLOCKED` `ESCALATED` `FAILED` `DISABLED` | `#b42318` | `#fef3f2` | `#f7d4cf` |

**Regel:** Default ist **neutral grau**. Farbe nur, wenn der Zustand sie wirklich trägt. `OBSERVED` ist passiv-informativ → **neutral**, nicht amber. Visuelle Referenz: `KYDE Status System.html`.

### 6.7 Filter-Pills / Segmented Control
- **Pill (zählend, z. B. All 34 / Critical 1):** `radius 9999`, `padding 6px 13px`, 13px/500. Aktiv: `bg rgba(17,24,39,.03)`, `border 1px #e6e6e6`, `color #0a0a0a`. Inaktiv: transparent, `color #737373`. Count in Mono 12/600, eingefärbt nach Severity-`fg`.
- **Segmented (Open/In Review/Escalated/Closed/All):** zusammenhängende Pill-Gruppe; aktives Segment dunkel (`bg #0a0a0a`, `color #fff`) **oder** hell (`bg #f3f3f3`, `color #0a0a0a`) — eine Variante wählen und durchhalten. Rest transparent, `color #737373`.

### 6.8 Daten-Tabelle
- Header-Zeile: 13px, `color #737373`, Mehrgewicht 500, **keine** Füllfarbe, 1px-Border-Bottom `#e6e6e6`.
- Zeilen: `padding 13px 0` (bzw. 14–16px in dichten Tabellen), Trenner 1px `#e6e6e6` (erste Zeile ohne Top-Border). Hover `bg #f6f6f6`.
- **IDs / Zeitstempel / IP** in Mono (`#737373`/`#a3a3a3`). Namen in Sans 14 `#3d3d3d`.
- Severity-Spalte: Badge (§6.5). Status-Spalte: Text/Badge.
- **Action-Spalte:** Text-Links in Mono mit Pfeil („Show chain →", „Details →"), `color #737373`, Hover `#0a0a0a`. Optional Checkbox-Spalte links (shadcn `Checkbox`, `radius 4`, `border #e6e6e6`).

### 6.9 Inputs, Buttons, Selects
- **Input/Search:** `bg #fff`, `border 1px #e6e6e6`, `radius 6`, `height 40`, `padding 0 12`, Lupe-Icon `#a3a3a3`, Placeholder `#a3a3a3`. Focus → Ring (primary).
- **Button primär:** `bg #2563eb`, `color #fff`, `radius 6`, `padding 9–10px 16px`, Sans 13–14/600, Hover `#1d4ed8`.
- **Button sekundär/ghost:** transparent oder `bg #f3f3f3`, `color #0a0a0a`, 1px-Border `#e6e6e6`.
- **Button destruktiv:** `bg #b42318`/`#dc2626`, `color #fff`.
- **Select (Zeitraum „Last 30d"):** wie Input, Chevron rechts, Menü als Popover (dezenter Shadow).

### 6.10 Action-Chain-Step (Agent Chains)
Horizontale Karten („Bash → Bash → Chat …"), je Karte: 1px-Border `#e6e6e6`, `radius 6`, `padding 14–16`, oben grüner Check-Icon + Titel (Sans 14/600), darunter Modell/Detail in Mono `#737373`. Verbinder = schlichter Pfeil `→` (`#a3a3a3`) zwischen den Karten. Blockierte Steps: roter Status statt grünem Check.

### 6.11 Empty States
Zentriert, Sans 14 `#737373`, optionales Mono-Detail; keine Illustrationen nötig. Bei Tabellen: leere Zeile mit „(unresolved)"-Stil in `#a3a3a3`.

---

## 7. Screen-für-Screen-Spezifikation

Jeder Screen nutzt App-Shell (§6.1) + Seiten-Header (§6.2). Nur die Inhalts-Komposition unterscheidet sich.

### Fleet Status (Referenz, bereits gebaut)
Header → **Banner minimal** (§6.4) → **4 KPI-Karten** (Active Agents · Open Alerts · Blocked Chains 24h · Data Integrity=VERIFIED) → **2-Spalten-Charts** (links Liniendiagramm „Agent Activity (last 14 days)", rechts Top-5-Bar monochrom) → **Recent Activity**: H2 + Filter-Pills (All/Critical/High/Medium/Low) rechts + Tabelle/Liste (Zeit · Severity · Agent · Type · ID→).

### Threats & Alerts
- Vier **Summary-Karten** Critical/High/Medium/Low. **Wichtig:** statt voller Pastell-Fülle → neutrale Stat-Karten (§6.3) mit großer Mono-Zahl `#0a0a0a` und kleinem Severity-Punkt + Label in Severity-`fg`. (Die aktuelle voll eingefärbte Variante widerspricht Prinzip 5.)
- Filter-Zeile: Pill-Gruppe „All sources / Chat / MCP" + Segmented „Open / In Review / Escalated / Closed / All" (§6.7).
- **Tabelle** (§6.8): Checkbox · Alert ID (Mono) · Severity (Badge) · Type · Agent · Detected (Mono) · Status · Action (Show chain → / Details →).

### Agent Chains
- Liste anklickbarer Chain-Zeilen: Chain-ID (Mono) · Agent · Status-Badge (`OBSERVED` = **neutral**, §6.6) · rechts Zeitstempel (Mono). Ausgewählte Zeile leicht hervorgehoben (`bg #f3f3f3`).
- **Detail-Banner:** **kein** farbiger Flächen-Wash. Neutrale Karte (`bg #fff`, `border 1px #e6e6e6`); oben links der **neutrale** Status-Badge (`OBSERVED`/`MONITORING`), oben rechts ein **roter** Alert-Akzent-Badge **nur** für die tatsächliche Severity (z. B. „2 ALERTS RAISED"). Titel in `#0a0a0a` (17px/600), Meta-Zeile in Mono-Grau. Optional schmaler blauer Border-Left (3px) wenn der Zustand „aktiv beobachtet" betont werden soll. Voller farbiger Wash bleibt **echten kritischen** Incidents vorbehalten (BREACH-Banner, rot).
- **Stat-Karten-Reihe:** DLP Findings (exposed) · DLP Findings (flagged) · Blocked at step · Chain Duration · Data Integrity.
- **ACTION CHAIN:** horizontale Step-Karten (§6.10).

### Network Map
- **Stat-Karten:** Total Nodes · Network Segments · AI Providers · Models · Unknowns.
- **Sankey** nach §5 (Network Map): neutrale graue Links, höchstens eine Akzent-Spalte, Mono-Labels, Breadcrumb-Leiste „NETWORK SEGMENT → AGENT → KYDE GATEWAY → AI PROVIDER → MODEL" in `.eyebrow`.

### Agents
Tabelle (§6.8): Agent (Name + Mono-ID) · Host/Segment · Modell · letzte Aktivität (Mono) · Status-Badge · Action. Optional Filter-Pills oben.

### Hosts
- **Stat-Karten:** Total Hosts · Labeled (+ „0% named" Mono-Caption) · DNS Misses.
- Filter-Pills (All / Labeled / DNS / DNS miss / Unresolved) links, **Search-Input** rechts (§6.9).
- **Tabelle:** Host · IP (Mono) · Source · Last Seen (Mono, sortierbar ▼). Leere Hosts als „(unresolved)" in `#a3a3a3`.

### Usage & Cost
- **Stat-Karten:** Total Cost (EUR) + Wechselkurs-Caption (Mono) · Total Tokens · Prompt / Completion · Active Agents.
- **„Token Usage Over Time":** gestapelte Balken, **zwei Blautöne** statt Blau+Gelb → Prompt `#a6c1f6` (hell), Completion `#2563eb` (§5). Legende in Mono.
- **Drei Karten** „By Agent / By Model / By AI Provider": horizontale gestapelte Mini-Bars, gleiche Zwei-Ton-Logik.

### Users · MCP Servers · AI Providers
Tabellen-/Listen-Seiten (§6.8). Users: Name · Rolle-Badges (ADMIN/VIEWER-Stil) · letzter Login (Mono) · Action. AI Providers / MCP Servers: Karten- oder Tabellen-Liste mit Status-Badge (Connected/Direct) + Mono-Meta.

### Policies
Liste/Tabelle der Policies mit Toggle (shadcn `Switch`) oder Status-Badge (Enabled/Disabled), Beschreibung in `#737373`, Severity-Zuordnung als Badge.

### Labels · Pricing · Admin Actions · Settings
- **Labels:** Tag-Liste, Tags im DATA-INTEGRITY-Badge-Stil (neutral, Mono).
- **Pricing:** Tabellen mit Preis-Zellen in Mono, tabular-nums.
- **Admin Actions:** Audit-Log-Tabelle (Zeit Mono · Actor · Action · Target).
- **Settings:** shadcn-Form-Layout — Sektionen mit `.eyebrow`-Headern, Labels Sans 14, Inputs §6.9, `Switch`/`Select`; klare 1px-Sektionstrenner.

---

## 8. Do / Don't

**Do**
- 1px-Borders zur Trennung; flache, schattenlose Karten.
- Diagramme: monochrome Blau-Rampe; wichtigster Wert sättigstes Blau, folgende heller.
- Mono für Labels, IDs, Zahlen, Achsen, Zeitstempel.
- KPI-Zahlen neutral-schwarz; Severity nur in Badges/Alerts.
- `tabular-nums` überall bei Zahlen.

**Don't**
- ❌ Keine voll eingefärbten Pastell-Karten (rot/gelb/grün Fläche).
- ❌ Keine Regenbogen-/Mehrfarb-Diagramme; kein Gelb in Charts.
- ❌ Keine weichen, diffusen Schlagschatten auf Karten.
- ❌ Keine roten KPI-Zahlen, nur weil es „Alerts" sind.
- ❌ Keine linken Akzentbalken-Container, keine runden 12px+-Bubbles, kein Inter als Standard.
- ❌ Kein Gelb außer dem einen Marken-Funke (DATA INTEGRITY / Logo).

---

## 9. Copy-paste: globals.css + Tailwind + Fonts

### 9.1 Fonts (next/font Beispiel)
```ts
import { Geist, Geist_Mono } from "next/font/google";
export const sans = Geist({ subsets: ["latin"], variable: "--font-sans" });
export const mono = Geist_Mono({ subsets: ["latin"], variable: "--font-mono" });
// <html class={`${sans.variable} ${mono.variable}`}>
```
Ohne Next: `@fontsource/geist-sans` + `@fontsource/geist-mono`, oder Google Fonts:
`https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700;800&family=Geist+Mono:wght@400;500;600;700&display=swap`.

### 9.2 shadcn-Token-Override (`globals.css`, `:root`, Light)
> Werte als HEX zur Klarheit; falls eure shadcn-Version HSL/oklch erwartet, 1:1 umrechnen (Bedeutung bleibt).
```css
@layer base {
  :root {
    --radius: 0.375rem;            /* 6px */

    --background: #ffffff;
    --foreground: #0a0a0a;

    --card: #ffffff;
    --card-foreground: #0a0a0a;
    --popover: #ffffff;
    --popover-foreground: #0a0a0a;

    --primary: #2563eb;
    --primary-foreground: #ffffff;

    --secondary: #f3f3f3;
    --secondary-foreground: #0a0a0a;

    --muted: #f5f5f5;
    --muted-foreground: #737373;

    --accent: #f3f3f3;            /* aktive Nav / hover-aktiv */
    --accent-foreground: #0a0a0a;

    --destructive: #dc2626;
    --destructive-foreground: #ffffff;

    --border: #e6e6e6;
    --input: #e6e6e6;
    --ring: #2563eb;

    /* Sidebar (shadcn sidebar block) */
    --sidebar: #ffffff;
    --sidebar-foreground: #525252;
    --sidebar-primary: #0a0a0a;
    --sidebar-primary-foreground: #ffffff;
    --sidebar-accent: #f3f3f3;
    --sidebar-accent-foreground: #0a0a0a;
    --sidebar-border: #ededed;
    --sidebar-ring: #2563eb;

    /* Charts — monochrome Blau-Rampe + dunkle Linie */
    --chart-1: #2563eb;
    --chart-2: #5283ec;
    --chart-3: #7ba2f1;
    --chart-4: #a6c1f6;
    --chart-5: #cddffb;
    --chart-line: #171717;
    --chart-grid: #f0f0f0;
    --chart-axis: #a3a3a3;
    --chart-marker: #dc2626;
    --chart-track: #f7f7f7;

    /* Custom Tokens (nicht in shadcn-Default) */
    --text-base: #3d3d3d;
    --text-faint: #a3a3a3;
    --brand-yellow: #ca8a04;
    --brand-green: #15803d;
    --brand-mist: #dae4e5;
    --brand-mist-foreground: #33403f;

    /* Status / State (eigene Farbachse — NICHT Severity!) */
    --status-neutral-fg: #525252; --status-neutral-bg: #f3f3f3; --status-neutral-border: #e6e6e6;
    --status-active-fg:  #2563eb; --status-active-bg:  #f0f4fd; --status-active-border:  #d7e2fb;
    --status-ok-fg:      #157f3b; --status-ok-bg:      #f0faf3; --status-ok-border:      #cdeed7;
    --status-bad-fg:     #b42318; --status-bad-bg:     #fef3f2; --status-bad-border:     #f7d4cf;

    --sev-critical-fg: #b42318; --sev-critical-bg: #fef3f2;
    --sev-high-fg:     #b54708; --sev-high-bg:     #fef6ee;
    --sev-medium-fg:   #854d0e; --sev-medium-bg:   #fefaeb;
    --sev-low-fg:      #157f3b; --sev-low-bg:      #f0faf3;
  }

  * { border-color: var(--border); }
  body {
    background: var(--background);
    color: var(--text-base);
    font-family: var(--font-sans), system-ui, sans-serif;
    line-height: 1.5;
    -webkit-font-smoothing: antialiased;
  }
  h1, h2, h3 { line-height: 1.1; }
  /* Karten flach halten — Schatten global neutralisieren */
  .card, [data-slot="card"] { box-shadow: none; }
}
```

### 9.3 Tailwind-Erweiterung (`tailwind.config.ts`)
```ts
export default {
  theme: {
    extend: {
      fontFamily: {
        sans: ["var(--font-sans)", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "monospace"],
      },
      colors: {
        // shadcn-Tokens werden über CSS-Variablen aufgelöst
        sev: {
          critical: "var(--sev-critical-fg)",
          high: "var(--sev-high-fg)",
          medium: "var(--sev-medium-fg)",
          low: "var(--sev-low-fg)",
        },
      },
      borderRadius: { lg: "var(--radius)", md: "calc(var(--radius) - 2px)", sm: "calc(var(--radius) - 3px)" },
      boxShadow: { popover: "0 8px 28px rgba(0,0,0,.10), 0 0 0 1px rgba(0,0,0,.05)" },
    },
  },
};
```

### 9.4 Wiederverwendbare Utility-Klassen
```css
@layer components {
  .eyebrow {
    @apply font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-muted-foreground;
  }
  .stat-value { @apply font-mono text-[34px] font-semibold tracking-[-0.01em] text-foreground tabular-nums; }
  .badge-sev {
    @apply inline-flex items-center font-mono text-[11px] font-semibold uppercase tracking-[0.06em]
           rounded-[5px] px-2 py-[3px] border;
  }
  /* z.B. Critical: style={{color:'var(--sev-critical-fg)', background:'var(--sev-critical-bg)',
     borderColor:'color-mix(in srgb, var(--sev-critical-fg) 13%, transparent)'}} */
}
```

---

### Referenz
Der visuelle Soll-Zustand ist die Variante **„C · Editorial Mono"** im Prototyp `KYDE Redesign.html` (5-Richtungen-Canvas). Bei Detailfragen ist der Prototyp die Quelle der Wahrheit; dieses Dokument hat bei Widersprüchen Vorrang für Tokens/Regeln.
