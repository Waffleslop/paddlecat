# dmgbuild settings for PaddleCAT-mac.dmg -- the same tool electron-builder
# uses for POTACAT's DMGs: writes the Finder layout directly (no AppleScript),
# so it is reliable on headless CI.
#
#   dmgbuild -s packaging/dmg_settings.py -D app=dist/PaddleCAT.app \
#            PaddleCAT dist/PaddleCAT-mac.dmg
import os

app = defines.get("app", "dist/PaddleCAT.app")  # noqa: F821 (injected by dmgbuild)
appname = os.path.basename(app)

format = "UDZO"
filesystem = "HFS+"
size = None

files = [app]
symlinks = {"Applications": "/Applications"}
hide_extensions = [appname]

# Drag-to-install window: app on the left, Applications on the right.
window_rect = ((200, 160), (540, 360))
default_view = "icon-view"
show_status_bar = False
show_tab_view = False
show_toolbar = False
show_pathbar = False
show_sidebar = False
icon_size = 112
text_size = 13
icon_locations = {
    appname: (140, 170),
    "Applications": (400, 170),
}
background = "#130720"   # PaddleCAT's deep purple
