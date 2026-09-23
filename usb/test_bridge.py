"""Transport integration tests using a fake usbmuxd and real local TCP sockets."""

import asyncio
import contextlib
import os
import plistlib
import socket
import tempfile
import unittest
from types import SimpleNamespace

import bridge


USB_DEVICE = {"DeviceID": 7, "Properties": {
    "ConnectionType": "USB", "SerialNumber": "test-ipad"}}
WIFI_DEVICE = {"DeviceID": 8, "Properties": {
    "ConnectionType": "Network", "SerialNumber": "wifi-ipad"}}


class RelayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.mux_path = os.path.join(self.directory.name, "usbmuxd")
        self.servers = []
        self.handlers = set()
        self.errors = []
        self.devices = [WIFI_DEVICE, USB_DEVICE]
        self.connect_result = 0
        self.requests = []
        self.device_handler = None
        mux = await asyncio.start_unix_server(self.wrap(self.handle_mux), self.mux_path)
        self.servers.append(mux)

    async def asyncTearDown(self):
        for server in self.servers:
            server.close()
            await server.wait_closed()
        tasks = list(self.handlers)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.directory.cleanup()
        self.assertEqual(self.errors, [])

    def wrap(self, handler):
        async def wrapped(reader, writer):
            task = asyncio.current_task()
            self.handlers.add(task)
            try:
                await handler(reader, writer)
            except Exception as error:
                self.errors.append(error)
            finally:
                await bridge.close_writer(writer)
                self.handlers.discard(task)
        return wrapped

    async def handle_mux(self, reader, writer):
        size, version, kind, tag = bridge.HEADER.unpack(await reader.readexactly(16))
        self.assertEqual((version, kind, tag), (1, 8, 1))
        request = plistlib.loads(await reader.readexactly(size - 16))
        self.requests.append(request)
        if request["MessageType"] == "ListDevices":
            response = {"DeviceList": self.devices}
        else:
            self.assertEqual(request["MessageType"], "Connect")
            response = {"MessageType": "Result", "Number": self.connect_result}
        payload = plistlib.dumps(response)
        # Fragment headers and bodies, as a real stream is allowed to do.
        packet = bridge.HEADER.pack(16 + len(payload), 1, 8, 1) + payload
        writer.write(packet[:9])
        await writer.drain()
        writer.write(packet[9:])
        await writer.drain()
        if request["MessageType"] == "Connect" and self.connect_result == 0:
            await self.device_handler(reader, writer)

    async def host(self, handler):
        server = await asyncio.start_server(self.wrap(handler), "127.0.0.1", 0)
        self.servers.append(server)
        return server.sockets[0].getsockname()[1]

    def args(self, port):
        return SimpleNamespace(mux_socket=self.mux_path, udid="test-ipad", port=port)

    async def test_discovery_excludes_wifi_and_requires_unambiguous_device(self):
        devices = await bridge.list_devices(self.mux_path)
        self.assertEqual(devices, [USB_DEVICE])
        self.assertEqual(bridge.select_device(devices, None), USB_DEVICE)
        with self.assertRaises(bridge.BridgeError):
            bridge.select_device(devices, "wifi-ipad")
        with self.assertRaises(bridge.BridgeError):
            bridge.select_device([USB_DEVICE, USB_DEVICE], None)

    async def test_mux_connect_port_byte_order_and_raw_stream(self):
        async def device(reader, writer):
            writer.write(b"raw tunnel bytes")
            await writer.drain()
        self.device_handler = device
        reader, writer = await bridge.connect_device(self.mux_path, "test-ipad")
        try:
            self.assertEqual(await reader.read(), b"raw tunnel bytes")
            request = self.requests[-1]
            self.assertEqual(request["DeviceID"], 7)
            self.assertEqual(request["PortNumber"], socket.htons(49151))
        finally:
            await bridge.close_writer(writer)

    async def test_companion_not_listening_reports_mux_error(self):
        self.connect_result = 3
        with self.assertRaisesRegex(bridge.BridgeError, "companion app"):
            await bridge.connect_device(self.mux_path, "test-ipad")

    async def test_http_upgrade_and_binary_video_are_preserved(self):
        request = (b"GET /ws?access_code=secret HTTP/1.1\r\nHost: 127.0.0.1:1701\r\n"
                   b"Connection: Upgrade\r\nUpgrade: websocket\r\n\r\n")
        response = b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n\r\n"
        video = bytes(range(256)) * 4096
        pointer = b'\x81\x06PENCIL'
        received = asyncio.Event()

        async def host(reader, writer):
            self.assertEqual(await reader.readuntil(b"\r\n\r\n"), request)
            writer.write(response + video)
            await writer.drain()
            self.assertEqual(await reader.readexactly(len(pointer)), pointer)

        async def device(reader, writer):
            self.assertEqual(await reader.readexactly(len(bridge.HELLO)), bridge.HELLO)
            writer.write(bridge.START + request)
            await writer.drain()
            self.assertEqual(await reader.readuntil(b"\r\n\r\n"), response)
            self.assertEqual(await reader.readexactly(len(video)), video)
            writer.write(pointer)
            await writer.drain()
            writer.write_eof()
            self.assertEqual(await reader.read(), b"")
            received.set()

        self.device_handler = device
        port = await self.host(host)
        await asyncio.wait_for(bridge.serve_slot(self.args(port)), 5)
        await asyncio.wait_for(received.wait(), 2)

    async def test_half_close_does_not_truncate_response(self):
        response = b"response after request EOF" * 20000
        received = asyncio.Event()

        async def host(reader, writer):
            self.assertEqual(await reader.read(), b"request")
            writer.write(response)
            await writer.drain()

        async def device(reader, writer):
            await reader.readexactly(len(bridge.HELLO))
            writer.write(bridge.START + b"request")
            await writer.drain()
            writer.write_eof()
            self.assertEqual(await reader.read(), response)
            received.set()

        self.device_handler = device
        port = await self.host(host)
        await asyncio.wait_for(bridge.serve_slot(self.args(port)), 5)
        await asyncio.wait_for(received.wait(), 2)

    async def test_concurrent_streams_do_not_mix(self):
        completed = set()
        next_id = iter(range(16))

        async def host(reader, writer):
            while data := await reader.read(4096):
                writer.write(data)
                await writer.drain()

        async def device(reader, writer):
            stream_id = next(next_id)
            payload = bytes([stream_id]) * 100000
            await reader.readexactly(len(bridge.HELLO))
            writer.write(bridge.START + payload)
            await writer.drain()
            writer.write_eof()
            self.assertEqual(await reader.read(), payload)
            completed.add(stream_id)

        self.device_handler = device
        port = await self.host(host)
        await asyncio.wait_for(asyncio.gather(*[
            bridge.serve_slot(self.args(port)) for _ in range(16)]), 8)
        self.assertEqual(completed, set(range(16)))

    async def test_cancellation_closes_both_sockets(self):
        ready = asyncio.Event()
        host_closed = asyncio.Event()
        device_closed = asyncio.Event()

        async def host(reader, writer):
            self.assertEqual(await reader.read(), b"")
            host_closed.set()

        async def device(reader, writer):
            await reader.readexactly(len(bridge.HELLO))
            writer.write(bridge.START)
            await writer.drain()
            ready.set()
            self.assertEqual(await reader.read(), b"")
            device_closed.set()

        self.device_handler = device
        port = await self.host(host)
        slot = asyncio.create_task(bridge.serve_slot(self.args(port)))
        try:
            await asyncio.wait_for(ready.wait(), 2)
        finally:
            slot.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await slot
        await asyncio.wait_for(asyncio.gather(host_closed.wait(), device_closed.wait()), 2)

    async def test_worker_recovers_after_disconnect(self):
        attempts = 0
        recovered = asyncio.Event()

        async def host(reader, writer):
            await reader.read()

        async def device(reader, writer):
            nonlocal attempts
            await reader.readexactly(len(bridge.HELLO))
            attempts += 1
            if attempts == 1:
                return  # Simulate unplugging before a browser stream is assigned.
            recovered.set()
            await reader.read()

        self.device_handler = device
        port = await self.host(host)
        task = asyncio.create_task(bridge.worker(self.args(port), 1))
        try:
            await asyncio.wait_for(recovered.wait(), 4)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self.assertEqual(attempts, 2)


if __name__ == "__main__":
    unittest.main()
