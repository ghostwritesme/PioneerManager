@echo off
setlocal EnableExtensions

set "MSVC_FLAG="
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if exist "%VSWHERE%" (
    for /f "usebackq delims=" %%I in (`"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath 2^>nul`) do set "VS_INSTALL=%%I"
    if defined VS_INSTALL set "MSVC_FLAG=--msvc=latest"
)

if not defined MSVC_FLAG (
    echo [INFO] Microsoft C++ Build Tools not detected. Compiling with portable x86_64 compiler...
    set "COMPILER_FLAGS=--target-arch=x86_64 --assume-yes-for-downloads"
) else (
    echo [INFO] Detected MSVC. Compiling with MSVC...
    set "COMPILER_FLAGS=%MSVC_FLAG%"
)

echo Cleaning old build...
if exist build rmdir /s /q build

echo Compiling with Nuitka...
python -m nuitka --mode=standalone %COMPILER_FLAGS% --windows-console-mode=disable --enable-plugin=pyqt6 --include-data-dir=assets=assets --windows-icon-from-ico=assets\wd2mmicon.ico --company-name="xaex1" --product-name="Pioneer Manager" --file-description="Pioneer Manager - Mod Manager for Watch Dogs 2" --file-version=1.0.1.0 --product-version=1.0.1.0 --output-dir=build --output-filename=Pioneer_Manager.exe main.py
if errorlevel 1 goto :build_failed

echo Copying Gibbed tools...
xcopy /E /I /Y tools build\main.dist\tools
if errorlevel 1 goto :build_failed

echo Zipping everything up for release...
powershell -NoProfile -Command "Compress-Archive -Path 'build\main.dist\*' -DestinationPath 'build\Pioneer_Manager_Release.zip' -Force"
if errorlevel 1 goto :build_failed

echo.
echo Build Complete! Your ready-to-upload ZIP is sitting in the build folder: build\Pioneer_Manager_Release.zip
pause
exit /b 0

:build_failed
echo.
echo ERROR: The release build failed.
pause
exit /b 1
