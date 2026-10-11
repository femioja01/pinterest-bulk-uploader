import os
import sys
import socket
import webbrowser
import time
import threading
import uvicorn

def is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('127.0.0.1', port)) == 0

def find_available_port(start_port: int = 8000) -> int:
    port = start_port
    while is_port_in_use(port):
        port += 1
    return port

def open_browser(url: str):
    time.sleep(1.2)
    print(f"🚀 Opening Pin Uploader in your browser at {url}")
    webbrowser.open(url)

if __name__ == "__main__":
    current_dir = os.path.dirname(os.path.abspath(__file__))
    if current_dir not in sys.path:
        sys.path.insert(0, current_dir)

    target_port = find_available_port(8000)
    app_url = f"http://localhost:{target_port}/"

    print("=" * 65)
    print("📌 Starting Pinterest Bulk Pin Uploader & Scheduler")
    print(f"🌐 Access URL: {app_url}")
    print("=" * 65)

    threading.Thread(target=open_browser, args=(app_url,), daemon=True).start()

    uvicorn.run("src.app:app", host="127.0.0.1", port=target_port, reload=True, log_level="info")
