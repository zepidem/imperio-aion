# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Windows taskbar identity and supplied original number-2 icon."""
import os
from pathlib import Path
ICON=Path(__file__).resolve().parent/'assets/panel/imperioaion.ico'
APP_ID='Imperio.AION.Monitor'
def set_application_identity():
    if os.name=='nt':
        import ctypes
        function=ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        function.argtypes=[ctypes.c_wchar_p];function.restype=ctypes.c_long
        return function(APP_ID)
    return None
def apply_window_icon(window):
    if os.name=='nt' and ICON.is_file():
        window.iconbitmap(str(ICON))
        window.iconbitmap(default=str(ICON))
