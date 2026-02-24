"""
main.py
メインオーケストレーター
- 全モジュール（音声取得、STT、翻訳、WebSocket）を非同期で起動・管理する
- Rich ライブラリによるリッチなコンソールUIを提供
- Ctrl+C によるグレースフルシャットダウンに対応

起動順序（修正済み）:
  1. WebSocketサーバー先行起動 → OBSブラウザソースが即座に接続できる
  2. Ollama疎通確認
  3. Whisperモデルのロード（時間がかかることがある）
  4. 音声キャプチャ開始
"""

import sys
import os
import types

# --- 環境変数パッチ: 過去のCUDA再インストールの影響で無効なGPU UUIDがシステム環境変数に残留し、
#     デバイス全体でのCUDA認識が全滅してしまう（CUDA devices: 0）エラーを自動回避する ---
if "CUDA_VISIBLE_DEVICES" in os.environ and os.environ["CUDA_VISIBLE_DEVICES"].startswith("GPU-"):
    del os.environ["CUDA_VISIBLE_DEVICES"]

# === Nuitka Bug Workaround for av / Cython ===
# Nuitka compiled PyAV sources crash looking for `__spec__` in Cython.Shadow.
# This mock bypasses the runtime Cython dependency entirely.
if "cython" not in sys.modules:
    _dummy_cython = types.ModuleType("cython")
    _dummy_cython.__path__ = []

    class _DummyShadow(types.ModuleType):
        def __init__(self):
            super().__init__("Cython.Shadow")
        def __getattr__(self, name):
            if name == "__spec__":
                return None
            return lambda *args, **kwargs: None

    _shadow = _DummyShadow()
    _dummy_cython.Shadow = _shadow

    sys.modules["cython"] = _dummy_cython
    sys.modules["Cython"] = _dummy_cython
    sys.modules["Cython.Shadow"] = _shadow
# =============================================

import asyncio
import json
import logging
import sys
import queue
from pathlib import Path

import yaml
from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.table import Table
from rich import print as rprint

def get_base_path() -> Path:
    """Nuitkaコンパイル時とPythonスクリプト実行時でベースパスを切り替える"""
    if "__compiled__" in globals() or getattr(sys, 'frozen', False):
        # Nuitkaでビルドされた実行ファイルとして動作している場合
        return Path(sys.executable).resolve().parent
    else:
        # スクリプトとして動作している場合
        return Path(__file__).resolve().parent.parent

# src ディレクトリをパスに追加
sys.path.insert(0, str(get_base_path() / "src"))

from audio_capture import AudioCapture, list_devices
from stt_engine import STTEngine
from translator import Translator
from websocket_server import WebSocketBroadcaster
from settings_ui import start_settings_window

# ============================================================
# ロギング設定
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler(rich_tracebacks=True, markup=True)],
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("websockets").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)
console = Console()


def load_config(config_path: Path) -> dict:
    """Load config file, stripping any BOM (UTF-8/UTF-16 LE/BE)."""
    if not config_path.exists():
        console.print(f"[bold red]Config file not found: {config_path}[/bold red]")
        sys.exit(1)
    raw = config_path.read_bytes()
    # Strip BOM if present (UTF-16 LE/BE or UTF-8)
    if raw.startswith(b'\xff\xfe'):          # UTF-16 LE BOM
        text = raw[2:].decode('utf-16-le')
    elif raw.startswith(b'\xfe\xff'):        # UTF-16 BE BOM
        text = raw[2:].decode('utf-16-be')
    elif raw.startswith(b'\xef\xbb\xbf'):   # UTF-8 BOM
        text = raw[3:].decode('utf-8')
    else:
        text = raw.decode('utf-8')
    return yaml.safe_load(text)


