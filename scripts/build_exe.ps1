# 把 Wiki Transporter 打包成单个 exe（需在 Windows 上执行）
# 用法：pwsh -File scripts\build_exe.ps1
#
# 默认不打进任何 plans/*.json：exe 是纯工具，双击就开界面，书架一开始是空的。
# 想连计划一起发，加 -WithPlans（会内嵌 plans/ 与 wiki_transporter/builtin_plans/）。
param(
  [switch]$WithPlans
)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)

$py = 'py'
if (-not (Get-Command py -ErrorAction SilentlyContinue)) { $py = 'python' }

Write-Host '== 安装打包依赖 ==' -ForegroundColor Cyan
& $py -m pip install --upgrade python-docx pyinstaller

$extra = @(
  '--add-data', 'wiki_transporter/gui/web;wiki_transporter/gui/web'
)
if ($WithPlans) {
  $extra += @(
    '--add-data', 'plans;plans',
    '--add-data', 'wiki_transporter/builtin_plans;wiki_transporter/builtin_plans'
  )
}

Write-Host ('== 生成 exe' + $(if ($WithPlans) { '（内嵌计划）' } else { '' }) + ' ==') -ForegroundColor Cyan
& $py -m PyInstaller --noconfirm --clean --onefile `
  --name wiki-transporter `
  @extra `
  --paths . `
  scripts/entry.py
if ($LASTEXITCODE -ne 0) {
  Write-Host ('PyInstaller 失败（退出码 ' + $LASTEXITCODE + '）。dist\wiki-transporter.exe 正被占用时会这样，先关掉正在运行的 exe 再重来。') -ForegroundColor Red
  exit $LASTEXITCODE
}

if (-not (Test-Path 'dist/wiki-transporter.exe')) {
  Write-Host '打包失败：没有生成 dist\wiki-transporter.exe' -ForegroundColor Red
  exit 1
}

Write-Host '== 完成 ==' -ForegroundColor Green
Write-Host ('  dist\wiki-transporter.exe  ({0:N1} MB)' -f ((Get-Item 'dist/wiki-transporter.exe').Length / 1MB))
Write-Host ''
Write-Host '双击 = 打开图形界面；命令行用法：'
Write-Host '  wiki-transporter.exe doctor        # 环境自检'
Write-Host '  wiki-transporter.exe all --plan X  # 抓取 + 生成 Word/EPUB/PDF'
Write-Host ''
if ($WithPlans) {
  Write-Host '已内嵌 plans\*.json 与 builtin_plans\*.json。'
} else {
  Write-Host '未内嵌任何计划文件：把 <id>.json 放到 exe 同级的 plans\ 目录即可出现在书架。'
}

