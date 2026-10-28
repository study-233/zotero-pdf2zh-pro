"""Reject visible application windows, excluding Winit's internal event target."""
import ctypes
from ctypes import wintypes
import json
import sys


def visible_windows(process_id):
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    windows = []

    @callback_type
    def visit(window, unused):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(window, ctypes.byref(owner))
        if owner.value == process_id and user32.IsWindowVisible(window):
            title = ctypes.create_unicode_buffer(1024)
            window_class = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(window, title, len(title))
            user32.GetClassNameW(window, window_class, len(window_class))
            # Winit creates this untitled event-dispatch window even without a
            # Slint component. .NET MainWindowHandle can incorrectly select it.
            if window_class.value != 'Winit Thread Event Target' or title.value:
                windows.append({'handle': window, 'title': title.value, 'class': window_class.value})
        return True

    if not user32.EnumWindows(visit, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    return windows


if __name__ == '__main__':
    windows = visible_windows(int(sys.argv[1]))
    if '--panel' in sys.argv[2:]:
        titles = {'zotero-pdf2zh-pro'}
        if '--setup' in sys.argv[2:]:
            titles.add('zotero-pdf2zh-pro 安装')  # Earlier installers used a separate setup window.
        panels = [window for window in windows if window['title'] in titles]
        print(panels[0]['handle'] if panels else 0)
        raise SystemExit(0)
    if windows:
        print(json.dumps(windows, ensure_ascii=True))
        raise SystemExit(1)
