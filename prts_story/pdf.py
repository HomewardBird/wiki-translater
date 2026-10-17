# -*- coding: utf-8 -*-
"""PDF 引擎调度与「docx → PDF」这一路的老引擎。

默认引擎是同目录 `pdf_html.py`（Chromium 无头打印，不依赖 Office，目录页码与书签
齐全）。本模块保留另外两条退路：

* **Word COM**（Windows）：更新域和目录后导出，版式与 .docx 完全一致。
  **只读打开、只导出**——Word 在可写文档上保存时偶尔会弹出不可见的模态框，把 COM
  调用永久挂起；只读文档不会触发保存，导出渲染的仍是内存里的最新版面。需要把目录
  页码回存进 .docx 时用 `--write-back`（单独一轮、默认关闭）。
* **LibreOffice**（其它平台）：直接转换，目录页码需自行更新。

注意：Word 的导出路径在某些机器上会整条失效（实测连空白文档存 .rtf 都挂死，
重启/换打印机/停杀软均无效）。所以 `auto` 现在优先选 Chromium。
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import tempfile

from .config import ensure_dir

WORD_PS = r"""param([string]$ListFile, [string]$PdfDir, [string]$OutFile, [int]$WriteBack = 0)
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path $PdfDir | Out-Null
$files = Get-Content -LiteralPath $ListFile -Encoding UTF8
$lines = New-Object System.Collections.Generic.List[string]

# 逐步进度写到旁边一个文件里：即便进程被强杀，也能知道卡在哪一步。
$progress = $OutFile + '.progress'
$pEnc = New-Object System.Text.UTF8Encoding($false)
function P([string]$m) {
  [System.IO.File]::AppendAllText($progress, ("{0:HH:mm:ss} {1}`r`n" -f (Get-Date), $m), $pEnc)
}
[System.IO.File]::WriteAllText($progress, "", $pEnc)

# 清理残留的 Word 属主锁文件（~$xxx.docx）——上次异常退出留下的垃圾。
$seenDirs = @{}
foreach ($f in $files) {
  if ([string]::IsNullOrWhiteSpace($f)) { continue }
  $dir = [System.IO.Path]::GetDirectoryName($f)
  if ([string]::IsNullOrEmpty($dir)) { continue }
  if ($seenDirs.ContainsKey($dir)) { continue }
  $seenDirs[$dir] = $true
  $name = [System.IO.Path]::GetFileName($f)
  if ([string]::IsNullOrEmpty($name)) { continue }
  $owner = Join-Path $dir ("~`$" + $name)
  if (Test-Path -LiteralPath $owner) {
    try { Remove-Item -LiteralPath $owner -Force } catch { }
  }
}

# Word 异常退出后会把「肇事文档」记进 Resiliency\DisabledItems 黑名单，
# 之后再打开只报 COMException「命令失败」。只解封指向本工具输出目录的条目。
$resKey = 'HKCU:\Software\Microsoft\Office\16.0\Word\Resiliency\DisabledItems'
if (Test-Path $resKey) {
  $keyItem = Get-Item $resKey
  foreach ($vname in @($keyItem.GetValueNames())) {
    $raw = $keyItem.GetValue($vname)
    if ($raw -isnot [byte[]]) { continue }
    $text = [System.Text.Encoding]::Unicode.GetString($raw)
    foreach ($d in $seenDirs.Keys) {
      if ($text -like ('*' + $d + '*')) {
        Remove-ItemProperty -Path $resKey -Name $vname -ErrorAction SilentlyContinue
        break
      }
    }
  }
}

