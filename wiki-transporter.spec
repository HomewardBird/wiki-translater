# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：命令行版（带控制台）。

用法：py -m PyInstaller --noconfirm --clean wiki-transporter.spec
产物：dist/wiki-transporter.exe

默认不内嵌任何计划文件（plans/、builtin_plans/ 都不打包），所以发布出去的
exe 里没有任何剧情文本、也没有别人的书单；想连计划一起发，先设环境变量
WT_WITH_PLANS=1 再打包（scripts/build_exe.ps1 -WithPlans 会替你设）。

onnxruntime / tokenizers 是可选向量检索用的，必须连二进制一起收，否则
用户勾了「向量融合」才会在运行时报缺模块。
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
    ['scripts/entry.py'],
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
    name='wiki-transporter',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
