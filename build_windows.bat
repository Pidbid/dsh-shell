@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

cd /d "%~dp0"

echo.
echo ========================================
echo   dsh-shell - Windows onefile build
echo ========================================
echo.

if not exist "assets\icon.ico" (
    echo Missing assets\icon.ico; the icon set is committed to the repository.
    exit /b 1
)

if not exist "dist" mkdir dist

for /f "usebackq delims=" %%v in (`uv run python -c "from dsh_shell.version import __version__; print(__version__)"`) do (
    if not defined APP_VERSION set "APP_VERSION=%%v"
)
if not defined APP_VERSION (
    echo Could not read the application version from dsh_shell/version.py
    exit /b 1
)

echo Building dsh-shell %APP_VERSION% with Nuitka (onefile)...
uv run nuitka ^
  --mingw64 ^
  --onefile ^
  --remove-output ^
  --windows-console-mode=disable ^
  --enable-plugin=pyside6 ^
  --include-package=dsh_shell ^
  --include-package=loguru ^
  --include-package=ruamel.yaml ^
  --include-package=bottle ^
  --include-package=proxy_tools ^
  --include-package=pythonnet ^
  --include-package-data=pythonnet ^
  --include-package=clr_loader ^
  --include-package-data=clr_loader ^
  --include-data-dir=ui=ui ^
  --include-data-dir=assets=assets ^
  --windows-icon-from-ico=assets\icon.ico ^
  --windows-company-name="dsh-shell" ^
  --windows-product-name="dsh-shell" ^
  --windows-file-version=%APP_VERSION% ^
  --windows-product-version=%APP_VERSION% ^
  --windows-file-description="DeepSeek Harness desktop shell" ^
  --output-dir=dist ^
  --output-filename=dsh-shell.exe ^
  --assume-yes-for-downloads ^
  --show-progress ^
  main.py

if errorlevel 1 (
    echo.
    echo Build failed.
    exit /b 1
)

if not exist "dist\dsh-shell.exe" (
    echo Nuitka did not produce dist\dsh-shell.exe
    exit /b 1
)

echo.
echo Build complete: dist\dsh-shell.exe
echo.
echo Smoke test:
dist\dsh-shell.exe --version
exit /b %errorlevel%
