"""Serve the desktop site and always run application cleanup on Ctrl+C."""
import argparse
import os
import sys

import uvicorn
from uvicorn.protocols.http.h11_impl import H11Protocol


class LocalHTTPProtocol(H11Protocol):
    def connection_made(self, transport):
        super().connection_made(transport)
        # The event loop can register a socket after Uvicorn has already
        # swept its connection set. This socket has no response to finish.
        if getattr(self.server_state, 'draining', False):
            transport.abort()


class LocalServer(uvicorn.Server):
    def __init__(self, config):
        if sys.platform == 'win32' and config.loop == 'auto':
            # Python 3.12's Windows Proactor transport can raise on a peer reset
            # before detaching the socket. The desktop app only needs async TCP;
            # camera and media subprocesses use synchronous subprocess APIs.
            config.loop = 'asyncio:SelectorEventLoop'
        super().__init__(config)

    def handle_exit(self, sig, frame):
        # Console wrappers can deliver Ctrl+C more than once. Request a
        # graceful exit once, and do not re-raise SIGINT after cleanup.
        self.should_exit = True

    async def shutdown(self, sockets=None):
        self.server_state.draining = True
        await super().shutdown(sockets=sockets)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    if sys.platform == 'win32':
        from ctypes import windll

        # A launcher can pass down Windows' per-process Ctrl+C ignore flag.
        if windll.kernel32.GetConsoleCP():
            windll.kernel32.SetConsoleCtrlHandler(None, False)
    os.environ["ANJU_ACCOUNTS"] = "1"
    config = uvicorn.Config('backend.main:create_app', factory=True, host='127.0.0.1', port=args.port,
                            http=LocalHTTPProtocol, lifespan='on', timeout_graceful_shutdown=5)
    LocalServer(config).run()


if __name__ == '__main__':
    main()
