# 打两个测试用 exe（内嵌 sui 计划 + GUI 资源 + onnxruntime/tokenizers）
#   dist\wiki-transporter.exe       控制台版：双击也开界面，命令行可用
#   dist\WikiTransporter-GUI.exe    纯窗口版：双击直接开浏览器界面，不弹黑框
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)

$py = 'py'
if (-not (Get-Command py -ErrorAction SilentlyContinue)) { $py = 'python' }

$common = @(
  '--noconfirm', '--clean', '--onefile', '--paths', '.',
  '--add-data', 'wiki_transporter/gui/web;wiki_transporter/gui/web',
  '--add-data', 'wiki_transporter/builtin_plans;wiki_transporter/builtin_plans',
  '--add-data', 'plans;plans',
  '--collect-all', 'onnxruntime',
  '--collect-all', 'tokenizers'
)

Write-Host '== 1/2 控制台版 wiki-transporter.exe ==' -ForegroundColor Cyan
& $py -m PyInstaller @common --name wiki-transporter scripts/entry.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host '== 2/2 窗口版 WikiTransporter-GUI.exe ==' -ForegroundColor Cyan
& $py -m PyInstaller @common --windowed --name WikiTransporter-GUI scripts/entry_gui.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

foreach ($n in 'wiki-transporter', 'WikiTransporter-GUI') {
  $p = "dist/$n.exe"
  if (-not (Test-Path $p)) { Write-Host "缺少 $p" -ForegroundColor Red; exit 1 }
  Write-Host ('  {0}  {1:N1} MB' -f $p, ((Get-Item $p).Length / 1MB))
}
Write-Host '== 完成 ==' -ForegroundColor Green
