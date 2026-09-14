# disaster-recovery/windows-pull-backup.ps1
#
# Runs on the OPERATOR's own Windows machine (not on any server). Pulls any
# new encrypted snapshots from the Selectel bucket down to a local folder,
# via rclone. Deliberately does NOT decrypt anything here - the encrypted
# .age files are the point: even a compromised/stolen laptop only yields
# ciphertext without the separately-stored age private key.
#
# NOTE: keep this file plain ASCII. Windows PowerShell 5.1 does not reliably
# read UTF-8-without-BOM .ps1 files and can silently miscount characters on
# em-dashes/non-ASCII text, producing confusing "string is missing the
# terminator" errors on lines that are nowhere near the actual problem -
# confirmed live while writing this script.
#
# Setup (one-time):
#   1. Install rclone for Windows: https://rclone.org/downloads/ (just the
#      .exe, no installer needed) - place it somewhere on PATH, e.g.
#      C:\rclone\rclone.exe
#   2. Fill in the four Selectel* values below (same ones from the
#      server's /etc/vpn-ru-node/backup.env).
#   3. Adjust $DestDir if you want the backups somewhere other than the
#      default below.
#   4. Schedule this script in Task Scheduler:
#      Action: powershell.exe -ExecutionPolicy Bypass -File "<path to this script>"
#      Trigger: e.g. weekly, a day or two after the server-side timer
#      (server runs Mon/Thu 03:00 MSK) so there's always something new to
#      pull - e.g. Tuesday and Friday mornings.
#
# This script only downloads what isn't already present locally (rclone
# copy is incremental) - safe to run as often as you like.

$ErrorActionPreference = "Stop"

# ---- Fill these in from /etc/vpn-ru-node/backup.env on the server ----
$SelectelAccessKey = "REPLACE_ME"
$SelectelSecretKey = "REPLACE_ME"
$SelectelEndpoint  = "s3.ru-6.storage.selcloud.ru"   # host only, no https://
$SelectelBucket    = "vpn-ru-node-dr"
# ------------------------------------------------------------------------

$RclonePath = "rclone.exe"   # or full path, e.g. "C:\rclone\rclone.exe"
$DestDir    = "$env:USERPROFILE\VPN-DR-OFFSITE-BACKUPS"

if (-not (Test-Path $DestDir)) {
    New-Item -ItemType Directory -Path $DestDir | Out-Null
}

if ($SelectelAccessKey -eq "REPLACE_ME") {
    Write-Error "Edit this script first: fill in SelectelAccessKey/SelectelSecretKey from the server's /etc/vpn-ru-node/backup.env"
    exit 1
}

$remote = ":s3,provider=Other,access_key_id=$SelectelAccessKey,secret_access_key=$SelectelSecretKey,endpoint=${SelectelEndpoint}:$SelectelBucket/snapshots/"

Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] Syncing new backups from Selectel to $DestDir ..."
& $RclonePath copy $remote $DestDir --progress
if ($LASTEXITCODE -ne 0) {
    Write-Error "rclone exited with code $LASTEXITCODE"
    exit $LASTEXITCODE
}
Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] Done. Files are still ENCRYPTED (.age) - decrypting needs the separately-stored age private key, on purpose."
