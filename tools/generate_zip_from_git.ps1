# PowerShell skript: generate_zip_from_git.ps1
# Archivuje obsah Git projektu kromě .git složky

$repoPath = "$PSScriptRoot"
$outputZip = "$PSScriptRoot\rpi-tcp-proxy-git.zip"

# Odstranit existující archiv
if (Test-Path $outputZip) {
    Remove-Item $outputZip
}

# Vytvořit archiv
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory($repoPath, $outputZip)

Write-Host "Archiv byl vytvořen: $outputZip"
