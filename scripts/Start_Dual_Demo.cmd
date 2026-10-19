@echo off
REM ============================================================================
REM  IR-OWC Lab Terminal - Dual Node Demo (no driver, no admin)
REM
REM  Starts two independent nodes that talk through the in-app TCP link broker:
REM    Node A (host)   : LINK_A, embeds the broker on 127.0.0.1:BASE / BASE+1
REM    Node B (client) : LINK_B, retries until the host is ready
REM
REM  Both nodes auto-connect and split the primary screen left/right.
REM  Data/logs stay isolated under data\instances\A and data\instances\B.
REM
REM  Usage:  Start_Dual_Demo.cmd [base_port]
REM ============================================================================
setlocal
set "APP=%~dp0IR_OWC_Lab_Terminal.exe"
if not exist "%APP%" set "APP=%~dp0IR_OWC_Lab_Terminal"
if not exist "%APP%" (
  echo [ERROR] IR_OWC_Lab_Terminal executable not found next to this script.
  pause
  exit /b 1
)

set "BASE=%~1"
if "%BASE%"=="" set "BASE=6001"

echo Starting IR-OWC dual node demo (link base port %BASE%) ...
start "IR-OWC Node A (Host)" "%APP%" --instance A --link-role host --link-base-port %BASE% --auto-connect --window-pos left
timeout /t 1 /nobreak >nul
start "IR-OWC Node B (Client)" "%APP%" --instance B --link-role join --link-base-port %BASE% --auto-connect --window-pos right
endlocal
