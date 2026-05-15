param(
    [string]$PythonExe = "python",
    [switch]$SkipElevation
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$sdkDir = "C:\Program Files\Morpho\MorphoManager\Client"
$credentialsScript = Join-Path $projectRoot "instance\morpho.credentials.ps1"

function Test-IsAdministrator {
    $currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($currentIdentity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not $SkipElevation -and -not (Test-IsAdministrator)) {
    Write-Output "Restarting with Administrator rights for MorphoSmart access..."
    $argumentList = @(
        "-NoProfile"
        "-ExecutionPolicy"
        "Bypass"
        "-File"
        ('"{0}"' -f $PSCommandPath)
        "-PythonExe"
        ('"{0}"' -f $PythonExe)
        "-SkipElevation"
    )
    Start-Process -FilePath "powershell.exe" -Verb RunAs -ArgumentList $argumentList | Out-Null
    exit 0
}

if (Test-Path $credentialsScript) {
    . $credentialsScript
}

$env:ATTENDANCE_FINGERPRINT_BACKEND = "morphosmart"
$env:ATTENDANCE_FINGERPRINT_TIMEOUT = "30"
$env:ATTENDANCE_FINGERPRINT_ENROLL_TIMEOUT = "180"
$env:ATTENDANCE_FINGERPRINT_MORPHO_SDK_DIR = $sdkDir
$env:ATTENDANCE_FINGERPRINT_MORPHO_DEVICE_SERIAL = "251946674-1644S019535"
$env:ATTENDANCE_FINGERPRINT_MORPHO_FINGER = "RightIndex"
$env:ATTENDANCE_FINGERPRINT_MORPHO_THRESHOLD = "FAR_5"
$env:ATTENDANCE_DEBUG = "false"

Write-Output "Starting attendance app with MorphoSmart MSO300 backend..."
Write-Output "SDK Dir: $sdkDir"
Write-Output "Device Serial: $env:ATTENDANCE_FINGERPRINT_MORPHO_DEVICE_SERIAL"
if ([string]::IsNullOrWhiteSpace($env:ATTENDANCE_FINGERPRINT_MORPHO_USERNAME) -or [string]::IsNullOrWhiteSpace($env:ATTENDANCE_FINGERPRINT_MORPHO_PASSWORD)) {
    Write-Warning "MorphoManager credentials were not loaded. Check instance\\morpho.credentials.ps1 if the kiosk shows a credentials warning."
} else {
    Write-Output "MorphoManager credentials loaded for user: $env:ATTENDANCE_FINGERPRINT_MORPHO_USERNAME"
}

Push-Location $projectRoot
try {
    & $PythonExe "app.py"
} finally {
    Pop-Location
}
