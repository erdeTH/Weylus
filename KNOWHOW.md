# Weylus USB: working setup and everyday use

Updated 2026-09-23. The user reports that this setup works well and confirmed
automatic reconnection after stopping and starting the Linux server.

## This machine

- Project: `/home/cb/PROJECT/Weylus`, branch `codex/ipad-usb`.
- Fork: <https://github.com/erdeTH/Weylus/tree/codex/ipad-usb>.
- Linux: Ubuntu 22.04.5, x86_64, X11.
- Tablet: Wi-Fi-only iPad, iPadOS 26.0, with the **Weylus USB** companion installed
  using iLoader. The exact iPad model was not recorded.
- Transport: USB data cable → usbmuxd → Python relay → local Weylus server.
  Personal Hotspot is not required. The relay selects USB devices only.

## Start and stop

1. Connect the iPad with a data-capable USB-C cable, unlock it, and accept the
   computer trust prompt if shown.
2. Open **Weylus USB** on the iPad and keep it in the foreground.
3. Open **Weylus USB** from the Linux application menu. Alternatively:

   ```sh
   cd /home/cb/PROJECT/Weylus
   bash usb/start-usb.sh
   ```

The launcher starts both the matching Linux server and the relay. It prevents
duplicate launches from this checkout. The installed menu entry is
`/home/cb/.local/share/applications/weylus-usb.desktop` and points to this project;
update it if the project moves.

Linux **Stop → Start** now reconnects the iPad automatically: leave its app open.
Closing the Linux window also stops the relay; reopen the Linux launcher to start
both again. After changing relay code, close and reopen the Linux launcher to
load the changes. A relay-only update does not require reinstalling the iPad app.

## Show one display without stretching

In the iPad's Weylus settings:

- Under **Capture**, choose an individual monitor instead of **Desktop**, which
  captures both screens together. On this setup, **DP-1-2** is the external
  2560×1440 display and **eDP-1** is the laptop's 1920×1080 display.
- Turn off **Video → Stretch Video** to preserve the display's proportions.
  Empty space around the image is normal when the aspect ratios differ.

Monitor names can change with the connected hardware. Capture selection may need
to be made again after a page reload; this client does not persist it.

## If the page does not connect

Check that the iPad is unlocked, trusted, connected by a data cable, and that the
companion is open. Confirm the Linux server is started, then try the companion's
**Reload** button. To check device discovery:

```sh
cd /home/cb/PROJECT/Weylus
python3 usb/bridge.py --list
```

Do not start a separate relay alongside the normal launcher. For manual startup
or selecting between multiple attached Apple devices, use the instructions in
[usb/README.md](usb/README.md).

## Builds and maintenance

Use the Linux server built from this branch, located at `target/release/weylus`.
The old official v0.11.4 release uses separate web and WebSocket ports and does not
match this single-port companion, even though this build also reports version
0.11.4.

The installed companion came from the unsigned IPA at
`ios/.build/artifacts/WeylusUSB-unsigned.ipa`, signed and installed through iLoader.
GitHub's macOS runner builds it; Xcode is not needed on Linux. Signing refreshes
are separate from USB connection recovery.

See [usb/README.md](usb/README.md) for successful build links, source commits,
artifact hashes, transport architecture, and rebuild instructions. Main files:

- `usb/start-usb.sh`: combined Linux launcher and duplicate-launch lock.
- `usb/bridge.py`: USB transport and server-restart recovery.
- `ios/WeylusUSB/`: native iPad companion and local web view.
- `usb/test_bridge.py`: transport regression tests.

The restart fix detects closed desktop connections even while they are reserved
and idle, closes the entire USB connection pool, and reconnects. This lets the
companion detect recovery and reload instead of retaining stale sessions.

## Verification record

On 2026-09-23:

- Linux build downloaded, checksum verified, and launched; HTTP assets loaded
  and the H.264 encoder started.
- User confirmed controls or desktop video on the physical iPad, then reported
  the setup works well.
- User explicitly confirmed automatic reconnection with the iPad app left open
  during Linux **Stop → Start**.
- Duplicate launch check retained exactly one server and one relay.
- All 10 Linux transport integration tests passed, including idle and rapid
  server-reset regression cases.

Run the transport tests from the project folder with:

```sh
python3 -m unittest discover -s usb -p 'test_*.py' -v
```

These tests use simulated usbmuxd and real local sockets. A deliberate test with
iPad Wi-Fi disabled, Pencil pressure/tilt, cable unplug/replug, and measured
long-session stability have not been recorded. The user's successful session
does not establish those separate checks.
