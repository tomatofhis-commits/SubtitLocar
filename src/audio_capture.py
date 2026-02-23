"""
audio_capture.py
Audio capture module
- microphone mode: uses sounddevice (stable, numpy-compatible)
"""

import asyncio
import logging
import threading
import queue as stdlib_queue
from typing import Optional

import numpy as np
import sounddevice as sd
import webrtcvad

logger = logging.getLogger(__name__)


def list_devices() -> None:
    """Print available audio devices to console."""
    print("\n" + "=" * 60)
    print("  Available Input Devices (Microphone)")
    print("=" * 60)
    devices = sd.query_devices()
    for i, d in enumerate(devices):
        if d["max_input_channels"] > 0:
            print(f"  [{i}] {d['name']}")

    print("=" * 60 + "\n")


def _find_input_device_index(name: Optional[str]) -> Optional[int]:
    """Find input device index by partial name match (case-insensitive)."""
    if name is None:
        return None  # sounddevice uses system default
    devices = sd.query_devices()
    for i, d in enumerate(devices):
        if d["max_input_channels"] > 0 and name.lower() in d["name"].lower():
            logger.info(f"Using microphone: [{i}] {d['name']}")
            return i
    raise ValueError(f"Microphone not found: '{name}'\nAvailable: {[d['name'] for d in devices if d['max_input_channels'] > 0]}")


def _float32_to_pcm16_bytes(audio: np.ndarray) -> bytes:
    """Convert float32 numpy array to int16 PCM bytes (for webrtcvad)."""
    clipped = np.clip(audio, -1.0, 1.0)
    return (clipped * 32767).astype(np.int16).tobytes()


class AudioCapture:
    """
    Audio capture class supporting microphone and loopback modes.
    Detected speech segments are pushed to audio_queue (asyncio.Queue).
    """

    FRAME_DURATION_MS = 30  # VAD frame length in ms (must be 10, 20, or 30)

    def __init__(self, config: dict, audio_queue: asyncio.Queue, loop: asyncio.AbstractEventLoop, status_queue: stdlib_queue.Queue = None):
        audio_cfg = config.get("audio", {})
        self.mode: str = audio_cfg.get("mode", "microphone")
        self.mic_name: Optional[str] = audio_cfg.get("microphone_name")
        self.loopback_name: Optional[str] = audio_cfg.get("loopback_device_name")
        self.sample_rate: int = audio_cfg.get("sample_rate", 16000)
        self.vad_aggressiveness: int = audio_cfg.get("vad_aggressiveness", 2)
        self.silence_duration_sec: float = audio_cfg.get("silence_duration_sec", 0.8)
        self.audio_queue = audio_queue
        self.status_queue = status_queue
        self.loop = loop
        self._stop_event = threading.Event()

        self.vad = webrtcvad.Vad(self.vad_aggressiveness)
        self.frame_size = int(self.sample_rate * self.FRAME_DURATION_MS / 1000)
        self.max_silent_frames = int(self.silence_duration_sec * 1000 / self.FRAME_DURATION_MS)
    def start(self) -> None:
        """Start capture in a background thread."""
        t = threading.Thread(target=self._run, daemon=True, name="AudioCapture")
        t.start()
        logger.info(f"Audio capture started (mode={self.mode})")

    def stop(self) -> None:
        self._stop_event.set()

    def _run(self) -> None:
        try:
            self._capture_mic()
        except Exception as e:
            logger.error(f"Audio capture error: {e}")

    # ------------------------------------------------------------------
    # Microphone capture (sounddevice - numpy compatible)
    # ------------------------------------------------------------------

    def _capture_mic(self) -> None:
        """Capture from microphone using sounddevice with VAD."""
        device_idx = _find_input_device_index(self.mic_name)
        if device_idx is None:
            default = sd.query_devices(kind="input")
            logger.info(f"Using default microphone: {default['name']}")

        raw_queue: stdlib_queue.Queue = stdlib_queue.Queue()

        def callback(indata: np.ndarray, frames: int, time_info, status):
            if status:
                logger.warning(f"sounddevice status: {status}")
            # indata shape: (frames, channels) - take mono
            raw_queue.put(indata[:, 0].copy())

        with sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            blocksize=self.frame_size,
            device=device_idx,
            callback=callback,
        ):
            self._vad_loop_from_queue(raw_queue)

    # ------------------------------------------------------------------
    # VAD processing loop
    # ------------------------------------------------------------------

    def _vad_loop_from_queue(self, raw_queue: stdlib_queue.Queue) -> None:
        """
        Pull frames from raw_queue, apply VAD, and enqueue speech segments.
        Segments are flushed either when silence is detected OR when max_segment_sec is exceeded.
        """
        buffer: list[np.ndarray] = []
        silent_frames = 0
        is_speaking = False
        # 安全装置: 最大15秒で強制的に切り出す (15000ms / 30ms = 500 frames)
        max_segment_frames = int(15.0 * 1000 / self.FRAME_DURATION_MS)



        while not self._stop_event.is_set():
            try:
                frame: np.ndarray = raw_queue.get(timeout=0.1)
            except stdlib_queue.Empty:
                continue

            # Ensure exactly frame_size samples
            if len(frame) < self.frame_size:
                frame = np.pad(frame, (0, self.frame_size - len(frame)))
            elif len(frame) > self.frame_size:
                frame = frame[:self.frame_size]

            pcm_bytes = _float32_to_pcm16_bytes(frame)
            try:
                is_active = self.vad.is_speech(pcm_bytes, self.sample_rate)
            except Exception:
                is_active = False

            if is_active:
                silent_frames = 0
                if not is_speaking:
                    is_speaking = True
                    if self.status_queue:
                        self.status_queue.put({"type": "mic", "status": "active"})
                buffer.append(frame)

                # 強制切り出しチェック (ノイズ等で途切れない場合のストッパー)
                if len(buffer) >= max_segment_frames:
                    segment = np.concatenate(buffer)
                    self._enqueue(segment)
                    buffer = []
                    is_speaking = False
                    if self.status_queue:
                        self.status_queue.put({"type": "mic", "status": "inactive"})

            elif is_speaking:
                buffer.append(frame)
                silent_frames += 1
                if silent_frames >= self.max_silent_frames:
                    segment = np.concatenate(buffer)
                    self._enqueue(segment)
                    buffer = []
                    silent_frames = 0
                    is_speaking = False
                    if self.status_queue:
                        self.status_queue.put({"type": "mic", "status": "inactive"})

    def _enqueue(self, segment: np.ndarray) -> None:
        """Thread-safely push a speech segment to the asyncio queue."""
        duration = len(segment) / self.sample_rate
        
        # デバッグ: 毎回キャプチャした生の音声をWAVとして上書き保存し、データが壊れていないか確認する
        try:
            import wave
            import os
            debug_path = os.path.join(os.getcwd(), "debug_mic_capture.wav")
            pcm16 = _float32_to_pcm16_bytes(segment)
            with wave.open(debug_path, 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2) # 16-bit
                wf.setframerate(self.sample_rate)
                wf.writeframes(pcm16)
            max_amp = np.max(np.abs(segment))
            logger.info(f"Speech segment queued: {duration:.2f}s (Max Amp: {max_amp:.4f}) -> Saved debug WAV {debug_path}")
        except Exception as e:
            logger.info(f"Speech segment queued: {duration:.2f}s (Failed to save WAV: {e})")

        asyncio.run_coroutine_threadsafe(
            self.audio_queue.put(segment),
            self.loop
        )
