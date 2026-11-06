import asyncio
from contextlib import asynccontextmanager
import signal
import socket
import struct
import sys

from fastapi import FastAPI
import pytest
import uvicorn

from backend.config import Settings
from backend.local_server import LocalHTTPProtocol, LocalServer
from backend.main import create_app


def test_double_ctrl_c_cleans_up_with_a_late_idle_connection(caplog):
    async def run():
        cleaned = asyncio.Event()
        late_connection = asyncio.Event()

        @asynccontextmanager
        async def lifespan(_app):
            yield
            cleaned.set()

        app = FastAPI(lifespan=lifespan)

        @app.get('/api/camera/status')
        async def status():
            return {'state': 'stopped'}

        class DelayedProtocol(LocalHTTPProtocol):
            def connection_made(self, transport):
                if not self.server_state.connections:
                    super().connection_made(transport)
                    return
                # Register the idle socket after the shutdown sweep.
                self.loop.call_later(0.25, super().connection_made, transport)
                late_connection.set()

        server = LocalServer(uvicorn.Config(app, http=DelayedProtocol, lifespan='on',
                                            timeout_graceful_shutdown=1, log_config=None))
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
            serving = asyncio.create_task(server.serve(sockets=[listener]))
            writers = []
            try:
                while not server.started:
                    await asyncio.sleep(0.01)
                reader, writer = await asyncio.open_connection('127.0.0.1', port)
                writers.append(writer)
                writer.write(b'GET /api/camera/status HTTP/1.1\r\nHost: localhost\r\n\r\n')
                await writer.drain()
                assert b'200 OK' in await reader.readuntil(b'\r\n\r\n')
                idle_reader, idle_writer = await asyncio.open_connection('127.0.0.1', port)
                writers.append(idle_writer)
                await late_connection.wait()
                server.handle_exit(signal.SIGINT, None)
                server.handle_exit(signal.SIGINT, None)
                await serving
                assert await idle_reader.read() == b''
                assert cleaned.is_set()
            finally:
                for writer in writers:
                    writer.close()
                if not serving.done():
                    serving.cancel()
                    await asyncio.gather(serving, return_exceptions=True)

    asyncio.run(asyncio.wait_for(run(), timeout=4))
    assert 'timeout graceful shutdown exceeded' not in caplog.text


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows socket reset regression')
def test_video_range_can_be_reset_and_reopened_without_transport_errors(tmp_path, caplog):
    media = tmp_path / 'range-fixture.mp4'
    media.write_bytes(b'a' * 8192 + b'b' * 8192)
    app = create_app(Settings(data_dir=tmp_path / 'data'))
    app.state.db.add_video({'id': 'v_reset', 'name': media.name, 'sha256': 'a' * 64, 'duration_ms': 3000,
                            'original_path': str(media), 'playback_path': str(media), 'metadata': {}})
    server = LocalServer(uvicorn.Config(app, http=LocalHTTPProtocol, lifespan='on', log_config=None,
                                        timeout_graceful_shutdown=1))
    transport_errors = []

    def read_range(port, start, expected, *, reset=False):
        with socket.create_connection(('127.0.0.1', port), timeout=2) as client:
            if reset:
                client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('HH', 1, 0))
            end = start + len(expected) - 1
            client.sendall((f'GET /media/videos/v_reset HTTP/1.1\r\nHost: localhost\r\n'
                            f'Range: bytes={start}-{end}\r\nConnection: close\r\n\r\n').encode())
            with client.makefile('rb') as stream:
                assert b'206 Partial Content' in stream.readline()
                while stream.readline() not in (b'\r\n', b''):
                    pass
                assert stream.read(len(expected)) == expected

    async def run():
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(lambda _loop, context: transport_errors.append(context))
        call_soon = loop.call_soon

        def allow_reset_before_close(callback, *args, context=None):
            # Make the reset arrive while the server's socket cleanup is queued.
            if getattr(callback, '__name__', '') == '_call_connection_lost' and args == (None,):
                return loop.call_later(0.05, callback, *args, context=context)
            return call_soon(callback, *args, context=context)

        loop.call_soon = allow_reset_before_close
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
            serving = asyncio.create_task(server.serve(sockets=[listener]))
            try:
                while not server.started:
                    await asyncio.sleep(0.01)
                await asyncio.to_thread(read_range, port, 0, b'a' * 8192, reset=True)
                await asyncio.to_thread(read_range, port, 8192, b'b' * 8192)
            finally:
                server.handle_exit(signal.SIGINT, None)
                await serving

    with asyncio.Runner(loop_factory=server.config.get_loop_factory()) as runner:
        runner.run(asyncio.wait_for(run(), timeout=5))
    assert not transport_errors
    assert 'timeout graceful shutdown exceeded' not in caplog.text
