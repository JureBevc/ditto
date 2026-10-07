@echo off
rem Builds Build\buffer_test.exe (core buffer / search / markdown tests).
setlocal
cd /d "%~dp0.."
where cl >nul 2>nul
if errorlevel 1 (
    for /f "usebackq tokens=*" %%i in (`"%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe" -latest -products * -property installationPath`) do set "VSDIR=%%i"
)
if defined VSDIR set "PATH=%PATH%;%ProgramFiles(x86)%\Microsoft Visual Studio\Installer"
if defined VSDIR call "%VSDIR%\VC\Auxiliary\Build\vcvars64.bat" >nul
if not exist Build\testobj mkdir Build\testobj
cl /nologo /std:c++17 /O2 /MT /W4 /EHsc /permissive- /utf-8 /MP /DUNICODE /D_UNICODE /wd4100 /wd4201 /wd4996 ^
   /Fo:Build\testobj\ /Fe:Build\buffer_test.exe tests\buffer_test.cpp src\util.cpp src\text_buffer.cpp src\md_highlight.cpp ^
   src\editor_view.cpp src\editor_input.cpp ^
   /link /SUBSYSTEM:CONSOLE user32.lib gdi32.lib comctl32.lib shell32.lib ole32.lib imm32.lib uxtheme.lib advapi32.lib