def print_banner(config: dict) -> None:
    """起動バナーを表示する"""
    audio_cfg = config.get("audio", {})
    stt_cfg = config.get("stt", {})
    trans_cfg = config.get("translation", {})
    ws_cfg = config.get("websocket", {})

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="bold cyan", justify="right")
    table.add_column(style="white")

    table.add_row("マイク", str(audio_cfg.get("microphone_name") or "(デフォルト)"))
    table.add_row("STTモデル", f"{stt_cfg.get('model', 'large-v3')} [{stt_cfg.get('device', 'cuda').upper()}]")
    table.add_row("翻訳モデル", trans_cfg.get("model", "gemma3:12b"))
    table.add_row(
        "WebSocket",
        f"ws://{ws_cfg.get('host', 'localhost')}:{ws_cfg.get('port', 8765)}"
    )

    panel = Panel(
        table,
        title="[bold yellow] SubtitLocar v0.1 [/bold yellow]",
        subtitle="[dim]Ctrl+C to quit[/dim]",
        border_style="bright_blue",
        padding=(1, 2),
    )
    console.print(panel)


async def startup_sequence(
    config: dict,
    audio_queue: asyncio.Queue,
    text_queue: asyncio.Queue,
    translated_queue: asyncio.Queue,
    settings_queue: asyncio.Queue,
    status_queue: queue.Queue,
    loop: asyncio.AbstractEventLoop,
):
    """
    WebSocketを先に起動してからWhisperをロードする
    （OBSがモデルロード中でも接続できるようにする）
    """
    ws_cfg = config.get("websocket", {})
    host = ws_cfg.get("host", "localhost")
    port = ws_cfg.get("port", 8765)

    # ── Step 1: WebSocketサーバーを先行起動 ──────────────────────
    ws_broadcaster = WebSocketBroadcaster(
        config, translated_queue, settings_queue, text_queue
    )
    ws_task = asyncio.create_task(ws_broadcaster.run())
    console.print(f"[green][OK] WebSocket server started: ws://{host}:{port}[/green]")
    console.print("[dim]  -> OBS browser source status indicator will show 'LIVE'[/dim]")

    # --- Step 2: Ollama疎通確認 -----------------------------------
    translator = Translator(config, text_queue, translated_queue, status_queue)
    console.print("\n[cyan]Ollamaサーバーへの接続を確認中...[/cyan]")
    ok = await translator.check_connection()
    if not ok:
        console.print(
        "[bold red]Cannot connect to Ollama.\n"
        "  1. Make sure 'ollama serve' is running\n"
        "  2. Check ollama_url in config.yaml[/bold red]"
        )
        ws_task.cancel()
        sys.exit(1)

    # --- Step 3: Whisperモデルのロード ----------------------------
    stt_engine = STTEngine(config, audio_queue, text_queue)
    stt_cfg = config.get("stt", {})
    console.print(
        f"\n[cyan]Loading Whisper model: {stt_cfg.get('model', 'large-v3')} "
        f"[{stt_cfg.get('device', 'cuda').upper()}][/cyan]"
    )
    console.print("[dim]  -> First load may take a few minutes. OBS is already connected.[/dim]")

    try:
        await asyncio.get_event_loop().run_in_executor(None, stt_engine.load_model)
    except Exception as e:
        console.print(f"[bold red]Failed to load Whisper model: {e}[/bold red]")
        console.print("[yellow]Hint: try setting stt.device to 'cpu' or use a smaller model in config.yaml[/yellow]")
        ws_task.cancel()
        sys.exit(1)

    console.print("[green][OK] Whisper model loaded[/green]")

    # Step 4: Start audio capture
    audio_capture = AudioCapture(config, audio_queue, loop, status_queue)

    console.print("\n[bold green][OK] All modules ready. Start speaking![/bold green]\n")
    audio_capture.start()

    # WebSocketに加えて STT・翻訳ループも並列実行
    await asyncio.gather(
        ws_task,
        stt_engine.run(),
        translator.run(),
    )


