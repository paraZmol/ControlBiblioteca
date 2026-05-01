@echo off
:: ============================================================
::  INSTALADOR SERVIDOR - Control Biblioteca UNASAM
::  Ejecutar como Administrador
:: ============================================================
title Instalador Servidor Biblioteca UNASAM
color 0A
cd /d "%~dp0"

echo.
echo  ===================================================
echo   INSTALADOR SERVIDOR - Biblioteca UNASAM
echo  ===================================================
echo.

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo  [ERROR] Ejecute como Administrador.
    echo  Clic derecho ^> "Ejecutar como administrador"
    pause & exit /b 1
)

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo  [ERROR] Python no encontrado.
    echo  Instale Python 3.11+ desde https://www.python.org/downloads/
    echo  Marque "Add Python to PATH" durante la instalacion.
    pause & exit /b 1
)
for /f "tokens=*" %%i in ('python --version') do echo  [OK] %%i

:: PASO 1: Entorno virtual
echo.
echo  [1/4] Creando entorno virtual...
if not exist ".venv" (
    python -m venv .venv
    echo  Creado.
) else (
    echo  Ya existe, se reutiliza.
)

:: PASO 2: Dependencias
echo.
echo  [2/4] Instalando dependencias...
.venv\Scripts\pip.exe install --upgrade pip --quiet
.venv\Scripts\pip.exe install -r requirements.txt
if %errorlevel% neq 0 (
    echo  [ERROR] Fallo al instalar dependencias.
    pause & exit /b 1
)
echo  OK.

:: PASO 3: Configurar desde config.json (genera .env y crea BD)
echo.
echo  [3/4] Ejecutando configurador...
echo  (Edite config.json antes de continuar si necesita cambiar la IP o contrasena)
echo.
.venv\Scripts\python.exe configurador.py
if %errorlevel% neq 0 (
    echo.
    echo  [ERROR] El configurador fallo. Revise el mensaje arriba.
    echo  Edite config.json con los datos correctos y vuelva a ejecutar.
    pause & exit /b 1
)

echo.
:: PASO 4: Firewall e inicio automatico
echo.
echo  [4/4] Configurando firewall e inicio automatico...

netsh advfirewall firewall show rule name="Biblioteca UNASAM 8000" >nul 2>&1
if %errorlevel% neq 0 (
    netsh advfirewall firewall add rule name="Biblioteca UNASAM 8000" dir=in action=allow protocol=TCP localport=8000 >nul
    echo  Puerto 8000 habilitado en firewall.
) else (
    echo  Regla de firewall ya existe.
)

set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "SERVER_DIR=%~dp0"
(
    echo Dim WshShell
    echo Set WshShell = CreateObject^("WScript.Shell"^)
    echo WshShell.Run "cmd /c ""%SERVER_DIR%servidor_run.bat""", 0, False
) > "%STARTUP%\biblioteca_unasam.vbs"
echo  Inicio automatico configurado.

echo.
echo  ===================================================
echo   INSTALACION COMPLETADA
echo  ===================================================
echo.
echo  Inicie el servidor: doble clic en servidor_run.bat
echo  O reinicie la PC para arranque automatico.
echo.
echo  Panel: http://localhost:8000/admin
echo  admin / admin123  ^|  Nivel 2: max123
echo.
pause
