# SecreAI 字幕システム連携用 実装指示書

このドキュメントは、SecreAI側プログラム（AIが生成したテキストを読み上げるシステム）の処理内に、「ローカルAI字幕システム」へリアルタイムにSecreAIから出力された翻訳テキストを、OBS用の字幕画面（SubtitLocar v0.1）へ直接送信・表示させるためのシンプルな統合ガイドです。

- 連携先の「SubtitLocar v0.1（ローカルAI字幕システム）」は、バックグラウンドのWebSocketサーバー (`ws://localhost:8765`) にて待機しています。
- Pythonの標準ライブラリである `websockets` を使って、文字列をJSON形式で投げるだけで即座に画面へ反映されます。`{ "type": "translate_request", "text": "表示したいテキスト" }` という形式のJSONを投げることで、自動的に翻訳・字幕表示が機能します。

## 実装手順

### 1. 必要なライブラリのインストール
SecreAI側の環境にて、WebSocketsライブラリが必要です。まだ入っていない場合はインストールしてください。
```bash
pip install websockets
```

### 2. 送信用関数の追加
SecreAIの読み上げ機能が含まれるメインプロセス側（例: `main.py` や音声合成の管理クラス）に、以下の非同期関数を追加します。

```python
import asyncio
import json
import logging
import websockets

logger = logging.getLogger(__name__)

async def send_to_subtitle_display(text: str, ws_url: str = "ws://localhost:8765"):
    """
    リアルタイム字幕システムにテキストを送信します。
    """
    # 空白や空文字の場合は送信しない
    if not text or not text.strip():
        return

    try:
        async with websockets.connect(ws_url) as ws:
            payload = {
                "type": "translate_request",
                "text": text.strip()
            }
            await ws.send(json.dumps(payload))
            logger.debug(f"字幕システムへ送信: {text[:20]}...")
            
    except ConnectionRefusedError:
        logger.warning("字幕システム(OBS連携)が起動していません。送信をスキップします。")
    except Exception as e:
        logger.error(f"字幕システムへの送信に失敗しました: {e}")
```

### 3. 送信処理の呼び出し
SecreAIが **「テキストを音声合成エンジン（読み上げ）へ送るタイミング」** または **「AI APIからテキストのチャンクを受け取ったタイミング」** で上記の関数を呼び出します。

#### 同期関数から呼び出す場合の例
もし読み上げのキューイング処理やストリーム処理が同期関数（`async def` ではない）の場合、`asyncio.run()` などで実行する必要があります。

```python
# SecreAIの読み上げキュー処理・生成ループ内などの例
def process_tts_text(text: str):
    # --- 既存の読み上げ処理 ---
    # tts_engine.speak(text) 
    # -----------------------

    # +++ 追加: 字幕システムへの送信 +++
    try:
        # 既にイベントループが回っている環境か否かに応じて呼び出し方を変えてください
        # 単純なスレッド内であれば以下で動作します
        asyncio.run(send_to_subtitle_display(text))
    except Exception as e:
        logger.error(f"字幕送信エラー: {e}")
```

#### 非同期関数（`async def`）から呼び出す場合の例
```python
async def stream_and_speak():
    async for chunk in ai_response_stream:
        # text = チャンク処理...
        
        # 音声合成へ渡す処理...
        # await tts.speak(text)
        
        # +++ 追加: 字幕システムへの送信 +++
        # 非同期の場合は await でそのまま呼ぶ（Taskとしてバックグラウンドで投げるのが推奨です）
        asyncio.create_task(send_to_subtitle_display(text))
```
> **Point**: 送信処理がブロックして読み上げ遅延を発生させないよう、`asyncio.create_task` を用いてバックグラウンド（Fire-and-Forget）で投げる実装を強く推奨します。

---

## 期待される動作の確認方法

1. **字幕システムの起動**
   字幕システム側のPC/ディレクトリにて `python src/main.py` を実行しておく。
2. **OBS（またはブラウザ）の確認**
   OBS上の字幕ソース（`subtitle.html`）が配置されており、パネルに「LIVE」または「接続中」と緑色で表示されているかを確認。
3. **SecreAIシステムの稼働**
   SecreAIを起動して音声合成による読み上げを発生させる。
4. **結果確認**
   読み上げられたテキストが即座にOBS上に表示されれば成功です。
