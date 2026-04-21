"""
Put services/api/ on sys.path so tests can import the app modules
(`core`, `config`, `graphs`, `routers`) the same way main.py does.
"""

import pathlib
import sys

_API_DIR = pathlib.Path(__file__).resolve().parent.parent
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))
