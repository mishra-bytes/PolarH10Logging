# PyInstaller build: PolarH10Logging.exe (windowed) and PolarH10Logging-cli.exe (console).
from PyInstaller.utils.hooks import collect_submodules

hidden = collect_submodules("bleak") + collect_submodules("winrt")


def build(script, name, console):
    a = Analysis([script], pathex=["."], hiddenimports=hidden, excludes=["pytest"])
    pyz = PYZ(a.pure)
    return EXE(pyz, a.scripts, a.binaries, a.datas, [], name=name, console=console,
               upx=False, version="packaging/version_info.txt")


build("packaging/gui_entry.py", "PolarH10Logging", console=False)
build("packaging/cli_entry.py", "PolarH10Logging-cli", console=True)
