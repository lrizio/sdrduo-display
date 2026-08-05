"""Entry point: starts the SDRduo Display server and opens the browser."""
import webbrowser

import uvicorn

HOST = "127.0.0.1"
PORT = 8080

if __name__ == "__main__":
    webbrowser.open(f"http://{HOST}:{PORT}")
    uvicorn.run("backend.server:app", host=HOST, port=PORT, log_level="info")
