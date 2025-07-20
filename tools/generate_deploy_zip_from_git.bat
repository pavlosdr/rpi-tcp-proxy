@echo off
powershell -ExecutionPolicy Bypass -File "%~dp0generate_deploy_zip_from_git.ps1"
pause
