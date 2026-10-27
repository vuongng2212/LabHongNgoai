@echo off
setlocal
set "APP=%~dp0IR_OWC_Lab_Terminal.exe"
if not exist "%APP%" set "APP=%~dp0IR_OWC_Lab_Terminal"
if not exist "%APP%" (
  echo [ERROR] IR_OWC_Lab_Terminal executable not found next to this script.
  pause
  exit /b 1
)

set "TX_A=%~1"
set "RX_A=%~2"
set "TX_B=%~3"
set "RX_B=%~4"
if "%TX_A%"=="" set "TX_A=COM11"
if "%RX_A%"=="" set "RX_A=COM14"
if "%TX_B%"=="" set "TX_B=COM13"
if "%RX_B%"=="" set "RX_B=COM12"

echo Usage: Start_Com_Pair.cmd [TX_A] [RX_A] [TX_B] [RX_B]
echo Starting dual-port COM pair: Node A=TX %TX_A% / RX %RX_A%  Node B=TX %TX_B% / RX %RX_B%
start "IR-OWC Node A (TX %TX_A% / RX %RX_A%)" "%APP%" --instance A --tx-port %TX_A% --rx-port %RX_A% --baud 115200 --auto-connect --window-pos left
timeout /t 1 /nobreak >nul
start "IR-OWC Node B (TX %TX_B% / RX %RX_B%)" "%APP%" --instance B --tx-port %TX_B% --rx-port %RX_B% --baud 115200 --auto-connect --window-pos right
endlocal
