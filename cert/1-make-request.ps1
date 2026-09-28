# ---------------------------------------------------------------------------
# Step 1 of 2: make a private key + certificate signing request (CSR) for a
# Developer ID Application certificate -- on Windows, no Mac needed.
#
# Run from this folder:
#   powershell -ExecutionPolicy Bypass -File .\1-make-request.ps1
#
# Creates (all git-ignored, never committed):
#   paddlecat-devid.key   your PRIVATE key -- keep it safe, back it up
#   paddlecat-devid.csr   the request you upload to Apple
# ---------------------------------------------------------------------------
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$openssl = "C:\Program Files\Git\usr\bin\openssl.exe"
if (-not (Test-Path $openssl)) { throw "OpenSSL not found at $openssl (it ships with Git for Windows)." }

$key = "paddlecat-devid.key"
$csr = "paddlecat-devid.csr"
if (Test-Path $key) {
    throw "$key already exists. Delete it only if you are sure you want a new key."
}

& $openssl genrsa -out $key 2048
& $openssl req -new -key $key -out $csr -subj "/CN=PaddleCAT Developer ID/C=US"

Write-Host ""
Write-Host "Done. Now, in a browser:" -ForegroundColor Green
Write-Host "  1. developer.apple.com -> Certificates, IDs & Profiles -> Certificates -> +"
Write-Host "  2. Choose 'Developer ID Application' (G2 Sub-CA), upload:"
Write-Host "       $PSScriptRoot\$csr"
Write-Host "  3. Download the certificate (.cer) into this folder:"
Write-Host "       $PSScriptRoot"
Write-Host "  4. appleid.apple.com -> Sign-In and Security -> App-Specific Passwords"
Write-Host "     -> + 'PaddleCAT notarize'. Keep that password handy."
Write-Host "Then run:  powershell -ExecutionPolicy Bypass -File .\2-install-secrets.ps1"
