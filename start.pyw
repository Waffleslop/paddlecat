"""Silent launcher for the WinKeyer CW Trainer.

Double-click this file to run the app -- no console window at all (the .pyw
extension makes Windows associate it with pythonw.exe). Right-click ->
'Send to' -> 'Desktop (create shortcut)' to put a launcher on your desktop.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)

from winkeyer_app import App
App().mainloop()
