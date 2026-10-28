#!/bin/bash
# Start a virtual display (and VNC on it), then run the given command, e.g.
# `prefect worker start ...`. Headed scraping renders into this display.
set -euo pipefail

DISPLAY_NUM="${DISPLAY#:}"
DISPLAY_NUM="${DISPLAY_NUM:-99}"
export DISPLAY=":${DISPLAY_NUM}"

# Locks left behind by a crashed container would block the browser profile
# and the display.
if [ -n "${BROWSER_PROFILE_DIR:-}" ]; then
    rm -f "$BROWSER_PROFILE_DIR/SingletonLock" "$BROWSER_PROFILE_DIR/SingletonCookie"
fi
rm -f "/tmp/.X${DISPLAY_NUM}-lock" "/tmp/.X11-unix/X${DISPLAY_NUM}"

Xvfb "$DISPLAY" -screen 0 1280x1024x24 -ac +extension RANDR &
for _ in $(seq 1 10); do
    xdpyinfo -display "$DISPLAY" >/dev/null 2>&1 && break
    sleep 1
done

# No VNC password: only publish the port where it is not publicly reachable
# (VPN, SSH tunnel). -forever survives viewer disconnects, -shared allows two.
x11vnc -display "$DISPLAY" -rfbport 5900 -forever -shared -nopw -quiet -bg

exec "$@"
