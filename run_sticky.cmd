@echo off
rem cb_sticky launcher: tray-resident cb monitor (pythonw, no console window)
rem Equivalent to the startup entry. To install startup:
rem   py -3 "%~dp0tools\install_hooks.py" --install-startup
rem NOTE: keep this file ASCII-only - cmd.exe reads .cmd as GBK on this system,
rem non-ASCII comments get mangled and spew errors.

setlocal
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

rem windowless python: pythonw on PATH first, then the py launcher
set "PYW="
for /f "delims=" %%i in ('where pythonw 2^>nul') do if not defined PYW set "PYW=%%i"
if defined PYW (
    start "" "%PYW%" "%ROOT%\tools\sticky_app.py"
) else (
    where pyw >nul 2>&1 && start "" pyw -3 "%ROOT%\tools\sticky_app.py" || echo no windowless python found ^(pythonw/pyw^) >&2
)
endlocal
