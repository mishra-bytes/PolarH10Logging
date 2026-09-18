# PolarH10 Logging

Windows app that connects to a **Polar H10** chest strap over Bluetooth, shows heart rate and
RR intervals live, and logs every beat to CSV with UTC timestamps. HR/RR only (no ECG).

## Use (team members)

1. Copy `PolarH10Logging.exe` anywhere and double-click it. No install or Python needed.
   Windows SmartScreen may warn because the file is unsigned: **More info → Run anyway**.
2. Wet the strap electrodes and put the strap on. Close phone apps connected to it.
3. **Scan**, pick `Polar H10 <ID>`, **Connect**. Live HR, RR and charts appear.
   Nothing is saved yet.
4. To save, enter a **Participant ID** (a code, not a name) and click **Start logging**.
   Every packet is written to disk immediately.
5. **Stop logging** finalizes the files. **Open session folder** shows them. You can start
   another log on the same connection. **Disconnect** when done.

If the strap drops out, the app keeps trying to reconnect for the **Reconnect grace**
period (default 120 s) and continues the same log; after that the log is finalized.
If the PC crashes, the next start marks the session as interrupted and rebuilds its summary.

Files: see [docs/data-format.md](docs/data-format.md). Default folder:
`Documents\PolarH10Logging`.

## Command line

```text
PolarH10Logging-cli.exe --scan
PolarH10Logging-cli.exe --record --participant P07 [--device 1C3E0231] [--out DIR]
                        [--condition TEXT] [--notes TEXT] [--duration MIN] [--grace SEC]
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
.\.venv\Scripts\python -m polarh10logging --fake   # GUI with a simulated strap
.\build.ps1                                        # tests + dist\*.exe
```

## Privacy

HR/RR data can be personal health information. Everything stays on the PC; the app makes no
network connections. Use coded participant IDs and keep session folders secure.

Polar and H10 are trademarks of Polar Electro Oy. This project is independent and not
affiliated with or endorsed by Polar Electro. MIT license.
