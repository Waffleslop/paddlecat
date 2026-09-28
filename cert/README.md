# Mac signing certificate (made on Windows)

Two scripts that get a **Developer ID Application** certificate and store the
Mac signing secrets on GitHub, with no Mac needed. After this, every Mac build
in `.github/workflows/mac.yml` is signed and notarized by Apple.

1. In PowerShell in this folder, run
   `powershell -ExecutionPolicy Bypass -File .\1-make-request.ps1`. It makes your private key
   and a signing request (`paddlecat-devid.csr`) and prints the next steps.
2. At developer.apple.com, create a **Developer ID Application** certificate
   from that `.csr`, and download the `.cer` into this folder. At
   appleid.apple.com, create an app-specific password.
3. Run `powershell -ExecutionPolicy Bypass -File .\2-install-secrets.ps1`. It builds the `.p12`, then asks for your
   Apple ID and the app-specific password and saves all five secrets to the
   repo.

Everything generated here (`.key`, `.csr`, `.cer`, `.p12`) is git-ignored.
Back up the `.key` and `.p12` somewhere safe.