async def main() -> None:
    """メイン処理"""
    base = get_base_path()
    config_path = base / "config.yaml"
    config = load_config(config_path)

    # settings.json からシステム設定を読み込んで config を上書き
    settings_json = base / "settings.json"
    if settings_json.exists():
        try:
            sj = json.loads(settings_json.read_text("utf-8"))
            audio_cfg = config.setdefault("audio", {})
            stt_cfg = config.setdefault("stt", {})
            trans_cfg = config.setdefault("translation", {})

            # 項目ごとに個別に try-except して一部分の失敗が全体に波及しないようにする
            def safe_load(key, target_dict, target_key, transform=None):
                val = sj.get(key)
                if val is not None:
                    try:
                        target_dict[target_key] = transform(val) if transform else val
                        return True
                    except Exception:
                        pass
                return False

            if safe_load("audioMicDevice", audio_cfg, "microphone_name"):
                if audio_cfg["microphone_name"] == "(デフォルト)":
                    audio_cfg["microphone_name"] = None
                else:
                    console.print(f"[cyan]音声デバイス: {audio_cfg['microphone_name']}[/cyan]")

            if safe_load("aiModel", trans_cfg, "model"):
                console.print(f"[cyan]AIモデル: {trans_cfg['model']}[/cyan]")

            stt_lang = sj.get("sttLanguage")
            if stt_lang:
                if "自動判定" in stt_lang or "Auto" in stt_lang:
                    stt_cfg["language"] = None
                    console.print("[cyan]音声認識言語: Auto[/cyan]")
                else:
                    lang_map = {
                        "Japanese": "ja", "English": "en", "Chinese": "zh", 
                        "Korean": "ko", "Spanish": "es", "French": "fr", 
                        "German": "de", "Russian": "ru"
                    }
                    mapped = lang_map.get(stt_lang)
                    if mapped:
                        stt_cfg["language"] = mapped
                        console.print(f"[cyan]音声認識言語: {stt_lang} ({mapped})[/cyan]")

            safe_load("transSourceLang", trans_cfg, "source_lang")
            safe_load("transTargetLang", trans_cfg, "target_lang")
            if "source_lang" in trans_cfg: console.print(f"[cyan]翻訳元: {trans_cfg['source_lang']}[/cyan]")
            if "target_lang" in trans_cfg: console.print(f"[cyan]翻訳先: {trans_cfg['target_lang']}[/cyan]")

            if safe_load("micSensitivity", audio_cfg, "sensitivity", float):
                console.print(f"[cyan]マイク感度: {audio_cfg['sensitivity']}倍[/cyan]")

            if safe_load("vadThreshold", stt_cfg, "vad_threshold", float):
                console.print(f"[cyan]無音判定レベル: {stt_cfg['vad_threshold']}[/cyan]")

            if safe_load("beamSize", stt_cfg, "beam_size", int):
                console.print(f"[cyan]Beam Size (探索深度): {stt_cfg['beam_size']}[/cyan]")

        except Exception as e:
            console.print(f"[yellow]settings.json の解析失敗: {e}[/yellow]")
    else:
        console.print("[dim]settings.json が存在しません。デフォルト設定を使用します。[/dim]")

    print_banner(config)

    # デバイス一覧を表示
    rprint("[dim]利用可能なオーディオデバイスを確認中...[/dim]")
    try:
        list_devices()
    except Exception as e:
        console.print(f"[yellow]デバイス一覧の取得に失敗: {e}[/yellow]")

    # キューの初期化
    audio_queue: asyncio.Queue      = asyncio.Queue(maxsize=10)
    text_queue: asyncio.Queue       = asyncio.Queue(maxsize=20)
    translated_queue: asyncio.Queue = asyncio.Queue(maxsize=20)
    settings_queue: asyncio.Queue   = asyncio.Queue(maxsize=10)
    status_queue: queue.Queue       = queue.Queue()

    loop = asyncio.get_event_loop()

    # 設定UIウィンドウを別スレッドで起動
    start_settings_window(settings_queue, loop, status_queue)
    console.print("[green][OK] Settings window launched[/green]")

    try:
        await startup_sequence(config, audio_queue, text_queue, translated_queue, settings_queue, status_queue, loop)
    except asyncio.CancelledError:
        pass
    finally:
        console.print("\n[yellow]システムを停止しました。[/yellow]")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
