"""The OpenAI key stays out of the log, also with vox -v."""

import logging

import pytest
import websockets

from vox import __main__ as cli
from vox.config import Config
from vox.streaming import StreamingTranscriber

KEY = "sk-proj-LOGCHECK-0123456789abcdef"


@pytest.fixture
def verbose_log(caplog):
    """The logging vox -v sets up, undone afterwards."""
    root = logging.getLogger()
    handlers = root.handlers[:]
    levels = {name: logging.getLogger(name).level for name in cli._QUIET_LOGGERS}
    cli._configure_logging(verbose=True)
    caplog.set_level(logging.DEBUG)  # pytest's handlers make basicConfig a no-op, so set the level it would
    yield caplog
    root.handlers[:] = handlers
    for name, level in levels.items():
        logging.getLogger(name).setLevel(level)


@pytest.mark.anyio
async def test_a_streaming_connect_keeps_the_key_out_of_a_verbose_log(verbose_log):
    async def handler(websocket):
        async for _ in websocket:
            pass

    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    transcriber = StreamingTranscriber(Config(openai_api_key=KEY), ws_url=f"ws://127.0.0.1:{port}")
    try:
        await transcriber.connect()
        await transcriber.close()
    finally:
        server.close()
        await server.wait_closed()

    assert "Sent session.update" in verbose_log.text  # DEBUG lines are in the log
    assert KEY not in verbose_log.text
