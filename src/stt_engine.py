"""
stt_engine.py
STT (音声→テキスト) モジュール
- faster-whisper を使用してGPU/CPU上で日本語音声認識を行う
- audio_queue から受け取った numpy 配列を認識し、テキストを text_queue へ渡す
"""

import asyncio
import logging
import os
import sys
from typing import Optional

import numpy as np

# WindowsでCTranslate2(faster-whisper)がcublas等のDLLを見つけられない問題の対処
if sys.platform == "win32":
    import os
    import sys
    try:
        is_compiled = ("__compiled__" in globals()) or getattr(sys, 'frozen', False)
        if is_compiled:
            # Nuitka等でコンパイルされた場合、自身のexeがあるディレクトリをDLL検索パスに追加
            exe_dir = os.path.dirname(os.path.abspath(sys.executable))
            os.add_dll_directory(exe_dir)
            os.environ["PATH"] = exe_dir + os.pathsep + os.environ.get("PATH", "")
            
            # 強制的にメモリにロードさせておく (ctranslate2が後でロード失敗するのを完全防止)
            import ctypes
            import glob
            for dll_name in glob.glob(os.path.join(exe_dir, "*.dll")):
                # 全DLLをロードすると重いので、CUDA関連の特定名のみ
                if "cublas" in dll_name or "cudnn" in dll_name or "nvrtc" in dll_name or "cufft" in dll_name or "curand" in dll_name or "cusparse" in dll_name or "zlib" in dll_name:
                    try:
                        ctypes.CDLL(dll_name)
                        # logger.info(f"Pre-loaded DLL: {os.path.basename(dll_name)}") # ここではlogger未定義なのでパス
                    except Exception:
                        pass
        else:
            # スクリプト実行時: pip で入れた nvidia-* パッケージの bin または torch/lib を追加
            try:
                import site
                site_pkgs = site.getsitepackages()
                if hasattr(site, 'getusersitepackages'):
                    site_pkgs.append(site.getusersitepackages())
                for sp in site_pkgs:
                    for sub_path in [
                        os.path.join("nvidia", "cublas", "bin"),
                        os.path.join("nvidia", "cudnn", "bin"),
                        os.path.join("nvidia", "nvrtc", "bin"),
                        os.path.join("nvidia", "cufft", "bin"),
                        os.path.join("nvidia", "curand", "bin"),
                        os.path.join("nvidia", "cusparse", "bin"),
                        os.path.join("torch", "lib"),
                    ]:
                        p = os.path.join(sp, sub_path)
                        if os.path.exists(p):
                            os.add_dll_directory(p)
                            os.environ["PATH"] = p + os.pathsep + os.environ.get("PATH", "")
            except Exception:
                pass
    except Exception:
        pass

from faster_whisper import WhisperModel

logger = logging.getLogger(__name__)


class STTEngine:
    """
    Faster-Whisper を用いた音声認識エンジン。
    audio_queue から音声セグメントを受け取り、認識済みテキストを text_queue に積む。
    """

    def __init__(self, config: dict, audio_queue: asyncio.Queue, text_queue: asyncio.Queue):
        stt_cfg = config.get("stt", {})
        self.model_size: str = stt_cfg.get("model", "large-v3")
        self.device: str = stt_cfg.get("device", "cuda")
        self.compute_type: str = stt_cfg.get("compute_type", "auto")
        self.language: Optional[str] = stt_cfg.get("language", "ja") or None
        self.device_index: int = stt_cfg.get("device_index", 0)

        self.vad_threshold: float = stt_cfg.get("vad_threshold", 0.15)
        self.beam_size: int = stt_cfg.get("beam_size", 5)

        self.audio_queue = audio_queue
        self.text_queue = text_queue
        self.model: Optional[WhisperModel] = None

    def load_model(self) -> None:
        """Whisperモデルをロードする（起動時に一度だけ呼ぶ）"""
        logger.info(
            f"Whisperモデルをロード中: model={self.model_size}, "
            f"device={self.device}, compute_type={self.compute_type}"
        )
        self.model = WhisperModel(
            self.model_size,
            device=self.device,
            compute_type=self.compute_type,
            device_index=self.device_index,
        )
        logger.info("Whisperモデルのロード完了")

    async def run(self) -> None:
        """音声キューを監視し、テキストへ変換するメインループ"""
        if self.model is None:
            raise RuntimeError("load_model() を先に呼んでください")

        logger.info("STTエンジン処理ループを開始しました。")
        while True:
            audio_segment = await self.audio_queue.get()
            try:
                # CPUバウンドの推論処理を別スレッド(Executor)へ投げる
                text = await asyncio.get_event_loop().run_in_executor(
                    None, self._transcribe, audio_segment
                )
                if text:
                    logger.info(f"[STT Result] {text}")
                    # 翻訳モジュールへ dict で渡す
                    await self.text_queue.put({"text": text, "source": "mic"})
            except Exception as e:
                logger.error(f"STT処理エラー: {e}")
                if "cuBLAS" in str(e) or "CUDA" in str(e):
                    logger.warning("GPUエラーを検出しました。CPUモードへの切り替えを試みます...")
                    try:
                        # モデルをCPUでリロード
                        self.device = "cpu"
                        self.compute_type = "int8"
                        self.load_model()
                        logger.info("CPUモードへ切り替え完了。処理を続行します。")
                    except Exception as re:
                        logger.error(f"モデルの再ロードに失敗しました: {re}")
            finally:
                self.audio_queue.task_done()

    def _transcribe(self, audio: np.ndarray) -> str:
        """
        numpy配列をWhisperで文字起こしする（同期処理・run_in_executorで呼ぶ）
        """
        try:
            segments, info = self.model.transcribe(
                audio,
                language=self.language,
                beam_size=self.beam_size,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=500, threshold=self.vad_threshold),
                condition_on_previous_text=False,
            )

            texts = []
            for seg in segments:
                text = seg.text.strip()
                if not text:
                    continue

                # ── ハルシネーション対策 ──
                # no_speech_prob: Whisperが「これは音声ではない」と判断する確率 (0.0〜1.0)
                #   → 高いほど「音声ではない（ノイズ）」と判断している
                # avg_logprob: 認識結果の平均対数確率
                #   → 低いほど「自信がない（曖昧な認識）」ことを意味する
                no_speech = getattr(seg, "no_speech_prob", 0.0)
                avg_logprob = getattr(seg, "avg_logprob", 0.0)

                if no_speech > 0.6:
                    logger.debug(
                        f"[STT フィルタ] 無音確率が高いためスキップ "
                        f"(no_speech_prob={no_speech:.2f}): {text[:30]}"
                    )
                    continue

                if avg_logprob < -1.0:
                    logger.debug(
                        f"[STT フィルタ] 確信度が低いためスキップ "
                        f"(avg_logprob={avg_logprob:.2f}): {text[:30]}"
                    )
                    continue

                texts.append(text)

            result = " ".join(texts)
            return result
        except Exception as e:
            # ここで発生したエラーは呼び出し元の run() でキャッチされる
            raise e

