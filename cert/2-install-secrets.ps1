# ---------------------------------------------------------------------------
# Step 2 of 2: turn Apple's certificate + your key into a .p12 and store all
# five Mac signing secrets on the GitHub repo. After this, every Mac build
# (.github/workflows/mac.yml) is Developer ID signed and notarized.
#
# Run from this folder:
#   powershell -ExecutionPolicy Bypass -File .\2-install-secrets.ps1
# Needs: gh logged in (gh auth status), the .cer from Apple in this folder.
# Nothing secret is printed or written anywhere except paddlecat-devid.p12
# (git-ignored) and GitHub's encrypted secret store.
# ---------------------------------------------------------------------------
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$openssl = "C:\Program Files\Git\usr\bin\openssl.exe"
$repo    = "Waffleslop/paddlecat"
$teamId  = "J693SF7JDR"
$key     = "paddlecat-devid.key"
$p12     = "paddlecat-devid.p12"

if (-not (Test-Path $key)) { throw "No $key here -- run .\1-make-request.ps1 first." }
$cer = Get-ChildItem -Filter *.cer | Where-Object Name -ne "DeveloperIDG2CA.cer" |
       Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $cer) { throw "Download the Developer ID .cer from Apple into $PSScriptRoot first." }
Write-Host "Using certificate: $($cer.Name)"

# Apple's intermediate, so CI can build the full chain when signing.
Invoke-WebRequest https://www.apple.com/certificateauthority/DeveloperIDG2CA.cer `
    -OutFile DeveloperIDG2CA.cer -UseBasicParsing
& $openssl x509 -inform DER -in $cer.FullName -out paddlecat-devid.pem
& $openssl x509 -inform DER -in DeveloperIDG2CA.cer -out DeveloperIDG2CA.pem

# Check the key really belongs to this certificate before going further.
$m1 = & $openssl x509 -noout -modulus -in paddlecat-devid.pem
$m2 = & $openssl rsa -noout -modulus -in $key
if ($m1 -ne $m2) { throw "This .cer was not issued for $key -- upload paddlecat-devid.csr again." }

# Random export password: only CI ever needs it.
$bytes = New-Object byte[] 24
[System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$p12pw = [Convert]::ToBase64String($bytes)

# SHA1/3DES: the p12 encryption macOS 'security import' reliably accepts.
& $openssl pkcs12 -export -inkey $key -in paddlecat-devid.pem `
    -certfile DeveloperIDG2CA.pem -name "PaddleCAT Developer ID" `
    -keypbe PBE-SHA1-3DES -certpbe PBE-SHA1-3DES -macalg sha1 `
    -out $p12 -passout "pass:$p12pw"
Remove-Item paddlecat-devid.pem, DeveloperIDG2CA.pem

$appleId = Read-Host "Apple ID email"
$asp = Read-Host "App-specific password (from appleid.apple.com)" -AsSecureString
$aspPlain = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($asp))

Write-Host "Saving secrets to $repo ..."
# --body, not a pipe: Windows PowerShell appends a newline to piped input.
$p12b64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes("$PSScriptRoot\$p12"))
gh secret set MAC_CSC_LINK                -R $repo --body $p12b64
gh secret set MAC_CSC_KEY_PASSWORD        -R $repo --body $p12pw
gh secret set APPLE_ID                    -R $repo --body $appleId
gh secret set APPLE_APP_SPECIFIC_PASSWORD -R $repo --body $aspPlain
gh secret set APPLE_TEAM_ID               -R $repo --body $teamId
$aspPlain = $null

gh secret list -R $repo
Write-Host ""
Write-Host "Done. The next Mac build will be signed + notarized." -ForegroundColor Green
Write-Host "Back up paddlecat-devid.key and paddlecat-devid.p12 somewhere safe"
Write-Host "(e.g. D:\keyfiles). They are git-ignored and never leave this PC."
