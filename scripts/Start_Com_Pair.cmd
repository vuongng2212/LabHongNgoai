@echo off
REM ============================================================================
REM  IR-OWC Lab Terminal - Virtual COM Pair (hardware acceptance path)
REM
REM  Requires a virtual COM pair created once by VSPD/VSPE (recommended) or
REM  com0com (lab only). Node A opens COMA, Node B opens COMB.
REM
REM  Usage:  Start_Com_Pair.cmd [COMA] [COMB]
REM  Default: COMA=COM11  COMB=COM12
REM ============================================================================
setlocal
set "APP=%~dp0IR_OWC_Lab_Terminal.exe"
if not exist "%APP%" set "APP=%~dp0IR_OWC_Lab_Terminal"
if not exist "%APP%" (
  echo [ERROR] IR_OWC_Lab_Terminal executable not found next to this script.
  pause
  exit /b 1
)

set "COMA=%~1"
set "COMB=%~2"
if "%COMA%"=="" set "COMA=COM11"
if "%COMB%"=="" set "COMB=COM12"

echo Starting IR-OWC COM pair demo: Node A=%COMA%  Node B=%COMB%
start "IR-OWC Node A (%COMA%)" "%APP%" --instance A --port %COMA% --auto-connect --window-pos left
timeout /t 1 /nobreak >nul
start "IR-OWC Node B (%COMB%)" "%APP%" --instance B --port %COMB% --auto-connect --window-pos right
endlocal
