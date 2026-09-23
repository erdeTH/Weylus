#!/usr/bin/env python3
"""Experimental Weylus USB relay. Python 3.10+, no third-party modules.

Each USB connection carries one unmodified HTTP/WebSocket TCP stream. The iPad
accepts connections; Linux initiates them through usbmuxd (never Wi-Fi pairing).
"""

import argparse
import asyncio
import contextlib
import logging
import plistlib
import signal
import socket
import struct

HELLO = b"WEYLUS-USB/1\n"
START = b"START\n"
DEVICE_PORT = 49151
HEADER = struct.Struct("<IIII")
LOG = logging.getLogger("weylus-usb")


class BridgeError(Exception):
    pass


async def close_writer(writer):
    if writer is not None:
        writer.close()
        with contextlib.suppress(OSError, asyncio.TimeoutError):
            await asyncio.wait_for(writer.wait_closed(), 2)


async def mux_request(reader, writer, message):
    body = plistlib.dumps({"ClientVersionString": "WeylusUSB-1", **message})
    writer.write(HEADER.pack(HEADER.size + len(body), 1, 8, 1) + body)
    await writer.drain()
    size, version, kind, tag = HEADER.unpack(await reader.readexactly(HEADER.size))
    if not HEADER.size <= size <= 1024 * 1024 or (version, kind, tag) != (1, 8, 1):
        raise BridgeError("Invalid response from usbmuxd")
    try:
        result = plistlib.loads(await reader.readexactly(size - HEADER.size))
    except (ValueError, plistlib.InvalidFileException) as error:
        raise BridgeError("Invalid usbmuxd property list") from error
    if not isinstance(result, dict):
        raise BridgeError("Expected a usbmuxd response dictionary")
    return result


async def list_devices(mux_socket):
    reader, writer = await asyncio.open_unix_connection(mux_socket)
    try:
        response = await mux_request(reader, writer, {"MessageType": "ListDevices"})
        # Explicit USB filter: paired Wi-Fi devices must never be selected.
        return [device for device in response.get("DeviceList", [])
                if device.get("Properties", {}).get("ConnectionType") == "USB"]
    finally:
        await close_writer(writer)


def select_device(devices, udid):
    if udid:
        devices = [device for device in devices
                   if device.get("Properties", {}).get("SerialNumber") == udid]
    if not devices:
        raise BridgeError("No matching USB device. Connect, unlock, and trust this computer.")
    if len(devices) != 1:
        raise BridgeError("Multiple USB devices. Use --list and select one with --udid.")
    return devices[0]


async def connect_device(mux_socket, udid):
    device = select_device(await list_devices(mux_socket), udid)
    reader, writer = await asyncio.open_unix_connection(mux_socket)
    try:
        response = await mux_request(reader, writer, {
            "MessageType": "Connect",
            "DeviceID": device["DeviceID"],
            "PortNumber": socket.htons(DEVICE_PORT),
        })
        if response.get("Number") != 0:
            raise BridgeError("Cannot reach companion app. Keep Weylus USB open on the iPad "
                              f"(usbmuxd result {response.get('Number')}).")
        return reader, writer
    except BaseException:
        await close_writer(writer)
        raise


async def copy_stream(reader, writer):
    while data := await reader.read(64 * 1024):
        writer.write(data)
        await writer.drain()
    # Preserve half-closes so an HTTP response can finish after request EOF.
    writer.write_eof()
    await writer.drain()


async def relay(device_reader, device_writer, host_reader, host_writer):
    pumps = [asyncio.create_task(copy_stream(device_reader, host_writer)),
             asyncio.create_task(copy_stream(host_reader, device_writer))]
    try:
        await asyncio.gather(*pumps)
    finally:
        for pump in pumps:
            pump.cancel()
        await asyncio.gather(*pumps, return_exceptions=True)


async def serve_slot(args):
    device_writer = host_writer = None
    try:
        device_reader, device_writer = await asyncio.wait_for(
            connect_device(args.mux_socket, args.udid), 5)
        # Only announce readiness when the Weylus listener is reachable.
        host_reader, host_writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", args.port), 5)
        device_writer.write(HELLO)
        await device_writer.drain()
        if await device_reader.readexactly(len(START)) != START:
            raise BridgeError("Companion protocol mismatch; update both components.")
        LOG.debug("Relaying a browser connection")
        await relay(device_reader, device_writer, host_reader, host_writer)
    finally:
        await close_writer(device_writer)
        await close_writer(host_writer)


async def worker(args, index):
    previous_error = None
    while True:
        try:
            await serve_slot(args)
            previous_error = None
        except (OSError, asyncio.IncompleteReadError, asyncio.TimeoutError, BridgeError) as error:
            message = str(error) or type(error).__name__
            if index == 0 and message != previous_error:
                LOG.warning("%s; retrying. Weylus must be listening on 127.0.0.1:%d.",
                            message, args.port)
                previous_error = message
            await asyncio.sleep(1)


async def run(args):
    if args.list:
        devices = await asyncio.wait_for(list_devices(args.mux_socket), 5)
        for device in devices:
            print(device["Properties"]["SerialNumber"])
        if not devices:
            raise BridgeError("No USB devices found")
        return
    # Pin to one UDID for this run; reconnects must not switch to another iPad.
    device = select_device(await asyncio.wait_for(list_devices(args.mux_socket), 5), args.udid)
    args.udid = device["Properties"]["SerialNumber"]
    LOG.info("USB-only relay to Weylus at 127.0.0.1:%d. Open Weylus USB on the iPad. "
             "Press Ctrl+C to stop.", args.port)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop.set)
    tasks = [asyncio.create_task(worker(args, index)) for index in range(16)]
    stop_task = asyncio.create_task(stop.wait())
    try:
        done, _ = await asyncio.wait([*tasks, stop_task], return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        for task in [*tasks, stop_task]:
            task.cancel()
        await asyncio.gather(*tasks, stop_task, return_exceptions=True)
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(signum)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--udid", help="USB iPad identifier (required with multiple devices)")
    parser.add_argument("--list", action="store_true", help="List USB device identifiers")
    parser.add_argument("--port", type=int, default=1701, help="Local Weylus web port")
    parser.add_argument("--mux-socket", default="/var/run/usbmuxd")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s: %(message)s")
    try:
        asyncio.run(run(args))
    except (OSError, asyncio.TimeoutError, BridgeError) as error:
        parser.exit(1, f"USB relay: {error}\n")


if __name__ == "__main__":
    main()
