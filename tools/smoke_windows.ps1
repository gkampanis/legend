$ErrorActionPreference = 'Stop'
$application = (Resolve-Path 'dist/myInvoices/myInvoices.exe').Path
$check = Start-Process -FilePath $application -ArgumentList '--check-demo' -Wait -PassThru
if ($check.ExitCode -ne 0) { throw 'Packaged offline demo failed.' }
$smokeDirectory = Join-Path ([System.IO.Path]::GetTempPath()) ('myInvoices-smoke-' + [guid]::NewGuid())
New-Item -ItemType Directory -Path $smokeDirectory | Out-Null
$smokeDatabase = Join-Path $smokeDirectory 'demo.sqlite3'
$process = $null
try {
    $process = Start-Process -FilePath $application -ArgumentList @('--demo', '--db', ('"' + $smokeDatabase + '"')) -PassThru
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    $visible = $false
    while ([DateTime]::UtcNow -lt $deadline) {
        $process.Refresh()
        if ($process.HasExited) { throw 'Packaged GUI exited before startup.' }
        if ($process.MainWindowTitle -like '*myInvoices*') { $visible = $true; break }
        Start-Sleep -Milliseconds 300
    }
    if (-not $visible) { throw 'Packaged GUI window was not observed.' }
    if (-not (Test-Path $smokeDatabase)) { throw 'Packaged demo database was not created.' }
    Write-Output 'Packaged offline demo and native GUI startup passed.'
} finally {
    if ($process -and -not $process.HasExited) {
        $process.CloseMainWindow() | Out-Null
        if (-not $process.WaitForExit(5000)) { $process.Kill(); $process.WaitForExit() }
    }
    Remove-Item -LiteralPath $smokeDirectory -Recurse -Force
}
