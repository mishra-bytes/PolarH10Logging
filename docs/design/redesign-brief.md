# Design brief: "PolarH10 Logging" desktop app (redesign)

## What the app is
A Windows desktop app for researchers. It connects to a **Polar H10** heart-rate chest strap over Bluetooth Low Energy, shows the signal live and records sessions to disk for later analysis. Users are research assistants running study sessions with participants, not engineers. They need to: connect fast, trust that recording is working, and never lose data. Everything stays on the PC (no network, no accounts).

Please design a **minimal, calm, compartmentalized** UI: clear panels/cards, one job per panel, lots of whitespace, no clutter. It should be usable by a first-time user without a manual, and glanceable from across the room during a session.

Target: Windows 11 desktop window, default 1280×800, must also work at 1024×700. Light theme first, dark theme as a variant. Font: Segoe UI (or Segoe UI Variable). It will be rebuilt in a native Python desktop toolkit, so prefer flat panels, simple controls and simple line charts over heavy effects, blur or complex animation.

## Data the app handles
- **Heart rate (HR)**, bpm, ~1 update/second. Always on.
- **RR intervals**, ms (time between beats), 0-3 per update. Always on.
- **ECG** (optional): raw voltage in µV, 130 samples/second.
- **Accelerometer** (optional): X/Y/Z in milli-g; rate 25/50/100/200 Hz; range ±2/±4/±8 g.
- Device info: device ID (e.g. `1C3E0231`), battery %, skin contact yes/no (contact is only reported while ECG or accelerometer is on), firmware.
- Live metrics over a selectable window (60 s, 2 min, 5 min, 10 min, Full session): Mean HR, Mean RR, SDNN, RMSSD, pNN50. Must be labeled "descriptive, not medical".
- Recording counters: elapsed time, packets, RR count, excluded RR (outside 300-2000 ms), disconnect count, file size so far.

## Connection states (design every one)
`Bluetooth off / unavailable` → `Idle (no device)` → `Scanning` → `Devices found` → `Connecting` → `Connected` → `Reconnecting (grace countdown)` → `Disconnecting`, plus `Connect failed` and `Lost (grace expired, log finalized)`.

## Screens / compartments

### 1. Connect (first run and whenever not connected)
- One big primary action: **Find my Polar H10**. Scanning shows a subtle progress indicator and lists found straps as cards: name `Polar H10 1C3E0231`, signal strength (bars), "last used" badge on the remembered device.
- Auto-select and offer one-click reconnect to the last used strap.
- Short pre-flight checklist beside it (3 lines, with icons): wet the electrodes and put the strap on · close phone apps that are connected to the strap · Bluetooth is on.
- **Graceful failure and retry:**
  - No devices found → friendly empty state, the checklist highlighted, **Scan again** button, auto-rescan toggle.
  - Connect failed → inline message with the likely cause, **Retry**, **Pick another device**. Show "Attempt 2 of 3" if auto-retrying.
  - Bluetooth off → a single clear message with a "Open Bluetooth settings" button.

### 2. Live dashboard (main screen when connected)
Compartments, each its own card:
- **Status strip** (top, thin): device name + ID, connection pill (green Connected / amber Reconnecting / red Lost), battery icon with %, contact indicator, **Disconnect** (secondary, not prominent).
- **Heart-rate hero card**: very large HR number with "bpm", last RR in ms beneath, small heart pulse animation on each beat (optional, subtle).
- **Charts card** with tabs or segmented control: `HR & RR` (last 2-5 minutes, two stacked line charts), `ECG` (scrolling ~5 s trace), `Motion` (X/Y/Z three colored lines with legend). Tabs for disabled streams show an empty state with an "Enable ECG" / "Enable motion" button.
- **Metrics card**: window selector (segmented: 60 s · 2 min · 5 min · 10 min · Full) and five compact stat tiles (Mean HR, Mean RR, SDNN, RMSSD, pNN50) with units. Small muted "descriptive, not medical" caption.
- **Streams card**: toggles for ECG (130 Hz) and Accelerometer with rate and range selectors. Can be changed any time, including while recording. Show an estimated data rate next to each (ECG ≈ 35 MB/h; Accel ≈ 15 MB/h at 50 Hz, 60 MB/h at 200 Hz).
- **Recording card** (see 3).
- **Event feed**: small collapsible list of recent events with timestamps and icons (start, stop, disconnect, reconnect, battery, stream on/off, warning, error).

