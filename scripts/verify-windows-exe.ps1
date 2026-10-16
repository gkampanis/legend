# Launch the production executable without changing application code or touching user files.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ExePath,
    [string]$EvidenceDirectory = './build-evidence'
)
$ErrorActionPreference = 'Stop'
if (-not $IsWindows) { throw 'This check requires Windows and an interactive desktop.' }
$exe = (Resolve-Path $ExePath).Path
New-Item -ItemType Directory -Force $EvidenceDirectory | Out-Null
$evidence = (Resolve-Path $EvidenceDirectory).Path
$report = [ordered]@{
    executable = [IO.Path]::GetFileName($exe)
    commit = $env:GITHUB_SHA
    verified = $false
    pe_subsystem = $null
    window_title = $null
    child_windows = 0
    screenshot = $null
}

# Confirm the PE is x64 WINDOWS_GUI (subsystem 2), not a console executable.
$reader = [IO.BinaryReader]::new([IO.File]::OpenRead($exe))
try {
    if ($reader.ReadUInt16() -ne 0x5a4d) { throw 'Missing MZ header.' }
    $reader.BaseStream.Position = 0x3c
    $pe = $reader.ReadInt32()
    $reader.BaseStream.Position = $pe
    if ($reader.ReadUInt32() -ne 0x4550) { throw 'Missing PE signature.' }
    if ($reader.ReadUInt16() -ne 0x8664) { throw 'Expected a Windows x64 executable.' }
    $reader.BaseStream.Position = $pe + 24 + 68
    $report.pe_subsystem = $reader.ReadUInt16()
    if ($report.pe_subsystem -ne 2) { throw 'The EXE is not a windowed GUI executable.' }
} finally { $reader.Dispose() }

