# 把岁家剧情的成品打成一个发布用压缩包（exe 里不再内嵌剧情，成品单独发）
# 用法：pwsh -File scripts\pack_release.ps1
#       pwsh -File scripts\pack_release.ps1 -Src 'dist\out' -Out 'release\sui-story.zip'
param(
  [string]$Id = 'sui',
  [string]$Src = 'out',
  [string]$Plan = 'plans\sui.json',
  [string]$Out = 'release\sui-story.zip'
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

$bookDir = Join-Path (Resolve-Path $Src) $Id
if (-not (Test-Path $bookDir)) { throw "找不到成品目录：$bookDir（先跑 py -m prts_story all --plan $Id）" }
if (-not (Test-Path $Plan)) { throw "找不到计划文件：$Plan" }

$name = '岁家剧情'
$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ('rel_' + [guid]::NewGuid().ToString('N').Substring(0, 8))
$pkg = Join-Path $tmp $name
New-Item -ItemType Directory -Force -Path $pkg | Out-Null

Write-Host "== 复制成品 $bookDir ==" -ForegroundColor Cyan
Copy-Item $bookDir (Join-Path $pkg $Id) -Recurse -Force
Copy-Item $Plan (Join-Path $pkg "$Id.json") -Force

$pdfs = @(Get-ChildItem (Join-Path $bookDir 'pdf') -Filter *.pdf -ErrorAction SilentlyContinue)
$pages = '合订本 PDF 1208 页'
@(
  "$name — 明日方舟剧情文字稿（Wiki Translater 排版）",
  "",
  "sui\        已经排好的成品：pdf\ docx\ epub\ 三个子目录，10 卷 + 全一册",
  "$Id.json     这本书的计划文件",
  "",
  "只想读：直接打开 sui\pdf\ 或 sui\epub\ 里的文件，不需要 exe。",
  "想重新生成：把 $Id.json 放到 wiki-translater.exe 同级的 plans\ 目录（没有就新建），",
  "            重启 exe，书库里会出现这本书，「整理」页点「整套整理」即可重新印。",
  "",
  "整理与排版：安巢鸟。剧情文本著作权归其权利方所有，wiki 文字来自对应 Wiki 的贡献者；",
  "权利归属与使用条件逐站写在仓库 NOTICE.md 里，仅供个人阅读存档，请勿商用或付费分发。"
) | Set-Content -Path (Join-Path $pkg '说明.txt') -Encoding utf8

New-Item -ItemType Directory -Force -Path (Split-Path $Out -Parent) | Out-Null
if (Test-Path $Out) { Remove-Item $Out -Force }
Write-Host "== 压缩到 $Out ==" -ForegroundColor Cyan
Compress-Archive -Path $pkg -DestinationPath $Out -CompressionLevel Optimal

Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
$zip = Get-Item $Out
$count = @(Get-ChildItem $bookDir -Recurse -File).Count
Write-Host ('== 完成 ==  {0}  ({1:N1} MB)  含 {2} 个成品文件，{3} 个 PDF 分卷' -f `
  $zip.FullName, ($zip.Length / 1MB), $count, $pdfs.Count) -ForegroundColor Green
