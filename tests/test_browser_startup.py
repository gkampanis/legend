import asyncio
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from myinvoices.browser_downloads import BrowserPDFDownloader, browser_launch_choices
from myinvoices.models import ApplicationError


@unittest.skipUnless(importlib.util.find_spec('playwright'),'Optional browser support is not installed')
class BrowserStartupTests(unittest.TestCase):
    def test_windows_discovers_brave_and_retains_bundled_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            brave=Path(temp)/'BraveSoftware/Brave-Browser/Application/brave.exe'
            brave.parent.mkdir(parents=True);brave.touch()
            with patch('myinvoices.browser_downloads.sys.platform','win32'), \
                    patch.dict('os.environ',{'PROGRAMFILES':temp,'PROGRAMFILES(X86)':temp,'LOCALAPPDATA':temp},clear=True):
                choices=browser_launch_choices()
            self.assertEqual(choices[:2],[{'channel':'msedge'},{'channel':'chrome'}])
            self.assertEqual(choices.count({'executable_path':str(brave)}),1)
            self.assertEqual(choices[-1],{})

    def test_launch_can_fall_back_to_installed_chromium(self):
        from playwright.async_api import Error
        browser=SimpleNamespace(new_context=AsyncMock(return_value=Mock()))
        launch=AsyncMock(side_effect=[Error('synthetic missing Edge'),Error('synthetic missing Chrome'),browser])
        playwright=SimpleNamespace(chromium=SimpleNamespace(launch=launch))
        starter=SimpleNamespace(start=AsyncMock(return_value=playwright))
        downloader=BrowserPDFDownloader(Mock(),headless=True)
        with patch('playwright.async_api.async_playwright',return_value=starter), \
                patch('myinvoices.browser_downloads.getproxies',return_value={}), \
                patch('myinvoices.browser_downloads.browser_launch_choices',return_value=[{'channel':'msedge'},{'channel':'chrome'},{}]):
            asyncio.run(downloader._start())
        self.assertIs(downloader.browser,browser)
        self.assertEqual(launch.await_count,3)
        self.assertEqual(launch.call_args.kwargs,{'headless':True})

    def test_failed_session_is_safe_and_closes_started_browser(self):
        from playwright.async_api import Error
        browser=SimpleNamespace(close=AsyncMock())
        downloader=BrowserPDFDownloader(Mock())
        async def failed():
            downloader.browser=browser
            raise Error('private token must not escape')
        with patch.object(downloader,'_start',side_effect=failed):
            with self.assertRaises(ApplicationError) as caught:
                with downloader.session():
                    self.fail('Must not enter failed browser session')
        self.assertNotIn('private',str(caught.exception))
        self.assertIn('INSTALL_BROWSER',str(caught.exception))
        browser.close.assert_awaited_once()
