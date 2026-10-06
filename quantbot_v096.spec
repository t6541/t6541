# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ["desktop_entry.py"], pathex=["src"],
    binaries=[],
    datas=[("configs/default.toml", "configs"), ("configs/branding.toml", "configs"), ("configs/update-manifest.json", "configs"), ("configs/range-pivot.toml", "configs"), ("configs/strategy-03.toml", "configs")],
    hiddenimports=[], hookspath=[], runtime_hooks=[],
    excludes=["pytest", "yaml", "tkinter", "matplotlib", "pandas.tests", "numpy.tests", "openpyxl", "lxml", "PIL"], noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="CodexQuantBot-v0.7.398-Slots-Base-Switch",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=True,
    console=False, version="version_info.txt", uac_admin=True)



























