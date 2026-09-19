# PyInstaller build: PolarH10Logging.exe (windowed) and PolarH10Logging-cli.exe (console).
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hidden = (collect_submodules("bleak") + collect_submodules("winrt")
          + collect_submodules("webview") + ["clr_loader", "pythonnet"])
# pywebview ships its bridge as .js files, which are data, not modules
web = ([("polarh10logging/web", "polarh10logging/web")]
       + collect_data_files("webview"))


def build(script, name, console):
    a = Analysis([script], pathex=["."], datas=web if not console else [],
                 hiddenimports=hidden, excludes=["pytest"])
    pyz = PYZ(a.pure)
    return EXE(pyz, a.scripts, a.binaries, a.datas, [], name=name, console=console,
               upx=False, version="packaging/version_info.txt")


build("packaging/gui_entry.py", "PolarH10Logging", console=False)
build("packaging/cli_entry.py", "PolarH10Logging-cli", console=True)
