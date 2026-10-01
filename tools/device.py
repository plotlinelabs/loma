"""Compatibility entry point: the device CLI lives in device_loader/cli/device.py.

Skills and prompts call `python3 tools/device.py ...` and code imports `tools.device`,
so both keep working: run as a script it runs the CLI, imported it IS that module.
"""
import importlib
import runpy
import sys
from pathlib import Path

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    runpy.run_module('device_loader.cli.device', run_name='__main__', alter_sys=True)
else:
    sys.modules[__name__] = importlib.import_module('device_loader.cli.device')
