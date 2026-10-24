# 把 dist\ 里的 exe 发到 GitHub Release（Windows / PowerShell 7）
#
# 用法：
#   $env:GITHUB_TOKEN = 'ghp_xxx'          # 需要 repo 权限；也可用 gh auth token
#   pwsh -File scripts\release.ps1 -Tag v1.6.0
#   pwsh -File scripts\release.ps1 -Tag v1.6.0 -DryRun        # 只算哈希、不调 API
#   pwsh -File scripts\release.ps1 -Tag v1.6.0 -Repo owner/name
#
# 它做四件事：
#   1. 校验 dist\ 里两个 exe 存在，算 SHA256；
#   2. 把哈希写回 README.md 的「校验和」段（自动更新，不用手抄）；
#   3. 用 docs\release-<tag>.md 作正文建/改 Release（已存在则更新，不重复建）；
#   4. 上传两个 exe 作附件（同名附件先删再传，避免 422）。
#
# 令牌只从环境变量 / -Token 读，不写进任何文件。
param(
  [Parameter(Mandatory = $true)][string]$Tag,
  [string]$Repo = 'HomewardBird/wiki-transporter',
  [string]$Token = $env:GITHUB_TOKEN,
  [string]$NotesFile = '',
  [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

$assets = @(
  @{ Path = 'dist\wiki-transporter.exe'; Name = 'wiki-transporter.exe' },
  @{ Path = 'dist\WikiTransporter-GUI.exe'; Name = 'WikiTransporter-GUI.exe' }
)

foreach ($a in $assets) {
  if (-not (Test-Path $a.Path)) {
    throw "找不到 $($a.Path)（先跑 pwsh -File scripts\build_exe.ps1）"
  }
}

Write-Host '== 校验和 ==' -ForegroundColor Cyan
$hashes = @{}
foreach ($a in $assets) {
  $h = (Get-FileHash -Path $a.Path -Algorithm SHA256).Hash.ToLower()
  $mb = [math]::Round((Get-Item $a.Path).Length / 1MB, 1)
  $hashes[$a.Name] = $h
  Write-Host ("  {0}  {1} MB  sha256 {2}" -f $a.Name, $mb, $h)
}

# README 的校验和段：<!-- checksum:begin --> ... <!-- checksum:end -->
$readmePath = Join-Path $root 'README.md'
$readme = Get-Content $readmePath -Raw
$begin = '<!-- checksum:begin -->'
$end = '<!-- checksum:end -->'
if ($readme.Contains($begin) -and $readme.Contains($end)) {
  $lines = @($begin, '两个 exe 的 SHA256（自己核对一下再双击，值不对就别运行）：', '')
  foreach ($a in $assets) {
    $lines += ('- `{0}` — `{1}`' -f $a.Name, $hashes[$a.Name])
  }
  $lines += @('', $end)
  $block = ($lines -join "`n")
  $readme = [regex]::Replace($readme, [regex]::Escape($begin) + '[\s\S]*?' + [regex]::Escape($end), $block)
  Set-Content -Path $readmePath -Value $readme -Encoding utf8 -NoNewline
  Write-Host '  已写回 README.md 的校验和段'
} else {
  Write-Host '  README.md 里没有 checksum 标记段，跳过写回' -ForegroundColor Yellow
}

if ($DryRun) {
  Write-Host '== DryRun：不调用 GitHub API ==' -ForegroundColor Yellow
  exit 0
}

if (-not $Token) {
  throw '缺少令牌：设 $env:GITHUB_TOKEN（或 -Token），需要 repo 权限'
}
if (-not $NotesFile) { $NotesFile = "docs\release-$Tag.md" }
if (-not (Test-Path $NotesFile)) { throw "找不到 Release 正文 $NotesFile" }
$body = Get-Content $NotesFile -Raw

# 正文里的 {{SHA256:文件名}} / {{SIZE:文件名}} 用真实值替换，别让说明里的哈希靠手抄
foreach ($a in $assets) {
  $mb = '{0:N1} MB' -f ((Get-Item $a.Path).Length / 1MB)
  $body = $body.Replace("{{SHA256:$($a.Name)}}", $hashes[$a.Name])
  $body = $body.Replace("{{SIZE:$($a.Name)}}", $mb)
}
if ($body -match '\{\{(SHA256|SIZE):') {
  Write-Host '  警告：正文里还有没替换的占位符' -ForegroundColor Yellow
}

$headers = @{
  Authorization          = "token $Token"
  'User-Agent'           = 'wiki-transporter-release'
  Accept                 = 'application/vnd.github+json'
  'X-GitHub-Api-Version' = '2022-11-28'
}
$api = "https://api.github.com/repos/$Repo"

Write-Host "== 同步 Release $Tag ==" -ForegroundColor Cyan
$existing = $null
try {
  $existing = Invoke-RestMethod -Uri "$api/releases/tags/$Tag" -Headers $headers -TimeoutSec 30
} catch {
  Write-Host '  远端还没有该 tag 的 Release，将新建'
}

if ($existing) {
  $payload = @{ tag_name = $Tag; name = $existing.name; body = $body; draft = $false; prerelease = $false } | ConvertTo-Json -Depth 4
  $release = Invoke-RestMethod -Method Patch -Uri "$api/releases/$($existing.id)" -Headers $headers -Body ([Text.Encoding]::UTF8.GetBytes($payload)) -ContentType 'application/json; charset=utf-8' -TimeoutSec 30
  Write-Host "  已更新 Release id=$($release.id)"
} else {
  $payload = @{ tag_name = $Tag; name = "wiki-transporter $Tag"; body = $body; draft = $false; prerelease = $false } | ConvertTo-Json -Depth 4
  $release = Invoke-RestMethod -Method Post -Uri "$api/releases" -Headers $headers -Body ([Text.Encoding]::UTF8.GetBytes($payload)) -ContentType 'application/json; charset=utf-8' -TimeoutSec 60
  Write-Host "  已新建 Release id=$($release.id)"
}

Write-Host '== 上传附件 ==' -ForegroundColor Cyan
foreach ($a in $assets) {
  # 同名附件先删，否则重传会 422 already_exists
  $old = @($release.assets | Where-Object { $_.name -eq $a.Name })
  foreach ($o in $old) {
    Invoke-RestMethod -Method Delete -Uri "$api/releases/assets/$($o.id)" -Headers $headers -TimeoutSec 30 | Out-Null
    Write-Host "  删掉旧附件 $($a.Name)"
  }
  $sizeMb = [math]::Round((Get-Item $a.Path).Length / 1MB, 1)
  Write-Host "  上传 $($a.Name)（$sizeMb MB，几十 MB 要等一会）"
  $encoded = [uri]::EscapeDataString($a.Name)
  $uploadUrl = "https://uploads.github.com/repos/$Repo/releases/$($release.id)/assets?name=$encoded"
  $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
  if ($curl) {
    & curl.exe -sS -X POST -H "Authorization: token $Token" -H 'Content-Type: application/octet-stream' --data-binary "@$($a.Path)" $uploadUrl | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "上传失败 ($($a.Name))：curl 退出码 $LASTEXITCODE" }
  } else {
    Invoke-RestMethod -Method Post -Uri $uploadUrl -Headers $headers -InFile $a.Path -ContentType 'application/octet-stream' -TimeoutSec 1800 | Out-Null
  }
  Write-Host "  完成 $($a.Name)"
}

$final = Invoke-RestMethod -Uri "$api/releases/tags/$Tag" -Headers $headers -TimeoutSec 30
Write-Host '== 线上现状 ==' -ForegroundColor Green
Write-Host "  $($final.html_url)"
foreach ($as in $final.assets) {
  Write-Host ('  {0}  {1} MB  下载数 {2}' -f $as.name, [math]::Round($as.size / 1MB, 1), $as.download_count)
}
