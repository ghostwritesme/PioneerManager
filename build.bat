@echo off
setlocal EnableExtensions

rem Do not let Nuitka choose its Zig/Clang fallback.  The v1.0.0 release was
rem built that way and emitted instructions unsupported by some x64 CPUs.
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if not exist "%VSWHERE%" goto :missing_msvc

set "VS_INSTALL="
for /f "usebackq delims=" %%I in (`"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VS_INSTALL=%%I"
if not defined VS_INSTALL goto :missing_msvc

echo Cleaning old build...
if exist build rmdir /s /q build

echo Compiling with MSVC (generic x64 target)...
python -m nuitka --mode=standalone --msvc=latest --windows-console-mode=disable --enable-plugin=pyqt6 --include-data-dir=assets=assets --windows-icon-from-ico=assets\wd2mmicon.ico --company-name="xaex1" --product-name="Pioneer Manager" --file-description="Pioneer Manager - Mod Manager for Watch Dogs 2" --file-version=1.0.1.0 --product-version=1.0.1.0 --output-dir=build --output-filename=Pioneer_Manager.exe main.py
if errorlevel 1 goto :build_failed

echo Copying Gibbed tools...
xcopy /E /I /Y tools build\main.dist\tools
if errorlevel 1 goto :build_failed

echo Zipping everything up for release...
powershell -NoProfile -Command "Compress-Archive -Path 'build\main.dist\*' -DestinationPath 'build\Pioneer_Manager_Release.zip' -Force"
if errorlevel 1 goto :build_failed

echo.
echo Build Complete! Your ready-to-upload ZIP is sitting in the build folder!
pause
exit /b 0

:missing_msvc
echo.
echo ERROR: Microsoft C++ Build Tools (x64/x86) are required for release builds.
echo Install Visual Studio 2022 Build Tools with the "Desktop development with C++"
echo workload, then run this script again. The build intentionally will not
echo fall back to Zig/Clang because that produced an incompatible v1.0.0 EXE.
pause
exit /b 1

:build_failed
echo.
echo ERROR: The release build failed. No package should be uploaded.
pause
exit /b 1
