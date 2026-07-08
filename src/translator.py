"""
translator.py - 翻訳モジュール
長文を文境界で自動分割して複数ブロックとして表示する。
"""

import asyncio
import json
import logging
import re
import time
import queue as stdlib_queue

import httpx

logger = logging.getLogger(__name__)

# ゲーム実況・VTuber向け翻訳プロンプト
SYSTEM_PROMPT = """You are a professional real-time subtitle translator for a live streamer (VTuber/game streamer).
Your task is to translate spoken Japanese into natural, concise English suitable for on-screen subtitles.

Rules:
- Output ONLY the translated English text. No explanations, no notes.
- Keep the translation short and natural, matching the casual tone of live commentary.
- Preserve gaming terms, character names, and onomatopoeia as appropriate.
- If the input is already in English or is just noise/filler sounds, output an empty string.
- Never add quotation marks or labels to your output."""

# 翻訳ロジック（旧: 長文分割機能あり）
# SecreAI連携では不要なため分割処理(_split_chunks)は削除されました。

class Translator:
    """
    Ollama LLMを使った非同期翻訳クラス。
    """

    def __init__(self, config: dict, text_queue: asyncio.Queue, translated_queue: asyncio.Queue, status_queue: stdlib_queue.Queue = None):
        trans_cfg = config.get("translation", {})
        self.llm_provider: str        = trans_cfg.get("provider", "ollama")
        self.ollama_url: str          = trans_cfg.get("ollama_url", "http://localhost:11434").replace("localhost", "127.0.0.1")
        self.lmstudio_url: str        = trans_cfg.get("lmstudio_url", "http://localhost:1234/v1").replace("localhost", "127.0.0.1")
        self.model: str               = trans_cfg.get("model", "gemma3:4b")
        self.source_lang: str         = trans_cfg.get("source_lang", "Japanese")
        self.target_lang: str         = trans_cfg.get("target_lang", "English")
        self.timeout_sec: float       = trans_cfg.get("timeout_sec", 15)

        self.text_queue       = text_queue
        self.translated_queue = translated_queue
        self.status_queue     = status_queue
        self.error_cooldown_until: float = 0.0

    async def check_connection(self) -> bool:
        """ローカルLLM（Ollama / LM Studio）への疎通確認"""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                if self.llm_provider == "lmstudio":
                    url = self.lmstudio_url
                    if not url.endswith("/models") and not url.endswith("/models/"):
                        if url.endswith("/v1") or url.endswith("/v1/"):
                            api_url = url.rstrip("/") + "/models"
                        else:
                            api_url = url.rstrip("/") + "/v1/models"
                    else:
                        api_url = url
                    
                    resp = await client.get(api_url)
                    if resp.status_code == 200:
                        data = resp.json()
                        models = []
                        if "data" in data:
                            for item in data["data"]:
                                if "id" in item:
                                    models.append(item["id"])
                        logger.info(f"LM Studio接続OK. 利用可能なモデル: {models}")
                        if self.model not in models:
                            logger.warning(f"モデル '{self.model}' がLM Studioのロードモデルリストに見つかりません。")
                        return True
                else: # ollama
                    resp = await client.get(f"{self.ollama_url}/api/tags")
                    if resp.status_code == 200:
                        tags   = resp.json()
                        models = [m["name"] for m in tags.get("models", [])]
                        logger.info(f"Ollama接続OK. 利用可能なモデル: {models}")
                        if self.model not in models:
                            base_names = [m.split(":")[0] for m in models]
                            if self.model.split(":")[0] not in base_names:
                                logger.warning(
                                    f"モデル '{self.model}' がOllamaに見つかりません。"
                                    f"事前に 'ollama pull {self.model}' を実行してください。"
                                )
                        return True
        except Exception as e:
            logger.error(f"ローカルLLM接続エラー ({self.llm_provider}): {e}")
        return False

    async def run(self) -> None:
        """翻訳キューを監視するメインループ"""
        logger.info("翻訳モジュールを起動しました。テキスト入力を待機中...")

        async with httpx.AsyncClient(timeout=self.timeout_sec) as client:
            while True:
                item = await self.text_queue.get()
                original_text = ""
                source = "mic"
                
                if isinstance(item, dict):
                    original_text = item.get("text", "")
                    source = item.get("source", "mic")
                else:
                    original_text = str(item)
                    
                if not original_text:
                    self.text_queue.task_done()
                    continue

                if time.time() < self.error_cooldown_until:
                    logger.debug("Ollamaが処理落ち中のため、翻訳をスキップして原文のみ流します。")
                    await self._enqueue_chunks(original_text, "(処理落ち中)", source)
                    self.text_queue.task_done()
                    continue

                try:
                    translated = await self._translate(client, original_text)
                    if translated:
                        await self._enqueue_chunks(original_text, translated, source)
                    else:
                        logger.debug(f"翻訳結果が空のためスキップ: {original_text[:30]}")
                except Exception as e:
                    logger.warning(f"Ollamaが過負荷・エラー応答のため、15秒間翻訳をスキップします: {e}")
                    self.error_cooldown_until = time.time() + 15.0
                    await self._enqueue_chunks(original_text, "(処理落ち中)", source)
                finally:
                    self.text_queue.task_done()

    async def _enqueue_chunks(self, original: str, translated: str, source: str) -> None:
        """翻訳結果とソース情報をキューに積む。"""
        try:
            # WindowsコンソールでのShift-JISエンコードエラー（UnicodeEncodeError等）によるクラッシュを防止
            logger.info(f"[字幕 ({source})] {original[:30]}... -> {translated[:40]}...")
        except Exception:
            pass
            
        await self.translated_queue.put({
            "original": original,
            "translated": translated,
            "source": source
        })

        if self.status_queue is not None:
            self.status_queue.put({
                "type": "overlay_subtitle",
                "translated": translated,
                "source": source
            })

    async def _translate(self, client: httpx.AsyncClient, text: str) -> str:
        """ローカルLLMを呼び出して翻訳する（ストリーミング）"""
        if self.status_queue is not None:
            self.status_queue.put({"type": "translation", "status": "active"})
            
        try:
            if self.llm_provider == "lmstudio":
                url = f"{self.lmstudio_url.rstrip('/')}/chat/completions"
                payload = {
                    "model": self.model,
                    "messages": [
                        {
                            "role": "user",
                            "content": f"{SYSTEM_PROMPT}\n\nTranslate the following {self.source_lang} to {self.target_lang}:\n{text}",
                        },
                    ],
                    "stream": True,
                    "temperature": 0.3,
                    "top_p": 0.9,
                }
                
                result_parts = []
                async with client.stream("POST", url, json=payload) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        if line.startswith("data: "):
                            data_str = line[6:].strip()
                            if data_str == "[DONE]":
                                break
                            try:
                                data = json.loads(data_str)
                                content = data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                                if content:
                                    result_parts.append(content)
                            except json.JSONDecodeError:
                                continue
                return "".join(result_parts).strip()
            else: # ollama
                url = f"{self.ollama_url}/api/chat"
                payload = {
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": f"Translate the following {self.source_lang} to {self.target_lang}:\n{text}",
                        },
                    ],
                    "stream": True,
                    "options": {"temperature": 0.3, "top_p": 0.9},
                }
        
                result_parts = []
                async with client.stream("POST", url, json=payload) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        try:
                            data    = json.loads(line)
                            content = data.get("message", {}).get("content", "")
                            if content:
                                result_parts.append(content)
                            if data.get("done", False):
                                break
                        except json.JSONDecodeError:
                            continue
        
                return "".join(result_parts).strip()
        finally:
            if self.status_queue is not None:
                self.status_queue.put({"type": "translation", "status": "inactive"})
