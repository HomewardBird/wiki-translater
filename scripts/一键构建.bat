@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0.."

set "PY=py"
where py >nul 2>nul || set "PY=python"

echo ============================================
echo   Wiki Transporter  一键构建
echo   工作目录: %CD%
echo ============================================
echo.

%PY% -m pip install -r requirements.txt --quiet
if errorlevel 1 (
  echo [错误] 依赖安装失败，请确认已安装 Python 3.9+ 并加入 PATH。
  pause
  exit /b 1
)

%PY% -m wiki_transporter all %*
echo.
echo 完成。产物在 out\ 目录（docx / pdf / epub 三套）。
pause
