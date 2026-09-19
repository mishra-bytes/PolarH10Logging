# PolarH10 Logging

Windows app that connects to a **Polar H10** chest strap over Bluetooth, shows heart rate and
RR intervals live, and logs every beat to CSV with UTC timestamps. Optionally it also streams
and logs **raw ECG** (130 Hz) and the **accelerometer** (25-200 Hz).

## Use (team members)

1. Copy `PolarH10Logging.exe` anywhere and double-click it. No install or Python needed.
   Windows SmartScreen may warn because the file is unsigned: **More info -> Run anyway**.
2. Wet the strap electrodes and put the strap on. Close phone apps connected to it.
3. **Find my Polar H10**, then **Connect** on your strap. The last strap you used is
   remembered and preselected, so the next session is one click. Live heart rate, RR and
   charts appear. Nothing is saved yet.
   Turn on **ECG** and/or **Accelerometer** in the *Streams* card (rate and range are next to
   it) to stream them; the **ECG** and **Motion** chart tabs show them live. You can change
   these at any time, also while recording.
4. To save, enter a **Participant ID** (a code, not a name) and click **Start recording**.
   Every packet is written to disk immediately.
5. **Stop recording** asks for confirmation, then finalizes the files and shows where they
   are. You can start another recording on the same connection. **Disconnect** when done.

If the strap drops out, the app keeps the recording open and retries for the **grace period**
(Settings, default 2 minutes). A banner counts the time down and offers **Retry now**,
**Extend +2 min** and **Stop and save now**. After the grace period the recording is
finalized. If the PC crashes, the next start marks the session as interrupted, rebuilds its
summary and says so.

Settings holds the output folder, the grace period, the theme (system, light or dark) and the
output formats. CSV is written today; JSON Lines and Parquet are marked *coming next*.

Files: see [docs/data-format.md](docs/data-format.md). Default folder:
`Documents\PolarH10Logging`.

## Command line

```text
PolarH10Logging-cli.exe --scan
PolarH10Logging-cli.exe --record --participant P07 [--device 1C3E0231] [--out DIR]
                        [--condition TEXT] [--notes TEXT] [--duration MIN] [--grace SEC]
                        [--ecg] [--acc] [--acc-rate 25|50|100|200] [--acc-range 2|4|8]
PolarH10Logging-cli.exe --fake --record --participant P07 [--replay raw.jsonl]
PolarH10Logging-cli.exe --recover SESSION_FOLDER
```

Exit codes: 0 ok, 2 Bluetooth unavailable, 3 no device / connect failed, 4 invalid input or
output folder, 5 storage failure, 6 recovery failure.

## Develop

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m pytest
.\.venv\Scripts\python -m polarh10logging --fake   # app window with a simulated strap
.\build.ps1                                        # tests + dist\*.exe
```

The window is a WebView2 view (pywebview) over the Python session controller: the page in
`polarh10logging/web/` renders every screen and polls `Api.poll()` in `webui.py` a few times a
second. WebView2 ships with Windows 11 and recent Windows 10; on an older PC install the
Microsoft Edge WebView2 Runtime.

## Privacy

HR/RR data can be personal health information. Everything stays on the PC; the app makes no
network connections. Use coded participant IDs and keep session folders secure.

Polar and H10 are trademarks of Polar Electro Oy. This project is independent and not
affiliated with or endorsed by Polar Electro. MIT license.
