# PaddleCAT — App Icons

Magenta / lime wordmark on violet ground (Variation A master).

## macOS (`macos/`)
- `PaddleCAT.icns`: ready to use. Set as `CFBundleIconFile` or drop into your Electron/Tauri build config.
- `PaddleCAT.iconset/`: source PNGs (16–1024, @1x/@2x). Rebuild with `iconutil -c icns PaddleCAT.iconset`.
- For Xcode: drag the iconset PNGs into an `AppIcon` asset catalog slot.
- Icons follow Apple's grid: 824px rounded body in a 1024 canvas, transparent margin with a baked-in drop shadow.

## Windows (`windows/`)
- `PaddleCAT.ico`: multi-size (16, 20, 24, 32, 40, 48, 64, 96, 128, 256), PNG-compressed, 32-bit.
- `png/`: the same sizes as loose PNGs (for MSIX `Square44x44Logo` targets etc.).
- `store/StoreLogo-300.png`: Microsoft Store listing logo.
- `PaddleCAT-1024.png`: full-bleed master.

Regenerate from `PaddleCAT Icon Export.html`.
