# PowerShell skript: generate_deploy_zip_from_git.ps1
# Archivuje obsah Git projektu bez .git složky ale jen soubory
# potřebnými k přenosu na Raspberry Pi

# ==== KONFIGURACE ====
$sourcePath = "C:\Git\rpi-tcp-proxy"
$outputDir = "C:\Git\rpi-tcp-proxy\download"
$zipPath = "$outputDir\rpi-tcp-proxy-no-git.zip"
$safeZip = "$zipPath.tmp"
# ==== VYLOUČENÉ SOUBORY A SLOŽKY ====
$excludeNames = @(
    ".git", ".github", "tools", "download", "README.md", ".gitignore" 
)

# ==== ZAJISTI, ŽE EXISTUJE VÝSTUPNÍ SLOŽKA ====
if (-not (Test-Path $outputDir)) {
    New-Item -ItemType Directory -Path $outputDir | Out-Null
}

# ==== SÉRIOVÉ VYTVÁŘENÍ SEZNAMU SOUBORŮ ====
$files = Get-ChildItem -Recurse -Path $sourcePath -File | Where-Object {
    $relative = $_.FullName.Substring($sourcePath.Length + 1)
    foreach ($excluded in $excludeNames) {
        if ($relative -like "$excluded*") { return $false }
    }
    return $true
}

# ==== VYTVOŘ ZIP ====
Add-Type -AssemblyName System.IO.Compression.FileSystem
if (Test-Path $safeZip) { Remove-Item $safeZip -Force }

$zipArchive = [System.IO.Compression.ZipFile]::Open($safeZip, 'Create')
foreach ($file in $files) {
    $relativePath = $file.FullName.Substring($sourcePath.Length + 1)
    [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zipArchive, $file.FullName, $relativePath)
}
$zipArchive.Dispose()

# ==== PŘESUŇ HOTOVÝ ZIP ====
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
Move-Item -Force $safeZip $zipPath

Write-Host "OK: ZIP archiv vytvořen: $zipPath"