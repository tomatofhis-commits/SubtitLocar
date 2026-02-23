import asyncio
import json
import logging
import websockets

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def send_to_subtitle(text: str, ws_url: str = "ws://localhost:8765"):
    """
    リアルタイム字幕システムにテキストを送信します。
    """
    try:
        async with websockets.connect(ws_url) as ws:
            payload = {
                "type": "translate_request",
                "text": text
            }
            await ws.send(json.dumps(payload))
            logger.info(f"Subtitles sent successfully: {text[:20]}...")
            
    except ConnectionRefusedError:
        logger.error(f"Cannot connect to the websocket server at {ws_url}. Is main.py running?")
    except Exception as e:
        logger.error(f"Failed to send subtitles: {e}")

if __name__ == "__main__":
    # テスト用実行
    import sys
    test_text = "SecreAIからのテストメッセージです。このメッセージがOBSに表示されれば連携成功です！"
    if len(sys.argv) > 1:
        test_text = sys.argv[1]
    
    asyncio.run(send_to_subtitle(test_text))
