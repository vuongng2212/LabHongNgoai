@echo off
setlocal
set "APP=%~dp0IR_OWC_Lab_Terminal.exe"
if not exist "%APP%" set "APP=%~dp0IR_OWC_Lab_Terminal"
if not exist "%APP%" (
  echo [ERROR] IR_OWC_Lab_Terminal executable not found next to this script.
  pause
  exit /b 1
)

set "DATA_A=%~1"
set "CTRL_A=%~2"
set "DATA_B=%~3"
set "CTRL_B=%~4"
if "%DATA_A%"=="" set "DATA_A=COM11"
if "%CTRL_A%"=="" set "CTRL_A=COM12"
if "%DATA_B%"=="" set "DATA_B=COM13"
if "%CTRL_B%"=="" set "CTRL_B=COM14"

echo Usage: Start_Com_Pair.cmd [DATA_A] [CTRL_A] [DATA_B] [CTRL_B]
echo Starting Dual COM hardware pair:
echo   Node A: COM1 Data=%DATA_A%  COM2 Control=%CTRL_A%
echo   Node B: COM1 Data=%DATA_B%  COM2 Control=%CTRL_B%

start "IR-OWC Node A (Data %DATA_A% / Ctrl %CTRL_A%)" "%APP%" --instance A --data-port %DATA_A% --ctrl-port %CTRL_A% --baud 19200 --auto-connect --window-pos left
timeout /t 1 /nobreak >nul
start "IR-OWC Node B (Data %DATA_B% / Ctrl %CTRL_B%)" "%APP%" --instance B --data-port %DATA_B% --ctrl-port %CTRL_B% --baud 19200 --auto-connect --window-pos right
endlocal
