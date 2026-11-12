# disaster-recovery/node-backup/windows-node-backup-lib.ps1
# Runs on the OPERATOR's Windows PC. Pulls the encrypted stream of node-backup.sh from an exit node over SSH, checks it, keeps a local copy
# and uploads it to the cloud bucket (prefix nodes/<name>/). Nothing is decrypted here; the node never gets cloud credentials.
#
# Usage (from your own private script that already knows rclone and the cloud remote string):
#   . .\windows-node-backup-lib.ps1
#   Invoke-NodeBackupPull -Name de -SshTarget root@HOST -KeyPath C:\path\key -DestRoot E:\VPN_BACKUP\nodes-pulled `
#        -RclonePath C:\rclone\rclone.exe -RemoteBase $remoteBaseWithoutPrefix -Keep 14
# Returns $true on success; throws on failure (so the scheduled task gets a non-zero result).

function Invoke-NodeBackupPull {
    param(
        [Parameter(Mandatory)] [string] $Name,
        [Parameter(Mandatory)] [string] $SshTarget,
        [Parameter(Mandatory)] [string] $KeyPath,
        [Parameter(Mandatory)] [string] $DestRoot,
        [Parameter(Mandatory)] [string] $RclonePath,
        [Parameter(Mandatory)] [string] $RemoteBase,      # e.g. ":s3,...:bucket"  (no trailing slash, no prefix)
        [int] $Keep = 14,
        [int] $MinBytes = 20000,
        [string] $RemoteScript = "/opt/vpn-node-backup/node-backup.sh"
    )
    $ErrorActionPreference = "Stop"
    $dir = Join-Path $DestRoot $Name
    New-Item -ItemType Directory -Force $dir | Out-Null
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $final = Join-Path $dir "node-$Name-$stamp.tar.gz.age"
    $part  = "$final.partial"
    $err   = "$final.err"
    $ssh = Join-Path $env:SystemRoot "System32\OpenSSH\ssh.exe"
    if (-not (Test-Path $ssh)) { $ssh = "ssh" }

    # cmd.exe redirection is binary-safe (PowerShell's '>' would re-encode the stream)
    $cmd = "`"$ssh`" -i `"$KeyPath`" -o BatchMode=yes -o ConnectTimeout=20 -o ServerAliveInterval=15 -o StrictHostKeyChecking=accept-new $SshTarget $RemoteScript > `"$part`" 2> `"$err`""
    & cmd.exe /c $cmd
    $rc = $LASTEXITCODE
    $size = if (Test-Path $part) { (Get-Item $part).Length } else { 0 }
    $head = ""
    if ($size -ge 21) {
        $fs = [System.IO.File]::OpenRead($part); $buf = New-Object byte[] 21; [void]$fs.Read($buf, 0, 21); $fs.Close()
        $head = [System.Text.Encoding]::ASCII.GetString($buf)
    }
    if ($rc -ne 0 -or $size -lt $MinBytes -or $head -ne "age-encryption.org/v1") {
        $why = "node=$Name ssh/exit=$rc bytes=$size header='$head'"
        $tail = if (Test-Path $err) { (Get-Content $err -Tail 3) -join " | " } else { "" }
        Remove-Item $part -Force -ErrorAction SilentlyContinue
        throw "node backup rejected ($why) $tail"
    }
    Move-Item $part $final -Force
    Remove-Item $err -Force -ErrorAction SilentlyContinue
    Write-Host "[node-backup] $Name stored: $final ($([math]::Round($size/1KB)) KB)"

    # upload + verify the size in the cloud
    $remote = "$RemoteBase/nodes/$Name/"
    $objName = Split-Path $final -Leaf
    $eap = $ErrorActionPreference; $ErrorActionPreference = "Continue"      # rclone writes harmless notices to stderr; judge by the exit code only
    & $RclonePath copyto $final "$remote$objName" --s3-no-check-bucket -q 2>&1 | Out-Null
    $upRc = $LASTEXITCODE
    $ErrorActionPreference = $eap
    if ($upRc -ne 0) { throw "cloud upload failed for $Name (rclone exit $upRc)" }
    $ErrorActionPreference = "Continue"; $remoteSize = (& $RclonePath lsf --format s "$remote$objName" -q 2>$null | Select-Object -First 1); $ErrorActionPreference = $eap
    if ("$remoteSize".Trim() -ne "$size") { throw "cloud size mismatch for ${Name}: local $size, cloud '$remoteSize'" }
    Write-Host "[node-backup] $Name uploaded and verified ($size bytes)"

    # retention: local and cloud keep the newest $Keep
    Get-ChildItem $dir -Filter "node-$Name-*.tar.gz.age" | Sort-Object Name -Descending | Select-Object -Skip $Keep | ForEach-Object { Remove-Item $_.FullName -Force }
    $ErrorActionPreference = "Continue"
    $objs = @(& $RclonePath lsf $remote --files-only -q 2>$null | Where-Object { $_ -match "^node-$Name-.*\.tar\.gz\.age$" } | Sort-Object)
    if ($objs.Count -gt $Keep) {
        $objs | Select-Object -First ($objs.Count - $Keep) | ForEach-Object { & $RclonePath deletefile "$remote$_" -q 2>&1 | Out-Null }
    }
    $ErrorActionPreference = $eap
    return $true
}
