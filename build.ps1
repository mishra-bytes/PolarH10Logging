# Build PolarH10Logging.exe and PolarH10Logging-cli.exe into dist\.
# Usage: .\build.ps1   (creates .venv on first run)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path .venv)) { py -3.12 -m venv .venv }
.\.venv\Scripts\python -m pip install -q -e ".[dev]"
.\.venv\Scripts\python -m pytest -q --cov --cov-fail-under=85
if ($LASTEXITCODE -ne 0) { throw "tests failed" }
.\.venv\Scripts\pyinstaller --noconfirm --clean PolarH10Logging.spec
if ($LASTEXITCODE -ne 0) { throw "pyinstaller failed" }
Get-ChildItem dist\*.exe | Format-Table Name, Length
