@echo off
REM ---------------------------------------------------------------------
REM Build WinKeyerCWTrainer.exe -- a single-file Windows app, no Python
REM install needed by whoever you give it to.
REM ---------------------------------------------------------------------
echo Installing build dependencies...
python -m pip install --upgrade pyinstaller pyserial customtkinter || goto :fail

echo.
echo Building WinKeyerCWTrainer.exe ...
python -m PyInstaller --onefile --windowed --noconfirm ^
    --name WinKeyerCWTrainer ^
    --collect-all customtkinter ^
    winkeyer_app.py || goto :fail

echo.
echo ======================================================================
echo  Done.  The app is at:  dist\WinKeyerCWTrainer.exe
echo  (winkeyer_vail.py, iambic.py, morse_decode.py, drills.py, qsos.py
echo   and invaders.py are bundled in automatically.)
echo ======================================================================
echo  Note: the .exe is unsigned, so the first run shows a Windows
echo  SmartScreen warning -- click "More info" then "Run anyway".
pause
exit /b 0

:fail
echo.
echo BUILD FAILED -- see the messages above.
pause
exit /b 1
