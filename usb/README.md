# Experimental direct USB connection for iPad

This prototype adds a native iPad companion and a Linux relay for a **Wi-Fi-only
iPad connected with a USB data cable**. Personal Hotspot, Ethernet adapters and
jailbreaking are not part of this design. The existing Weylus application and
browser client are reused unchanged.

**Status:** all eight Linux relay integration tests passed locally and in CI. The
Swift app compiled successfully on macOS on 2026-09-23. Download the unsigned IPA
from the **WeylusUSB-unsigned** artifact in the
[successful build](https://github.com/erdeTH/Weylus/actions/runs/35855512452).
The user has since installed and opened the companion on iPadOS 26.0. A direct
USB connection to the companion succeeded, the Linux relay connected to the
matching server, and the user reported seeing Weylus controls or desktop video.
Pencil pressure/tilt, Wi-Fi-disabled operation and longer stability tests remain
unverified.

Build provenance: source commit `5fe12ead2eb1faf4d5e9f3903afab59e113e2469`, Xcode
26.6, iPhoneOS SDK 26.5, arm64, minimum iPadOS 17.0, bundle `org.weylus.usb`.
The downloaded IPA passed ZIP integrity and executable-presence checks.
IPA SHA-256: `7b360613a20b6149f4776597385066a3e0888c6631758bf99749a8014e41cb6d`.

## How the connection works

```text
Linux Weylus (127.0.0.1:1701)
    ↕ HTTP and WebSocket bytes
Python relay → local usbmuxd → USB cable
    ↕
iPad companion (loopback port 49151)
    ↕ local TCP forwarding
WKWebView → http://127.0.0.1:1701 on the iPad
```

Linux initiates connections to the companion through Apple's USB multiplexing
transport. The companion assigns each connection to one local browser connection.
This reverses the direction of connection establishment without changing Weylus's
HTTP or WebSocket protocol. Sixteen streams are maintained to accommodate page
assets, HTTP keep-alive and the video/input WebSocket.

The relay selects **only devices reported as USB**, and pins the selected device's
identifier until the relay exits. It will not fall back to a paired Wi-Fi device.
Both iPad listeners bind to loopback; the Linux destination is also fixed to
loopback. Weylus access-code authentication remains available.

## Build the companion without owning a Mac

1. Put these changes in a GitHub repository you control. The upstream repository
   cannot build changes that only exist on your Linux computer.
2. In its Actions tab, run **Experimental iPad USB companion**. It also runs when
   the companion or relay files change. A macOS runner installs XcodeGen and runs
   `bash ios/build-unsigned.sh` using Xcode.
3. After a successful run, download the **WeylusUSB-unsigned** artifact and extract
   `WeylusUSB-unsigned.ipa` from the artifact ZIP.
4. Sign and install that IPA using a sideloading tool. The unsigned IPA cannot be
   installed directly. No Apple account or signing key is used by the workflow.

For an installation route from Linux, follow the current
[SideStore prerequisites](https://docs.sidestore.io/docs/installation/prerequisites)
and [installation guide](https://docs.sidestore.io/docs/installation/install).
After SideStore is working, import the companion IPA into it. Enter Apple account
credentials only in the signing tool, never in this repository or a chat.
Free-account installations need regular refreshes (typically every seven days),
and iPadOS may require Developer Mode. Initial installation and refreshes can need
internet access even though the Weylus USB session does not.

The app targets iPadOS 17 and newer; iPadOS 26.0 is the intended first hardware
test. This project has not verified SideStore installation or signing on that
device. Future builds must pass before attempting installation.

## Run on Linux

Use the server built from this branch. The official v0.11.4 release uses separate
web and WebSocket ports and is not compatible with this single-port companion.
The **Linux server for iPad USB** workflow builds on Ubuntu 22.04 and produces a
`weylus-linux-usb` artifact containing `weylus-linux-usb.tar.gz` and `SHA256SUMS`.
The first successful matching Linux build is
[run 35857686042](https://github.com/erdeTH/Weylus/actions/runs/35857686042), source
commit `7992e6ee7348f26fe7affc8fb846c0770a82e3fd`. It was downloaded, checksum-verified,
and launched on Ubuntu 22.04.5 x86_64 with X11. No missing runtime libraries were
reported; `/`, `/lib.js` and `/style.css` returned HTTP 200, and the H.264 encoder
started for the connected client. Executable SHA-256:
`85ed6867fef6c90cfd6d399f22b010b845a0fc91027bdda23b3777a5e9827811`.

The checksum file records the archive as `packages/weylus-linux-usb.tar.gz`.
Place the two artifact files in a `packages` directory and run
`sha256sum -c packages/SHA256SUMS` from its parent, then extract the archive into
the repository's `target/release/`.

With that binary installed, you can start the server and relay together from the
repository directory:

```sh
bash usb/start-usb.sh
```

Keep the iPad companion open and connected before starting. Closing the Linux
Weylus window also stops the relay started by this launcher. The manual steps
below are useful when troubleshooting or using an already-running server.

If the desktop looks stretched, open the iPad's Weylus settings and turn off
**Video → Stretch Video**. This preserves the source aspect ratio and can leave
empty space around the image. Selecting one monitor or application under
**Capture** avoids displaying a very wide combined desktop.

Install Python 3.10 or newer, `usbmuxd`, and the libimobiledevice command-line tools
using your distribution's package manager. For Debian/Ubuntu, the package names
are typically `python3`, `usbmuxd`, and `libimobiledevice-utils`.

1. Connect a **data-capable USB-C cable**, unlock the iPad, and accept **Trust This
   Computer**. If needed, run `idevicepair pair` on Linux and respond on the iPad.
2. Start this version of Weylus. To restrict its web listener to this computer:

   ```sh
   ./target/release/weylus --bind-address 127.0.0.1 --web-port 1701
   ```

   Use your actual Weylus binary path. If its window has a Start button, start the
   server there too. Existing Linux capture and `/dev/uinput` setup still applies.
3. Open **Weylus USB** on the iPad and keep it in the foreground.
4. From the repository directory, run:

   ```sh
   python3 usb/bridge.py
   ```

   If several Apple devices are attached:

   ```sh
   python3 usb/bridge.py --list
   python3 usb/bridge.py --udid YOUR_IPAD_IDENTIFIER
   ```

   If Weylus uses another web port, pass `--port PORT` to the relay. The iPad app
   continues using its own local port 1701.
5. The companion should load the normal Weylus page when a relay connection is
   available. Enter the Weylus access code if configured, select a capture source,
   then test video and Pencil input. Tap **Reload** if the page needs retrying.

To stop, press Ctrl+C in the relay terminal. Backgrounding the companion closes
its streams; reopening it starts new listeners and the relay retries. This is a
foreground prototype, not a background USB display driver.

## Verification and limitations

Run the Linux transport tests:

```sh
python3 -m unittest discover -s usb -p 'test_*.py' -v
```

These tests use a simulated usbmuxd and real local sockets. They verify USB-only
selection, port byte order, transparent HTTP upgrade and binary forwarding,
concurrent stream isolation, half-closes, cancellation, and reconnection. They do
**not** prove that iPadOS exposes the app listener through usbmuxd or that WKWebView
decodes the Weylus video correctly.

Before calling this feature working, record an actual device test:

- iPad model and iPadOS version; Linux distribution, Weylus build and capture backend.
- Successful macOS build, signing and installation.
- With iPad Wi-Fi turned off in **Settings**, load the page and stream video.
- Test Pencil pressure/tilt, touch, keyboard, rotation and the access-code form.
- Unplug/replug the cable and background/reopen the app; verify reconnection.
- Run at the intended resolution for at least ten minutes and observe latency,
  heat, memory use and stability. Performance is not measured yet.

The USB transport design is grounded in
[libusbmuxd](https://github.com/libimobiledevice/libusbmuxd) and
[PeerTalk](https://github.com/rsms/peertalk). GitHub documents the remote build hosts
in [GitHub-hosted runners](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).
