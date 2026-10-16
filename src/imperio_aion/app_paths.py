# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Runtime assets stay with the app; all mutable user data stays in AppData."""
import os
from pathlib import Path


def data_directory():
    base = os.environ.get('LOCALAPPDATA') or os.environ.get('XDG_DATA_HOME') or str(Path.home()/'.local/share')
    return Path(base)/'ImperioAION'


def export_directory():
    return data_directory()/'exportacoes'


def diagnostics_directory():
    return data_directory()/'diagnosticos-automaticos'