P "cleanup done"
$word = New-Object -ComObject Word.Application
P "word created"
$word.Visible = $false
$word.DisplayAlerts = 0
try {
  foreach ($f in $files) {
    if ([string]::IsNullOrWhiteSpace($f)) { continue }
    if (-not (Test-Path -LiteralPath $f)) {
      $lines.Add("SKIP`t" + $f + "`t0")
      continue
    }
    $name = [System.IO.Path]::GetFileNameWithoutExtension($f)
    Write-Output ("OPEN {0}" -f $name)
    P ("open " + $name)
    # 关键：一律只读打开。只读文档上 Word 绝不会为「保存」弹模态框，
    # 而导出用的是内存里的最新版面，结果与可写打开完全一致。
    $doc = $word.Documents.Open($f, $false, $true)
    try {
      Write-Output ("FIELDS {0}" -f $name)
      P ("opened -> fields")
      # Fields.Update() 会一并更新目录（TOC 本身就是域）。
      # 不要再单独 Update TablesOfContents：实测个别环境会卡在这条路径上。
      $doc.Fields.Update() | Out-Null
      P "fields updated"
      $doc.Repaginate()
      P "repaginated"
      $pdf = Join-Path $PdfDir ($name + '.pdf')
      Write-Output ("EXPORT {0}" -f $name)
      P "exporting"
      # 参数序：输出、格式(17=PDF)、后台导出、优化、范围、起、止、项、文档属性、保留授权、PDF书签(1=按标题)、结构标签、缺字体位图、PDF/A
      $doc.ExportAsFixedFormat($pdf, 17, $false, 0, 0, 0, 0, 0, $true, $true, 1, $true, $true, $false)
      P "exported"
      $pages = $doc.ComputeStatistics(2)
      $lines.Add("OK`t" + $name + "`t" + $pages)
      Write-Output ("OK {0} pages={1}" -f $name, $pages)
    } finally {
      $doc.Close($false)
      P "closed"
    }
  }
} finally {
  $word.Quit()
  P "quit"
  [System.Runtime.InteropServices.Marshal]::ReleaseComObject($word) | Out-Null
}

# 可选：把更新后的目录页码回存进 .docx（默认关闭，见 --write-back）
if ($WriteBack -eq 1) {
  $word2 = New-Object -ComObject Word.Application
  $word2.Visible = $false
  $word2.DisplayAlerts = 0
  try {
    foreach ($f in $files) {
      if ([string]::IsNullOrWhiteSpace($f)) { continue }
      if (-not (Test-Path -LiteralPath $f)) { continue }
      $name = [System.IO.Path]::GetFileNameWithoutExtension($f)
      $doc2 = $null
      try {
        $doc2 = $word2.Documents.Open($f, $false, $false)
        $doc2.Fields.Update() | Out-Null
        foreach ($toc in $doc2.TablesOfContents) { $toc.Update() | Out-Null }
        $doc2.Save()
        $lines.Add("SAVED`t" + $name + "`t0")
      } catch {
        $lines.Add("WARN`t" + $name + "`t回存失败：" + $_.Exception.Message)
      } finally {
        if ($null -ne $doc2) { $doc2.Close($false) }
      }
    }
  } finally {
    $word2.Quit()
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($word2) | Out-Null
  }
}

