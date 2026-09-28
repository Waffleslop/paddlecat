@echo off
REM Launch the WinKeyer CW Trainer.
REM
REM Right-click this file -> Send to -> Desktop (create shortcut) to put a
REM launcher icon on your desktop. The shortcut works from anywhere because
REM %~dp0 expands to the folder this .bat actually lives in -- so it always
REM finds winkeyer_app.py and friends.
REM
REM 'start ""' detaches and exits this script immediately; pythonw.exe runs
REM the GUI without a console window.

cd /d "%~dp0"
start "" pythonw.exe winkeyer_app.py
