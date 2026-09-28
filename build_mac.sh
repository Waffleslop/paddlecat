#!/bin/bash
# ---------------------------------------------------------------------------
# Build PaddleCAT.app and PaddleCAT-mac.dmg on a Mac.
#
#   ./build_mac.sh                 # unsigned (ad-hoc) build
#
# Signing + notarization switch on when these are set (CI passes them from
# GitHub secrets; see .github/workflows/mac.yml):
#   MAC_CSC_LINK          base64 of the Developer ID Application .p12
#   MAC_CSC_KEY_PASSWORD  its password
#   APPLE_ID, APPLE_APP_SPECIFIC_PASSWORD, APPLE_TEAM_ID   for notarytool
#
# PYTHON must be a universal2 Python (the python.org installer) to get one
# .app that runs natively on both Apple Silicon and Intel Macs.
# ---------------------------------------------------------------------------
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
VERSION="${VERSION:-0.0.0}"
APP="dist/PaddleCAT.app"
TMPD="${RUNNER_TEMP:-$(mktemp -d)}"
DMG="dist/PaddleCAT-mac.dmg"

"$PYTHON" -m pip install --upgrade pyinstaller pyserial customtkinter

rm -rf build dist
"$PYTHON" -m PyInstaller --windowed --noconfirm \
    --name PaddleCAT \
    --icon icon/macos/PaddleCAT.icns \
    --osx-bundle-identifier com.potacat.paddlecat \
    --target-arch "${TARGET_ARCH:-universal2}" \
    --collect-all customtkinter \
    winkeyer_app.py

PLIST="$APP/Contents/Info.plist"
plutil -replace CFBundleShortVersionString -string "$VERSION" "$PLIST"
plutil -replace CFBundleVersion -string "$VERSION" "$PLIST"
plutil -replace CFBundleDisplayName -string "PaddleCAT" "$PLIST"
echo "Architectures: $(lipo -archs "$APP/Contents/MacOS/PaddleCAT")"

# ---- signing ---------------------------------------------------------------
IDENTITY="-"   # ad-hoc: runs locally, but Gatekeeper will warn on download
if [ -n "${MAC_CSC_LINK:-}" ]; then
    KC="$TMPD/paddlecat.keychain-db"
    KC_PW="$(uuidgen)"
    echo "$MAC_CSC_LINK" | base64 --decode > "$TMPD/cert.p12"
    security create-keychain -p "$KC_PW" "$KC"
    security set-keychain-settings -lut 3600 "$KC"
    security unlock-keychain -p "$KC_PW" "$KC"
    security import "$TMPD/cert.p12" -k "$KC" \
        -P "$MAC_CSC_KEY_PASSWORD" -T /usr/bin/codesign
    security set-key-partition-list -S apple-tool:,apple:,codesign: \
        -s -k "$KC_PW" "$KC" > /dev/null
    security list-keychains -d user -s "$KC" $(security list-keychains -d user | tr -d '"')
    rm -f "$TMPD/cert.p12"
    IDENTITY="$(security find-identity -v -p codesigning "$KC" \
        | grep 'Developer ID Application' | head -1 | sed -E 's/.*"(.*)"/\1/')"
    echo "Signing as: $IDENTITY"
fi

SIGN_ARGS=(--force --deep --sign "$IDENTITY")
if [ "$IDENTITY" != "-" ]; then
    SIGN_ARGS+=(--options runtime --timestamp
                --entitlements packaging/entitlements.mac.plist)
fi
codesign "${SIGN_ARGS[@]}" "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"

notarize() {   # $1 = file to submit
    xcrun notarytool submit "$1" --apple-id "$APPLE_ID" \
        --password "$APPLE_APP_SPECIFIC_PASSWORD" --team-id "$APPLE_TEAM_ID" \
        --wait
}

if [ "$IDENTITY" != "-" ] && [ -n "${APPLE_ID:-}" ]; then
    ditto -c -k --keepParent "$APP" dist/PaddleCAT-notarize.zip
    notarize dist/PaddleCAT-notarize.zip
    xcrun stapler staple "$APP"
    rm dist/PaddleCAT-notarize.zip
fi

# ---- disk image: the app plus an Applications shortcut to drag it onto -----
STAGE="$(mktemp -d)"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -volname PaddleCAT -srcfolder "$STAGE" -ov -format UDZO "$DMG"
rm -rf "$STAGE"

if [ "$IDENTITY" != "-" ]; then
    codesign --force --timestamp --sign "$IDENTITY" "$DMG"
    if [ -n "${APPLE_ID:-}" ]; then
        notarize "$DMG"
        xcrun stapler staple "$DMG"
    fi
fi

echo "Built $DMG ($([ "$IDENTITY" = "-" ] && echo unsigned || echo "signed + notarized"))"
