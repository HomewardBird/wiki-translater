# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：图形版（无控制台窗口）。

用法：py -m PyInstaller --noconfirm --clean WikiTransporter-GUI.spec
产物：dist/WikiTransporter-GUI.exe

和命令行版的差别只有两处：入口换成 scripts/entry_gui.py（直接 run_gui，
不解析命令行参数），以及 console=False（双击不弹黑框）。没有控制台就看不到
报错，所以 entry_gui.py 会把崩溃栈写到 exe 同目录的 gui-error.log。

计划文件的处理与命令行版一致：默认不内嵌，WT_WITH_PLANS=1 才带上。
"""
import os

from PyInstaller.utils.hooks import collect_all

with_plans = os.environ.get('WT_WITH_PLANS') == '1'

datas = [('wiki_transporter/gui/web', 'wiki_transporter/gui/web')]
if with_plans:
    datas += [
        ('plans', 'plans'),
        ('wiki_transporter/builtin_plans', 'wiki_transporter/builtin_plans'),
    ]

binaries = []
hiddenimports = []
tmp_ret = collect_all('onnxruntime')
datas += tmp_ret[0]
binaries += tmp_ret[1]
hiddenimports += tmp_ret[2]
tmp_ret = collect_all('tokenizers')
datas += tmp_ret[0]
binaries += tmp_ret[1]
hiddenimports += tmp_ret[2]


a = Analysis(
    ['scripts/entry_gui.py'],
    pathex=['.'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='WikiTransporter-GUI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
