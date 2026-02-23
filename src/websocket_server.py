"""
websocket_server.py
WebSocket server:
- Broadcasts translated subtitles from translated_queue
- Broadcasts settings updates from settings_queue (from tkinter UI)
- Sends current settings to new clients on connect
"""

import asyncio
import json
import logging
from pathlib import Path

import websockets
from websockets.server import WebSocketServerProtocol
import sys

logger = logging.getLogger(__name__)

def get_base_path() -> Path:
    if "__compiled__" in globals() or getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    else:
        return Path(__file__).resolve().parent.parent

SETTINGS_FILE = get_base_path() / "settings.json"


def _load_current_settings() -> dict:
    """Load settings from file for sending to new clients."""
    try:
        if SETTINGS_FILE.exists():
            return json.loads(SETTINGS_FILE.read_text("utf-8"))
    except Exception:
        pass
    return {}


class WebSocketBroadcaster:
    def __init__(self, config: dict, translated_queue: asyncio.Queue,
                 settings_queue: asyncio.Queue | None = None,
                 text_queue: asyncio.Queue | None = None):
        ws_cfg = config.get("websocket", {})
        self.host = ws_cfg.get("host", "localhost")
        self.port = ws_cfg.get("port", 8765)
        self.translated_queue = translated_queue
        self.settings_queue   = settings_queue  # from tkinter UI
        self.text_queue       = text_queue      # to Translator
        self._clients: set[WebSocketServerProtocol] = set()

    async def run(self) -> None:
        tasks = [
            asyncio.create_task(self._serve()),
            asyncio.create_task(self._subtitle_sender()),
        ]
        if self.settings_queue is not None:
            tasks.append(asyncio.create_task(self._settings_sender()))
        await asyncio.gather(*tasks)

    async def _serve(self) -> None:
        logger.info(f"WebSocket server: ws://{self.host}:{self.port}")
        async with websockets.serve(self._handle_client, self.host, self.port):
            await asyncio.Future()

    async def _handle_client(self, ws: WebSocketServerProtocol) -> None:
        self._clients.add(ws)
        logger.info(f"Client connected: {ws.remote_address} (total: {len(self._clients)})")
        try:
            # Send current settings immediately so OBS picks them up
            saved = _load_current_settings()
            if saved:
                await ws.send(json.dumps({"type": "settings_update", "settings": saved}))
            await ws.send(json.dumps({"type": "connected", "message": "Subtitle server ready"}))

            # Handle incoming messages (e.g., from SecreAI)
            async for message in ws:
                try:
                    data = json.loads(message)
                    msg_type = data.get("type", "")
                    
                    if msg_type == "translate_request" or "text" in data:
                        text = data.get("text", "")
                        if text and self.text_queue is not None:
                            logger.info(f"Received text from WebSocket: {text[:30]}...")
                            # 翻訳モジュールへ dict で渡す
                            await self.text_queue.put({"text": text, "source": "secreai"})
                except json.JSONDecodeError:
                    pass
                except Exception as e:
                    logger.error(f"Error handling WebSocket message: {e}")
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self._clients.discard(ws)
            logger.info(f"Client disconnected: {ws.remote_address} (total: {len(self._clients)})")

    async def _broadcast(self, message: str) -> None:
        dead = set()
        for ws in list(self._clients):
            try:
                await ws.send(message)
            except websockets.exceptions.ConnectionClosed:
                dead.add(ws)
        self._clients -= dead

    async def _subtitle_sender(self) -> None:
        """Broadcast translated subtitle payloads."""
        while True:
            payload: dict = await self.translated_queue.get()
            if self._clients:
                await self._broadcast(json.dumps(payload, ensure_ascii=False))
            self.translated_queue.task_done()

    async def _settings_sender(self) -> None:
        """Broadcast settings updates from tkinter UI."""
        while True:
            msg: dict = await self.settings_queue.get()
            if self._clients:
                await self._broadcast(json.dumps(msg, ensure_ascii=False))
            self.settings_queue.task_done()
