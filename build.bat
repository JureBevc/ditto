@echo off
rem Builds Build\ditto.exe with the MSVC toolchain (VS 2019/2022 or Build Tools).
setlocal
cd /d "%~dp0"

where cl >nul 2>nul
if errorlevel 1 (
    set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
    for /f "usebackq tokens=*" %%i in (`"%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VSDIR=%%i"
)
if defined VSDIR set "PATH=%PATH%;%ProgramFiles(x86)%\Microsoft Visual Studio\Installer"
if defined VSDIR call "%VSDIR%\VC\Auxiliary\Build\vcvars64.bat" >nul
where cl >nul 2>nul
if errorlevel 1 (
    echo ERROR: MSVC compiler not found. Install Visual Studio Build Tools with the C++ workload.
    exit /b 1
)

if not exist Build mkdir Build
if not exist Build\obj mkdir Build\obj

rc /nologo /fo Build\obj\app.res res\app.rc
if errorlevel 1 exit /b 1

cl /nologo /std:c++17 /O2 /MT /W4 /EHsc /permissive- /utf-8 /MP /DUNICODE /D_UNICODE ^
   /wd4100 /wd4201 /wd4996 ^
   /Fo:Build\obj\ /Fe:Build\ditto.exe src\*.cpp Build\obj\app.res ^
   /link /SUBSYSTEM:WINDOWS /OPT:REF /OPT:ICF ^
   user32.lib gdi32.lib comctl32.lib comdlg32.lib shell32.lib ole32.lib uuid.lib ^
   dwmapi.lib winhttp.lib imm32.lib uxtheme.lib advapi32.lib shlwapi.lib
if errorlevel 1 exit /b 1

echo.
echo Built Build\ditto.exe
