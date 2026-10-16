@echo off
rem Запись секрета на сервер. Из PowerShell или cmd: scripts\set_secret.cmd КЛЮЧ
rem .cmd не подпадает под политику выполнения PowerShell-скриптов, поэтому запускается без её изменения.
chcp 65001 >nul
setlocal
set "GITBASH="
if exist "%LOCALAPPDATA%\Programs\Git\bin\bash.exe" set "GITBASH=%LOCALAPPDATA%\Programs\Git\bin\bash.exe"
if not defined GITBASH if exist "%ProgramFiles%\Git\bin\bash.exe" set "GITBASH=%ProgramFiles%\Git\bin\bash.exe"
if not defined GITBASH (
  echo Git Bash не найден
  exit /b 1
)
"%GITBASH%" -l "%~dp0set_secret.sh" %1
exit /b %ERRORLEVEL%
