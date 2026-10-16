# The existing responsive-layout tests need a desktop wide enough for both layouts.
$ErrorActionPreference = 'Stop'
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class FileOrganizerDisplay {
    [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
    public struct Mode {
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst=32)] public string DeviceName;
        public ushort SpecVersion, DriverVersion, Size, DriverExtra;
        public uint Fields;
        public int PositionX, PositionY;
        public uint Orientation, FixedOutput;
        public short Color, Duplex, YResolution, TTOption, Collate;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst=32)] public string FormName;
        public ushort LogPixels;
        public uint BitsPerPel, Width, Height, Flags, Frequency, ICMMethod, ICMIntent, MediaType, DitherType, Reserved1, Reserved2, PanningWidth, PanningHeight;
    }
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern bool EnumDisplaySettings(string device, int index, ref Mode mode);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int ChangeDisplaySettings(ref Mode mode, uint flags);
}
'@
$mode = [FileOrganizerDisplay+Mode]::new()
$mode.Size = [Runtime.InteropServices.Marshal]::SizeOf($mode)
if (-not [FileOrganizerDisplay]::EnumDisplaySettings($null, -1, [ref]$mode)) { throw 'Unable to read the runner desktop mode.' }
$mode.Width = 1920
$mode.Height = 1080
$mode.Fields = 0x80000 -bor 0x100000
$result = [FileOrganizerDisplay]::ChangeDisplaySettings([ref]$mode, 0)
if ($result -ne 0) { throw "Unable to set runner desktop to 1920x1080 (Windows result $result)." }
Start-Sleep -Seconds 1
Add-Type -AssemblyName System.Windows.Forms
$screen = [Windows.Forms.Screen]::PrimaryScreen.Bounds
if ($screen.Width -lt 1600 -or $screen.Height -lt 1000) { throw "Runner desktop is too small for GUI tests: $screen" }
Write-Host "Runner desktop configured: $screen"
