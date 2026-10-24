# 把 Wiki Transporter 打包成两个单文件 exe（需在 Windows 上执行）
# 用法：pwsh -File scripts\build_exe.ps1
#       pwsh -File scripts\build_exe.ps1 -WithPlans
#
# 产物：
#   dist\wiki-transporter.exe       命令行版（带控制台；双击不带参数也开界面）
#   dist\WikiTransporter-GUI.exe    图形版（无控制台；崩溃栈写 exe 同目录 gui-error.log）
#
# 默认不把 plans/*.json 打进 exe：发出去的是纯工具，书库一开始是空的。
# 加 -WithPlans 才内嵌 plans/ 与 wiki_transporter/builtin_plans/。
param(
  [switch]$WithPlans
)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)

$py = 'py'
if (-not (Get-Command py -ErrorAction SilentlyContinue)) { $py = 'python' }

# 两个 spec 都读这个环境变量决定要不要 plans
if ($WithPlans) {
  $env:WT_WITH_PLANS = '1'
} else {
  Remove-Item Env:WT_WITH_PLANS -ErrorAction SilentlyContinue
}

Write-Host '== 安装打包依赖 ==' -ForegroundColor Cyan
& $py -m pip install --upgrade python-docx pyinstaller

$specs = @(
  @{ Spec = 'wiki-transporter.spec'; Out = 'dist\wiki-transporter.exe' },
  @{ Spec = 'WikiTransporter-GUI.spec'; Out = 'dist\WikiTransporter-GUI.exe' }
)

foreach ($s in $specs) {
  Write-Host ('== 打包 ' + $s.Out + ' ==') -ForegroundColor Cyan
  & $py -m PyInstaller --noconfirm --clean $s.Spec
  if ($LASTEXITCODE -ne 0) {
    Write-Host ('PyInstaller 失败（退出码 ' + $LASTEXITCODE + '）。' + $s.Out + ' 正被占用时会这样，先关掉正在运行的 exe 再重来。') -ForegroundColor Red
    exit $LASTEXITCODE
  }
  if (-not (Test-Path $s.Out)) {
    Write-Host ('打包失败：没有生成 ' + $s.Out) -ForegroundColor Red
    exit 1
  }
}

Write-Host '== 完成 ==' -ForegroundColor Green
foreach ($s in $specs) {
  Write-Host ('  {0}  ({1:N1} MB)' -f $s.Out, ((Get-Item $s.Out).Length / 1MB))
}
Write-Host ''
Write-Host '两个 exe 功能相同，区别只是有没有控制台窗口：'
Write-Host '  WikiTransporter-GUI.exe  双击开界面，不弹黑框'
Write-Host '  wiki-transporter.exe     双击同样开界面；在终端里能跑子命令'
Write-Host ''
Write-Host '命令行用法：'
Write-Host '  wiki-transporter.exe doctor        # 环境自检'
Write-Host '  wiki-transporter.exe all --plan X  # 抓取 + 生成 Word/EPUB/PDF'
Write-Host ''
if ($WithPlans) {
  Write-Host '已内嵌 plans\*.json 与 builtin_plans\*.json。'
} else {
  Write-Host '未内嵌任何计划文件：把 <id>.json 放到 exe 同级的 plans\ 目录即可出现在书架。'
}
