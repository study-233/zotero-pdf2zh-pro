param(
    [Parameter(Mandatory = $true)][int]$ProcessId,
    [Parameter(Mandatory = $true)][string]$Executable,
    [string]$ExpectedIcon = (Join-Path $PSScriptRoot '..\windows-app\src-tauri\icons\icon.ico'),
    [string]$ScreenshotPath,
    [string]$TaskbarScreenshotPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$process = Get-Process -Id $ProcessId -ErrorAction Stop
$expectedExecutable = [IO.Path]::GetFullPath($Executable)
if (-not [IO.Path]::GetFullPath($process.Path).Equals($expectedExecutable, [StringComparison]::OrdinalIgnoreCase)) {
    throw "The icon probe process does not match the expected executable: $ProcessId"
}

if (-not ('Pdf2zh.WindowIconProbe' -as [type])) {
    Add-Type -ReferencedAssemblies System.Drawing -TypeDefinition @'
using System;
using System.Drawing;
using System.Drawing.Imaging;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;

namespace Pdf2zh {
    public static class WindowIconProbe {
        delegate bool EnumWindow(IntPtr window, IntPtr parameter);
        [DllImport("user32.dll")] static extern bool EnumWindows(EnumWindow callback, IntPtr parameter);
        [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr window, out uint process);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetClassName(IntPtr window, StringBuilder name, int length);
        [DllImport("user32.dll", SetLastError = true)] static extern IntPtr SendMessageTimeout(IntPtr window, uint message, UIntPtr wparam, IntPtr lparam, uint flags, uint timeout, out UIntPtr result);
        [DllImport("user32.dll")] public static extern uint GetDpiForWindow(IntPtr window);
        [DllImport("user32.dll")] public static extern int GetSystemMetricsForDpi(int index, uint dpi);
        [DllImport("user32.dll", SetLastError = true)] static extern bool GetIconInfo(IntPtr icon, out IconInfo info);
        [DllImport("gdi32.dll", CharSet = CharSet.Unicode)] static extern int GetObject(IntPtr bitmap, int size, out BitmapInfo bitmapInfo);
        [DllImport("gdi32.dll")] static extern int GetDIBits(IntPtr dc, IntPtr bitmap, uint start, uint lines, byte[] pixels, ref BitmapHeader info, uint usage);
        [DllImport("gdi32.dll")] static extern bool DeleteObject(IntPtr handle);
        [DllImport("user32.dll")] static extern IntPtr GetDC(IntPtr window);
        [DllImport("user32.dll")] static extern int ReleaseDC(IntPtr window, IntPtr dc);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern IntPtr LoadLibraryEx(string file, IntPtr reserved, uint flags);
        [DllImport("kernel32.dll")] static extern bool FreeLibrary(IntPtr module);
        [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern IntPtr LoadImage(IntPtr module, IntPtr resource, uint type, int width, int height, uint flags);
        [DllImport("user32.dll", CharSet = CharSet.Unicode, EntryPoint = "LoadImageW", SetLastError = true)] static extern IntPtr LoadImageFile(IntPtr module, string file, uint type, int width, int height, uint flags);
        [DllImport("user32.dll")] static extern bool DestroyIcon(IntPtr icon);
        [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr window, out Rect rect);
        [DllImport("user32.dll")] static extern bool PrintWindow(IntPtr window, IntPtr dc, uint flags);
        [DllImport("user32.dll")] static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
        [DllImport("user32.dll", CharSet = CharSet.Unicode, EntryPoint = "FindWindowW")] static extern IntPtr FindShellWindow(string className, string title);

        [StructLayout(LayoutKind.Sequential)] struct IconInfo { public int Icon; public uint X, Y; public IntPtr Mask, Color; }
        [StructLayout(LayoutKind.Sequential)] struct BitmapInfo { public int Type, Width, Height, WidthBytes; public ushort Planes, BitsPixel; public IntPtr Bits; }
        [StructLayout(LayoutKind.Sequential)] struct BitmapHeader { public uint Size; public int Width, Height; public ushort Planes, BitCount; public uint Compression, SizeImage; public int XPels, YPels; public uint ColorsUsed, ColorsImportant; }
        [StructLayout(LayoutKind.Sequential)] struct Rect { public int Left, Top, Right, Bottom; }

        public static IntPtr FindWindow(int processId) {
            IntPtr found = IntPtr.Zero;
            EnumWindows(delegate(IntPtr window, IntPtr parameter) {
                uint owner; GetWindowThreadProcessId(window, out owner);
                if (owner != processId) return true;
                var name = new StringBuilder(256); GetClassName(window, name, name.Capacity);
                if (name.ToString() != "Tauri Window") return true;
                found = window; return false;
            }, IntPtr.Zero);
            return found;
        }

        static string PixelHash(IntPtr icon, int width, int height) {
            IconInfo info;
            if (!GetIconInfo(icon, out info)) throw new InvalidOperationException("Cannot read icon pixels.");
            try {
                BitmapInfo bitmap;
                if (GetObject(info.Color, Marshal.SizeOf(typeof(BitmapInfo)), out bitmap) == 0 || bitmap.Width != width || bitmap.Height != height)
                    throw new InvalidOperationException("Window icon dimensions do not match the window DPI.");
                var header = new BitmapHeader { Size = (uint)Marshal.SizeOf(typeof(BitmapHeader)), Width = width, Height = -height, Planes = 1, BitCount = 32 };
                var pixels = new byte[width * height * 4]; var dc = GetDC(IntPtr.Zero);
                try {
                    if (GetDIBits(dc, info.Color, 0, (uint)height, pixels, ref header, 0) != height)
                        throw new InvalidOperationException("Cannot copy icon pixels.");
                } finally { ReleaseDC(IntPtr.Zero, dc); }
                using (var sha = SHA256.Create()) return BitConverter.ToString(sha.ComputeHash(pixels)).Replace("-", "").ToLowerInvariant();
            } finally { DeleteObject(info.Color); DeleteObject(info.Mask); }
        }

        public static string Verify(IntPtr window, string executable, string sourceIcon, bool large, uint timeout) {
            uint dpi = GetDpiForWindow(window);
            int width = GetSystemMetricsForDpi(large ? 11 : 49, dpi);
            int height = GetSystemMetricsForDpi(large ? 12 : 50, dpi);
            UIntPtr result;
            if (SendMessageTimeout(window, 0x007f, new UIntPtr(large ? 1u : 0u), IntPtr.Zero, 2, timeout, out result) == IntPtr.Zero || result == UIntPtr.Zero)
                throw new InvalidOperationException("WM_GETICON returned no " + (large ? "BIG" : "SMALL") + " icon.");
            string actual = PixelHash(new IntPtr(unchecked((long)result.ToUInt64())), width, height);
            IntPtr module = LoadLibraryEx(executable, IntPtr.Zero, 0x22);
            if (module == IntPtr.Zero) throw new InvalidOperationException("Cannot read packaged EXE resources.");
            IntPtr resource = IntPtr.Zero, expected = IntPtr.Zero;
            try {
                resource = LoadImage(module, new IntPtr(32512), 1, width, height, 0);
                expected = LoadImageFile(IntPtr.Zero, sourceIcon, 1, width, height, 0x10);
                if (resource == IntPtr.Zero || expected == IntPtr.Zero) throw new InvalidOperationException("Cannot load reference icons.");
                if (actual != PixelHash(resource, width, height) || actual != PixelHash(expected, width, height))
                    throw new InvalidOperationException("Window icon differs from the packaged EXE or current source icon.");
            } finally {
                if (resource != IntPtr.Zero) DestroyIcon(resource);
                if (expected != IntPtr.Zero) DestroyIcon(expected);
                FreeLibrary(module);
            }
            return (large ? "BIG" : "SMALL") + " " + width + "x" + height + " dpi=" + dpi + " sha256=" + actual;
        }

        public static void Capture(IntPtr window, string path) {
            IntPtr previous = SetThreadDpiAwarenessContext(new IntPtr(-4));
            try {
                Rect rect; if (!GetWindowRect(window, out rect)) throw new InvalidOperationException("Cannot read window bounds.");
                using (var bitmap = new Bitmap(rect.Right - rect.Left, rect.Bottom - rect.Top)) {
                    using (var graphics = Graphics.FromImage(bitmap)) {
                        IntPtr dc = graphics.GetHdc();
                        try { if (!PrintWindow(window, dc, 2)) throw new InvalidOperationException("Window capture failed."); }
                        finally { graphics.ReleaseHdc(dc); }
                    }
                    bitmap.Save(path, ImageFormat.Png);
                }
            } finally { SetThreadDpiAwarenessContext(previous); }
        }

        public static void CaptureTaskbar(string path) {
            IntPtr previous = SetThreadDpiAwarenessContext(new IntPtr(-4));
            try {
                IntPtr window = FindShellWindow("Shell_TrayWnd", null);
                Rect rect; if (window == IntPtr.Zero || !GetWindowRect(window, out rect)) throw new InvalidOperationException("Cannot read taskbar bounds.");
                using (var bitmap = new Bitmap(rect.Right - rect.Left, rect.Bottom - rect.Top)) {
                    using (var graphics = Graphics.FromImage(bitmap)) graphics.CopyFromScreen(rect.Left, rect.Top, 0, 0, bitmap.Size);
                    bitmap.Save(path, ImageFormat.Png);
                }
            } finally { SetThreadDpiAwarenessContext(previous); }
        }
    }
}
'@
}

$sourceIcon = (Resolve-Path -LiteralPath $ExpectedIcon).Path
$window = [IntPtr]::Zero
$ready = $false
$verified = @()
$lastDiagnostic = 'No Tauri window belongs to the expected test process.'
$deadline = [Diagnostics.Stopwatch]::StartNew()
while ($deadline.ElapsedMilliseconds -lt 10000) {
    $process.Refresh()
    if ($process.HasExited) { throw 'The test process exited before its window icons became ready.' }
    $window = [Pdf2zh.WindowIconProbe]::FindWindow($ProcessId)
    if ($window -ne [IntPtr]::Zero) {
        try {
            $verified = @()
            foreach ($large in @($false, $true)) {
                $remaining = 10000 - $deadline.ElapsedMilliseconds
                if ($remaining -le 0) { throw 'The window icon readiness deadline expired.' }
                $timeout = [uint32][Math]::Min(2000, $remaining)
                $verified += [Pdf2zh.WindowIconProbe]::Verify($window, $expectedExecutable, $sourceIcon, $large, $timeout)
            }
            $ready = $true
            break
        } catch {
            $lastDiagnostic = $_.Exception.GetBaseException().Message
        }
    }
    $remaining = 10000 - $deadline.ElapsedMilliseconds
    if ($remaining -gt 0) { Start-Sleep -Milliseconds ([int][Math]::Min(250, $remaining)) }
}
if (-not $ready) {
    throw "Window icons did not become ready within 10 seconds (PID=$ProcessId, HWND=$window): $lastDiagnostic"
}
$verified | ForEach-Object { Write-Host $_ }
if ($ScreenshotPath) {
    [Pdf2zh.WindowIconProbe]::Capture($window, [IO.Path]::GetFullPath($ScreenshotPath))
}
if ($TaskbarScreenshotPath) {
    [Pdf2zh.WindowIconProbe]::CaptureTaskbar([IO.Path]::GetFullPath($TaskbarScreenshotPath))
}
Write-Host 'Window BIG and SMALL icons match the packaged EXE and current source icon at the active DPI.'
