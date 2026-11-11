@echo off
cd /d "%~dp0"
echo Starting Book2Audio Studio (Python version)...
py -3.14 -m b2a.cli studio
pause
