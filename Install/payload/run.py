"""Entry point: starts the SDRduo Display server and opens the browser."""
import socket
import threading
import time
import webbrowser

import uvicorn

HOST = "127.0.0.1"
PORT = 8080
URL = f"http://{HOST}:{PORT}"


def _wait_and_open_browser() -> None:
    # webbrowser.open() used to fire immediately, racing the server's own
    # startup -- in particular receiver.open() claiming the RSPduo inside
    # FastAPI's lifespan handler, which runs (and can take a few seconds)
    # BEFORE uvicorn actually binds the listening socket. Opening the tab
    # before that finishes meant the browser just saw "connection
    # refused" once, with no retry of its own -- hit for real on a fresh
    # install. Poll the port instead of guessing a fixed delay: uvicorn
    # only starts accepting connections once lifespan startup has
    # completed, so a successful connect here means the app is genuinely
    # ready, not just "probably ready by now".
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((HOST, PORT), timeout=0.5):
                break
        except OSError:
            time.sleep(0.25)
    else:
        # Didn't come up within 30s (e.g. no RSPduo found and the retry in
        # receiver.open() also timed out) -- open anyway so the browser
        # shows *something* rather than nothing; the real error is on the
        # console, same as if this wait weren't here at all.
        pass
    # open_new() (not open()) so this pops a fresh, focused window instead
    # of silently landing as a background tab in whatever browser window
    # already happens to be open -- easy to miss and looked like "the
    # script isn't doing anything" when it actually was.
    webbrowser.open_new(URL)


if __name__ == "__main__":
    threading.Thread(target=_wait_and_open_browser, daemon=True).start()
    uvicorn.run("backend.server:app", host=HOST, port=PORT, log_level="info")
