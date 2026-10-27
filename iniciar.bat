@echo off
REM Creado por Aldo Garcia.
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
if errorlevel 1 exit /b 1
title Matrix RH - iniciar
set "MATRIX_PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if exist "%SystemRoot%\Sysnative\WindowsPowerShell\v1.0\powershell.exe" set "MATRIX_PS=%SystemRoot%\Sysnative\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%MATRIX_PS%" (
    echo [FALLA] Se requiere Windows PowerShell 5.1 x64.
    pause
    exit /b 1
)
"%MATRIX_PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0backend\scripts\windows\MatrixRH.ps1" -Action iniciar %*
set "MATRIX_EXIT=%ERRORLEVEL%"
if not "%MATRIX_EXIT%"=="0" echo [FALLA] La operacion termino con codigo %MATRIX_EXIT%.
pause
exit /b %MATRIX_EXIT%