Add-Type @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class FileOrganizerNative {
    public delegate bool EnumProc(IntPtr window, IntPtr data);
    [StructLayout(LayoutKind.Sequential)]
    public struct Rect { public int Left, Top, Right, Bottom; }
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc callback, IntPtr data);
    [DllImport("user32.dll")] public static extern bool EnumChildWindows(IntPtr parent, EnumProc callback, IntPtr data);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr window, out uint pid);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr window);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr window, StringBuilder text, int length);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr window, StringBuilder text, int length);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr window, out Rect rect);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr window);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr window, int command);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern IntPtr SendMessageTimeout(IntPtr window, uint message, IntPtr wparam, IntPtr lparam, uint flags, uint timeout, out IntPtr result);
}
'@
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms
$process = $null
$ownedIds = [Collections.Generic.HashSet[int]]::new()
try {
    $process = Start-Process -FilePath $exe -PassThru -WorkingDirectory $env:TEMP
    [void]$ownedIds.Add($process.Id)
    $found = [Collections.Generic.List[IntPtr]]::new()
    $deadline = [DateTime]::UtcNow.AddSeconds(60)
    do {
        # PyInstaller --onefile uses a parent bootloader and a child with the same path.
        Get-Process -Name FileOrganizer -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $exe } | ForEach-Object { [void]$ownedIds.Add($_.Id) }
        $found.Clear()
        $callback = [FileOrganizerNative+EnumProc]{
            param($window, $data)
            $ownerId = [uint32]0
            [void][FileOrganizerNative]::GetWindowThreadProcessId($window, [ref]$ownerId)
            if ($ownedIds.Contains([int]$ownerId) -and [FileOrganizerNative]::IsWindowVisible($window)) {
                $title = [Text.StringBuilder]::new(512)
                $class = [Text.StringBuilder]::new(128)
                [void][FileOrganizerNative]::GetWindowText($window, $title, $title.Capacity)
                [void][FileOrganizerNative]::GetClassName($window, $class, $class.Capacity)
                if ($title.ToString() -eq 'File Organizer · Οργάνωση αρχείων' -and $class.ToString() -eq 'TkTopLevel') { $found.Add($window) }
            }
            return $true
        }
        [void][FileOrganizerNative]::EnumWindows($callback, [IntPtr]::Zero)
        if ($found.Count -gt 0) { break }
        if ($process.HasExited) { throw "EXE exited before displaying its GUI (exit $($process.ExitCode))." }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $deadline)
    if ($found.Count -ne 1) { throw 'Expected exactly one visible File Organizer Tk window within 60 seconds.' }
    $window = $found[0]
    $result = [IntPtr]::Zero
    if ([FileOrganizerNative]::SendMessageTimeout($window, 0, [IntPtr]::Zero, [IntPtr]::Zero, 2, 3000, [ref]$result) -eq [IntPtr]::Zero) { throw 'GUI did not respond to a Windows message.' }
    $children = [Collections.Generic.List[IntPtr]]::new()
    $childCallback = [FileOrganizerNative+EnumProc]{ param($child, $data) $children.Add($child); return $true }
    [void][FileOrganizerNative]::EnumChildWindows($window, $childCallback, [IntPtr]::Zero)
    $report.child_windows = $children.Count
    if ($children.Count -lt 20) { throw 'Expected the rendered application controls, not an empty window.' }
    $report.window_title = 'File Organizer · Οργάνωση αρχείων'
    [void][FileOrganizerNative]::ShowWindow($window, 9)
    [void][FileOrganizerNative]::SetForegroundWindow($window)
    Start-Sleep -Seconds 1
    $rect = [FileOrganizerNative+Rect]::new()
    if (-not [FileOrganizerNative]::GetWindowRect($window, [ref]$rect)) { throw 'Unable to measure the GUI window.' }
    $bounds = [Drawing.Rectangle]::FromLTRB($rect.Left, $rect.Top, $rect.Right, $rect.Bottom)
    $bounds = [Drawing.Rectangle]::Intersect($bounds, [Windows.Forms.SystemInformation]::VirtualScreen)
    if ($bounds.Width -lt 400 -or $bounds.Height -lt 300) { throw 'GUI is not sufficiently visible on the runner desktop.' }
    $bitmap = [Drawing.Bitmap]::new($bounds.Width, $bounds.Height)
    $graphics = [Drawing.Graphics]::FromImage($bitmap)
    try {
        $graphics.CopyFromScreen($bounds.Location, [Drawing.Point]::Empty, $bounds.Size)
        $bitmap.Save((Join-Path $evidence 'FileOrganizer-GUI.png'), [Drawing.Imaging.ImageFormat]::Png)
        $report.screenshot = 'FileOrganizer-GUI.png'
        $colors = [Collections.Generic.HashSet[int]]::new()
        for ($x = 0; $x -lt $bitmap.Width; $x += 8) {
            for ($y = 0; $y -lt $bitmap.Height; $y += 8) { [void]$colors.Add($bitmap.GetPixel($x, $y).ToArgb()) }
        }
        if ($colors.Count -lt 8) { throw 'The captured GUI appears blank; visual verification is not possible.' }
    } finally { $graphics.Dispose(); $bitmap.Dispose() }
    # Close through the normal window handler, then require a successful exit.
    if ([FileOrganizerNative]::SendMessageTimeout($window, 0x10, [IntPtr]::Zero, [IntPtr]::Zero, 2, 5000, [ref]$result) -eq [IntPtr]::Zero) { throw 'GUI did not accept a normal close request.' }
    if (-not $process.WaitForExit(15000)) { throw 'EXE failed to close normally.' }
    if ($process.ExitCode -ne 0) { throw "EXE returned exit code $($process.ExitCode)." }
    $report.verified = $true
    Write-Host 'PASS: packaged Windows GUI opened, rendered controls, responded and closed normally.'
} catch {
    $report['error'] = $_.Exception.Message
    throw
} finally {
    $report | ConvertTo-Json | Set-Content (Join-Path $evidence 'verification.json') -Encoding utf8
    foreach ($ownedId in $ownedIds) { Get-Process -Id $ownedId -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue }
}