[System.IO.File]::WriteAllLines($OutFile, $lines, (New-Object System.Text.UTF8Encoding($false)))
"""


def _find_powershell() -> str | None:
    for exe in ("powershell.exe", "pwsh.exe", "powershell", "pwsh"):
        path = shutil.which(exe)
        if path:
            return path
    return None


def word_available() -> bool:
    return platform.system() == "Windows" and _find_powershell() is not None


def soffice_available() -> str | None:
    for exe in ("soffice", "libreoffice"):
        path = shutil.which(exe)
        if path:
            return path
    for cand in (
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    ):
        if os.path.isfile(cand):
            return cand
    return None


def export_via_word(docx_files: list[str], pdf_dir: str, write_back: bool = False) -> dict:
    """用 Word 更新目录并导出 PDF，返回 {文件名: 页数}。"""
    ensure_dir(pdf_dir)
    ps = _find_powershell()
    if not ps:
        raise RuntimeError("未找到 PowerShell，无法调用 Word")
    tmp = tempfile.mkdtemp(prefix="wiki-translater-pdf-")
    list_file = os.path.join(tmp, "files.txt")
    out_file = os.path.join(tmp, "result.txt")
    script_file = os.path.join(tmp, "export.ps1")
    with open(list_file, "w", encoding="utf-8") as f:
        f.write("\n".join(docx_files))
    with open(script_file, "w", encoding="utf-8-sig") as f:
        f.write(WORD_PS)

    cmd = [ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script_file,
           "-ListFile", list_file, "-PdfDir", pdf_dir, "-OutFile", out_file,
           "-WriteBack", "1" if write_back else "0"]
    # 硬超时：Word 偶尔会卡在某个隐藏对话框上，不设超时就会一直挂着
    timeout = max(180, 90 * len(docx_files))
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired as e:
        prog = ""
        pfile = out_file + ".progress"
        if os.path.isfile(pfile):
            with open(pfile, encoding="utf-8", errors="replace") as f:
                prog = f.read().strip()
        out = (e.stdout or "") if isinstance(e.stdout, str) else ""
        raise RuntimeError(
            "Word 导出超时（%d 秒）。\n卡在：\n%s\n最后输出：\n%s\n"
            "处理办法：在任务管理器结束 WINWORD.EXE 后重试。"
            % (timeout, prog or "（无进度记录）", out[-800:] or "（无）")) from e
    if proc.returncode != 0:
        raise RuntimeError("Word 导出失败：\n%s\n%s" % (proc.stdout[-2000:], proc.stderr[-2000:]))

    pages = {}
    if os.path.isfile(out_file):
        with open(out_file, encoding="utf-8") as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= 3 and parts[0] == "OK":
                    pages[parts[1]] = int(parts[2] or 0)
                elif len(parts) >= 3 and parts[0] == "WARN":
                    print("  !! %s：%s" % (parts[1], parts[2]))
    return pages


def export_via_soffice(docx_files: list[str], pdf_dir: str) -> dict:
    """Linux / macOS 的退路：LibreOffice 转 PDF（目录需在 Word/WPS 里自行更新）。"""
    soffice = soffice_available()
    if not soffice:
        raise RuntimeError(
            "没有可用的 PDF 引擎：Windows 请安装 Microsoft Word；"
            "其它平台请安装 LibreOffice（soffice）。"
        )
    ensure_dir(pdf_dir)
    for f in docx_files:
        subprocess.run([soffice, "--headless", "--convert-to", "pdf",
                        "--outdir", pdf_dir, f], check=True,
                       capture_output=True, text=True)
    return {}


def export_pdfs(docx_files: list[str], pdf_dir: str, write_back: bool = False) -> dict:
    """自动选择引擎导出 PDF。"""
    docx_files = [os.path.abspath(f) for f in docx_files if os.path.isfile(f)]
    if not docx_files:
        return {}
    if word_available():
        try:
            return export_via_word(docx_files, pdf_dir, write_back=write_back)
        except Exception as e:  # Word 不可用（未安装 / COM 被禁）时退回 LibreOffice
            print("  !! Word 导出失败，尝试 LibreOffice：%s" % e)
    if soffice_available():
        print("  使用 LibreOffice 转换（目录页码需在 Word/WPS 中更新后再导出）")
        return export_via_soffice(docx_files, pdf_dir)
    raise RuntimeError("找不到 PDF 引擎：请安装 Microsoft Word 或 LibreOffice。")


def doctor() -> dict:
    from .pdf_html import find_chromium

    chrome = find_chromium()
    info = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "chromium": chrome or False,
        "word": word_available(),
        "libreoffice": soffice_available(),
    }
    try:
        import docx  # noqa: F401
        info["python-docx"] = True
    except Exception:
        info["python-docx"] = False
    try:
        import pymupdf  # noqa: F401
        info["pymupdf"] = True
    except Exception:
        info["pymupdf"] = False
    return info
