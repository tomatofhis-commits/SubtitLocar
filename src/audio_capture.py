"""
audio_capture.py
Audio capture module
- microphone mode: uses sounddevice (stable, numpy-compatible)
"""

import asyncio
import logging
import threading
import queue as stdlib_queue
import ctypes
from typing import Optional

import numpy as np
import sounddevice as sd
import soundcard as sc
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


def list_loopback_devices() -> None:
    """Print available loopback (speaker) devices to console."""
    print("\n" + "=" * 60)
    print("  Available Output Devices (Loopback/Speaker)")
    print("=" * 60)
    try:
        mics_all = sc.all_microphones(include_loopback=True)
        mics_real = sc.all_microphones(include_loopback=False)
        real_names = {m.name for m in mics_real}
        
        for i, m in enumerate(mics_all):
            if m.name not in real_names or "loopback" in m.name.lower():
                print(f"  [{i}] {m.name}")
    except Exception as e:
        print(f"  Error listing speakers: {e}")

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
        self.sensitivity: float = audio_cfg.get("sensitivity", 1.0)
        
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
            # Initialize COM for WASAPI (required for soundcard on background threads)
            if hasattr(ctypes, "windll"):
                ctypes.windll.ole32.CoInitialize(None)
            
            if self.mode == "loopback":
                self._capture_loopback()
            else:
                self._capture_mic()
        except Exception as e:
            logger.error(f"Audio capture error: {e}")
        finally:
            if hasattr(ctypes, "windll"):
                ctypes.windll.ole32.CoUninitialize()

    # ------------------------------------------------------------------
    # Microphone capture (sounddevice - numpy compatible)
    # ------------------------------------------------------------------

    def _capture_mic(self) -> None:
        """Capture from microphone using sounddevice with robust reconnection logic."""
        import time
        retry_delay = 2.0

        while not self._stop_event.is_set():
            local_stop = threading.Event()
            try:
                device_idx = _find_input_device_index(self.mic_name)
                if device_idx is None:
                    default = sd.query_devices(kind="input")
                    logger.info(f"Using default microphone: {default['name']}")

                raw_queue: stdlib_queue.Queue = stdlib_queue.Queue()

                def callback(indata: np.ndarray, frames: int, time_info, status):
                    if status:
                        logger.warning(f"sounddevice status: {status}")
                    frame = indata[:, 0]
                    if self.sensitivity != 1.0:
                        frame = frame * self.sensitivity
                    raw_queue.put(frame.copy())

                def run_vad():
                    self._vad_loop_from_queue(raw_queue, local_stop)

                vad_thread = threading.Thread(target=run_vad, daemon=True, name="MicVAD")
                vad_thread.start()

                with sd.InputStream(
                    samplerate=self.sample_rate,
                    channels=1,
                    dtype="float32",
                    blocksize=self.frame_size,
                    device=device_idx,
                    callback=callback,
                ):
                    logger.info("Microphone input stream started successfully.")
                    while not self._stop_event.is_set():
                        time.sleep(0.5)

                local_stop.set()
                break

            except Exception as e:
                logger.error(f"Microphone capture error or disconnected: {e}. Retrying in {retry_delay} seconds...")
                local_stop.set()
                for _ in range(int(retry_delay * 10)):
                    if self._stop_event.is_set():
                        break
                    time.sleep(0.1)

    # ------------------------------------------------------------------
    # Loopback capture (soundcard - WASAPI loopback)
    # ------------------------------------------------------------------

    def _capture_loopback(self) -> None:
        """Capture from system audio using soundcard library with robust reconnection logic."""
        import time
        retry_delay = 2.0

        while not self._stop_event.is_set():
            local_stop = threading.Event()
            try:
                device = None
                mics_all = sc.all_microphones(include_loopback=True)
                mics_real = sc.all_microphones(include_loopback=False)
                real_names = {m.name for m in mics_real}
                
                loopback_mics = [m for m in mics_all if m.name not in real_names or "loopback" in m.name.lower()]

                if self.loopback_name:
                    for m in loopback_mics:
                        if self.loopback_name.lower() in m.name.lower():
                            device = m
                            break
                
                if device is None and loopback_mics:
                    device = loopback_mics[0]
                
                if device is None:
                    logger.warning("No loopback-capable devices identified. Retrying in 2 seconds...")
                    time.sleep(retry_delay)
                    continue
                
                logger.info(f"Using loopback device: {device.name}")

                raw_queue: stdlib_queue.Queue = stdlib_queue.Queue()
                
                def run_vad():
                    self._vad_loop_from_queue(raw_queue, local_stop)
                
                vad_thread = threading.Thread(target=run_vad, daemon=True, name="LoopbackVAD")
                vad_thread.start()

                logger.info(f"Starting loopback recorder at {self.sample_rate}Hz...")
                with device.recorder(samplerate=self.sample_rate) as recorder:
                    drain_frames = int(self.sample_rate * 0.5)
                    logger.info("Draining initial loopback buffer...")
                    try:
                        recorder.record(numframes=drain_frames)
                    except Exception:
                        pass
                    
                    while not self._stop_event.is_set():
                        data = recorder.record(numframes=self.frame_size * 3)
                        
                        if data is None or len(data) == 0:
                            time.sleep(0.01)
                            continue

                        for i in range(0, len(data), self.frame_size):
                            frame = data[i : i + self.frame_size]
                            if len(frame) < self.frame_size:
                                continue
                            
                            if frame.ndim > 1 and frame.shape[1] > 1:
                                frame = np.mean(frame, axis=1)
                            elif frame.ndim > 1:
                                frame = frame[:, 0]
                            
                            if self.sensitivity != 1.0:
                                frame = frame * self.sensitivity
                                
                            raw_queue.put(frame)
                        
                        if not hasattr(self, '_loop_count'): self._loop_count = 0
                        self._loop_count += 1
                        if self._loop_count % 100 == 0:
                            max_amp = np.max(np.abs(data))
                            if max_amp > 0.001:
                                logger.debug(f"Loopback audio detected: max_amp={max_amp:.4f}")
                            elif self._loop_count % 500 == 0:
                                logger.info(f"Loopback heart-beat: max_amp={max_amp:.4f} (Silence?)")
                
                local_stop.set()
                break

            except Exception as e:
                logger.error(f"Loopback capture error or disconnected: {e}. Retrying in {retry_delay} seconds...")
                local_stop.set()
                for _ in range(int(retry_delay * 10)):
                    if self._stop_event.is_set():
                        break
                    time.sleep(0.1)

    # ------------------------------------------------------------------
    # VAD processing loop
    # ------------------------------------------------------------------

    def _vad_loop_from_queue(self, raw_queue: stdlib_queue.Queue, local_stop: Optional[threading.Event] = None) -> None:
        """
        Pull frames from raw_queue, apply VAD, and enqueue speech segments.
        Segments are flushed either when silence is detected OR when max_segment_sec is exceeded.
        """
        buffer: list[np.ndarray] = []
        silent_frames = 0
        is_speaking = False
        
        max_sec = 10.0 if self.mode == "loopback" else 15.0
        max_segment_frames = int(max_sec * 1000 / self.FRAME_DURATION_MS)

        while not self._stop_event.is_set():
            if local_stop and local_stop.is_set():
                break
            try:
                frame: np.ndarray = raw_queue.get(timeout=0.1)
            except stdlib_queue.Empty:
                continue

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
                        self.status_queue.put({"type": "mic", "status": "active", "mode": self.mode})
                buffer.append(frame)

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
                        self.status_queue.put({"type": "mic", "status": "inactive", "mode": self.mode})

    def _enqueue(self, segment: np.ndarray) -> None:
        """Thread-safely push a speech segment to the asyncio queue."""
        duration = len(segment) / self.sample_rate
        
        logger.info(f"Speech segment queued: {duration:.2f}s (Max Amp: {np.max(np.abs(segment)):.4f})")

        def _put_or_drop():
            try:
                self.audio_queue.put_nowait(segment)
            except asyncio.QueueFull:
                logger.warning("Audio queue is full. Dropping older speech segment to avoid memory leak / lag.")
                try:
                    self.audio_queue.get_nowait()
                    self.audio_queue.put_nowait(segment)
                except Exception:
                    pass

        self.loop.call_soon_threadsafe(_put_or_drop)
