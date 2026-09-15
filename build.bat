@echo off
echo Cleaning old build...
if exist build rmdir /s /q build

echo Compiling with Nuitka...
python -m nuitka --mode=standalone --windows-console-mode=disable --enable-plugin=pyqt6 --include-data-dir=assets=assets --windows-icon-from-ico=assets\wd2mmicon.ico --company-name="xaex1" --product-name="Pioneer Manager" --file-description="Pioneer Manager - Mod Manager for Watch Dogs 2" --file-version=1.0.0.0 --product-version=1.0.0.0 --output-dir=build --output-filename=Pioneer_Manager.exe main.py

echo Copying Gibbed tools...
xcopy /E /I /Y tools build\main.dist\tools

echo Zipping everything up for release...
powershell Compress-Archive -Path build\main.dist\* -DestinationPath build\Pioneer_Manager_Release.zip -Force

echo.
echo Build Complete! Your ready-to-upload ZIP is sitting in the build folder!
pause