### 3. Recording
- Idle state: fields **Participant ID** (required, with hint "use a research code, not a name"), **Condition** (optional), **Notes** (optional, multiline), then one prominent **Start recording** button. The live view works without recording ("Nothing is being saved" hint in muted text).
- Recording state: the card turns into a clear REC state: red dot + "REC 00:12:34", participant ID, counters (packets, RR, excluded RR, disconnects), live file size, **Stop recording** (requires confirm), **Open session folder**. A thin red accent on the window edge or title area so recording is obvious from across the room.
- After stop: "Saved" confirmation with the folder path, the files written, **Open folder**, **Start another recording** (same connection).

### 4. Reconnect / grace behaviour (important)
If the strap drops out during a recording, the app keeps the same recording open and retries for a **grace period** (default 120 s, configurable 0 s to 24 h).
- Show a non-blocking amber banner across the dashboard: "Connection lost — reconnecting… 1:42 left" with a countdown ring, attempt counter, and buttons **Retry now**, **Extend +2 min**, **Stop and save now**.
- Charts show a visible gap for the outage; HR hero shows "--" greyed with "Last seen 00:18 ago".
- On success: green toast "Reconnected — recording continues (segment 2)".
- On expiry: red banner "Could not reconnect. Recording was saved." with **Open folder** and **Reconnect** buttons.
- Also show crash recovery: on next launch after a PC crash, a dialog "A recording was interrupted on <date>. Its summary was rebuilt." with **Open folder** / **Dismiss**.

### 5. Settings (drawer or separate screen, not cluttering the dashboard)
- **Output folder** (path + Browse), default `Documents\PolarH10Logging`.
- **Reconnect grace** (stepper in seconds/minutes) and **auto-retry scan** toggle.
- **Output formats** (see below).
- Theme (light/dark/system), About (version, license, "not affiliated with Polar Electro").

## Output formats (new feature, design the selector)
In Settings and summarized in the Recording card ("Saving as: CSV + Parquet"). Multi-select checkboxes, at least one required:
1. **CSV** (default on): human-readable, opens in Excel. One file per stream (HR, ECG, ACC) + summary.
2. **JSON Lines** (`.jsonl`): one JSON object per record, easy for scripts and web tools.
3. **Parquet** (`.parquet`, recommended for long / high-rate recordings): compressed, typed, columnar format for numerical time series. Roughly 5-10× smaller than CSV, fast to load in Python/R/MATLAB. Option: **Encrypt with a password** (AES-256). When on, show a password + confirm field, a strength hint, and a clear warning: "If you lose this password the data cannot be recovered."

Show, next to each option, a one-line description and an estimated size per hour for the currently enabled streams. Always-written support files (`session.json` metadata, `raw` lossless backup, `app.log`) are listed as greyed "always included" so users understand what is in a session folder.

## Visual direction
- Minimal, clinical-but-friendly: white/very light grey background, cards with 8-12 px radius, subtle 1 px borders instead of heavy shadows.
- Signal colors: HR red `#d62839`, RR blue `#1f6feb`, ECG green `#0b7a3e`, accel X red / Y green / Z blue. Status: ok green `#2e7d32`, warning amber `#f5b700`, error red `#c62828`, muted text `#6b6b6b`.
- One primary accent button per screen. Destructive actions (Stop, Disconnect) are secondary and confirmed.
- Numbers use tabular figures so they don't jitter while updating.
- Keyboard accessible, WCAG AA contrast, don't rely on color alone for status (use icons + text).

## Deliverables
1. Connect screen: scanning, devices found, no devices found, connect failed, Bluetooth off.
2. Live dashboard: connected not recording; recording; reconnecting with grace countdown; grace expired.
3. Recording card: idle form, active, saved.
4. Settings with the output format selector (including Parquet encryption on).
5. Crash-recovery dialog.
6. Dark theme version of the recording dashboard.
7. A small component sheet: status pill, stat tile, stream toggle row, banner (info/warning/error), buttons, event row.
