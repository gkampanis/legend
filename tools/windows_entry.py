"""Absolute-import entry point for PyInstaller."""
import os
import sys

if getattr(sys, 'frozen', False):
    os.environ['PLAYWRIGHT_BROWSERS_PATH'] = '0'

from myinvoices.__main__ import main

if __name__ == '__main__':
    raise SystemExit(main())
