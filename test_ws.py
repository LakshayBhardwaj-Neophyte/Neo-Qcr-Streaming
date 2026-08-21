from fastapi import FastAPI, WebSocket, Query
import uvicorn
import asyncio
import httpx
import threading
import time

app = FastAPI()

@app.websocket("/ws/stream")
async def websocket_endpoint(
    websocket: WebSocket,
    token: str = Query(...),
    roi_x1: float = Query(...)
):
    await websocket.accept()
    await websocket.send_text("Hello")
    await websocket.close()

def run_server():
    uvicorn.run(app, host="127.0.0.1", port=9999, log_level="info")

server_thread = threading.Thread(target=run_server, daemon=True)
server_thread.start()
time.sleep(2)

import websockets
async def test_ws():
    try:
        # Missing roi_x1
        async with websockets.connect("ws://127.0.0.1:9999/ws/stream?token=abc") as ws:
            print(await ws.recv())
    except Exception as e:
        print("WS ERROR:", e)

asyncio.run(test_ws())
