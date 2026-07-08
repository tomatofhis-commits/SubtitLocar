"""
settings_ui.py - Tkinter Settings Window
Runs in a daemon thread. Settings → asyncio queue → WebSocket → subtitle.html (OBS)
Audio device settings saved to settings.json + affects config at next launch.
"""

import json
import asyncio
import threading
import tkinter as tk
from tkinter import ttk, colorchooser, font as tkfont
from pathlib import Path
from typing import Optional

import os
import signal
import sys
import logging
import sounddevice as sd
import soundcard as sc

logger = logging.getLogger(__name__)

def get_base_path() -> Path:
    if "__compiled__" in globals() or getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    else:
        return Path(__file__).resolve().parent.parent

SETTINGS_FILE = get_base_path() / "settings.json"

import yaml

def __get_config_yaml():
    try:
        path = get_base_path() / "config.yaml"
        if path.exists():
            return yaml.safe_load(path.read_text("utf-8")) or {}
    except Exception:
        pass
    return {}

_cfg = __get_config_yaml()
_stt_cfg = _cfg.get("stt", {})
_trans_cfg = _cfg.get("translation", {})

def _get_gpu_devices() -> list[str]:
    """Detect available CUDA GPU names and indices."""
    gpus = ["GPU 0 (デフォルト)"]
    try:
        import ctranslate2
        count = ctranslate2.get_cuda_device_count()
    except Exception:
        count = 0

    if count <= 1:
        return gpus

    import subprocess
    names = []
    try:
        res = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, check=True
        )
        names = [line.strip() for line in res.stdout.strip().split("\n") if line.strip()]
    except Exception:
        try:
            res = subprocess.run(
                ["wmic", "path", "win32_VideoController", "get", "name"],
                capture_output=True, text=True, check=True
            )
            lines = [line.strip() for line in res.stdout.split("\n")[1:] if line.strip()]
            names = [l for l in lines if "NVIDIA" in l]
        except Exception:
            pass

    gpus = []
    for i in range(count):
        name = names[i] if i < len(names) else "NVIDIA GPU"
        gpus.append(f"GPU {i}: {name}")
    return gpus

_LANG_MAP_REV = {
    "ja": "Japanese", "en": "English", "zh": "Chinese", 
    "ko": "Korean", "es": "Spanish", "fr": "French", 
    "de": "German", "ru": "Russian"
}

DEFAULTS = {
    # Display
    "showOriginal":      True,
    "showTranslated":    True,
    "showStatus":        True,
    "textAlign":         "left",
    "verticalPos":       "bottom",      # top / center / bottom
    "maxBlocks":         3,
    "maxCharsPerLine":   40,
    "displayDuration":   8000,
    # Font
    "fontFamily":        "Noto Sans JP",
    "fontSizeOrig":      36,
    "fontSizeTrans":     46,
    # Color
    "colorOrig":         "#ffe066",
    "colorTrans":        "#ffffff",
    "outlineColor":      "#000000",
    "outlineWidth":      2,
    # SecreAI overrides
    "fontFamilySecreAI": "Noto Sans JP",
    "colorOrigSecreAI":  "#ffb3b3",
    "colorTransSecreAI": "#ff99ff",
    # Typewriter
    "typewriterOn":      True,
    "typewriterSpeed":   35,
    # Connection
    "reconnectInterval": 3000,
    "audioCaptureMode":  "microphone",  # microphone / loopback
    "audioMicDevice":    "(デフォルト)",
    "audioLoopbackDevice": "(デフォルト)",
    "llmProvider":       _trans_cfg.get("provider", "ollama"),
    "ollamaUrl":         _trans_cfg.get("ollama_url", "http://localhost:11434"),
    "lmstudioUrl":       _trans_cfg.get("lmstudio_url", "http://localhost:1234/v1"),
    "cachedOllamaModels": ["gemma3:4b", "gemma2:9b", "llama3:8b", "phi3:mini"],
    "cachedLMStudioModels": ["meta-llama-3-8b-instruct", "gemma-2-9b-it", "gemma-3-4b-it"],
    "aiModel":           _trans_cfg.get("model", "gemma3:4b"),
    "sttLanguage":       _LANG_MAP_REV.get(_stt_cfg.get("language"), "自動判定 (Auto)"),
    "sttModel":          "large-v3-turbo (標準・高速高精度)",
    "sttGpuDevice":      "GPU 0 (デフォルト)",
    "transSourceLang":   _trans_cfg.get("source_lang", "Japanese"),
    "transTargetLang":   _trans_cfg.get("target_lang", "English"),
    "micSensitivity":    1.0,
    "vadThreshold":      0.15,
    "beamSize":          5,
    # Overlay Settings
    "overlayEnabled":      False,
    "overlaySize":         "medium",
    "overlayBgStyle":      "with_bg",
    "overlayTransparency": 0,
    "overlayX":            960,  # 画面中央(1920の半分)
    "overlayY":            900,  # 画面下部付近
    "overlayDragMode":     False,
}

LOCAL_KEYS = {
    "audioCaptureMode", "audioMicDevice", "audioLoopbackDevice", 
    "llmProvider", "ollamaUrl", "lmstudioUrl", "aiModel",
    "sttLanguage", "sttModel", "sttGpuDevice", "transSourceLang", "transTargetLang",
    "micSensitivity", "vadThreshold", "beamSize",
    "overlayEnabled", "overlaySize", "overlayBgStyle", "overlayTransparency", "overlayX", "overlayY", "overlayDragMode"
}   # excluded from WS broadcast


def load_settings() -> dict:
    try:
        if SETTINGS_FILE.exists():
            s = json.loads(SETTINGS_FILE.read_text("utf-8"))
            # 移行措置: 旧 sttJapaneseModel を新 sttModel にマップ
            if "sttJapaneseModel" in s and "sttModel" not in s:
                ja_m = s["sttJapaneseModel"]
                if ja_m == "Kotoba-Whisper v2.0 (公式)":
                    s["sttModel"] = "Kotoba-Whisper v2.0 (日本語特化・公式)"
                elif ja_m == "Kotoba-Whisper v2.2 (有志版)":
                    s["sttModel"] = "Kotoba-Whisper v2.2 (日本語特化・有志版)"
                else:
                    s["sttModel"] = "large-v3-turbo (標準・高速高精度)"
            return {**DEFAULTS, **s}
    except Exception:
        pass
    return dict(DEFAULTS)


def save_settings(s: dict) -> None:
    try:
        SETTINGS_FILE.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _get_mic_devices() -> list[str]:
    """Return list of input device names from sounddevice."""
    try:
        devs = sd.query_devices()
        names = ["(デフォルト)"] + [d["name"] for d in devs if d["max_input_channels"] > 0]
        return names
    except Exception:
        return ["(デフォルト)"]


def _get_speaker_devices() -> list[str]:
    """Return list of loopback devices by comparing mic lists."""
    try:
        mics_all = sc.all_microphones(include_loopback=True)
        mics_real = sc.all_microphones(include_loopback=False)
        real_names = {m.name for m in mics_real}
        
        loopbacks = [m.name for m in mics_all if m.name not in real_names or "loopback" in m.name.lower()]
        return ["(デフォルト)"] + sorted(list(set(loopbacks)))
    except Exception:
        return ["(デフォルト)"]


# ── UI Palette ──────────────────────────────────────────────────────
BG      = "#1a1d27"
BG2     = "#22263a"
BORDER  = "#2e3248"
ACCENT  = "#6c8fff"
ACCENT2 = "#a78bfa"
FG      = "#e8eaf6"
MUTED   = "#7b84a8"
RED     = "#ef4444"


class SettingsWindow:
    def __init__(self, settings_queue: asyncio.Queue, loop: asyncio.AbstractEventLoop, status_queue=None):
        self.queue        = settings_queue
        self.status_queue = status_queue
        self.loop         = loop
        self.settings     = load_settings()
        self._vars: dict[str, tk.Variable] = {}
        self._loading     = True

        self.root = tk.Tk()
        self.root.title("SubtitLocar 設定パネル v0.10")
        self.root.geometry("520x760")
        self.root.configure(bg=BG)
        self.root.resizable(True, True)
        self.root.minsize(440, 520)

        try:
            icon_path = get_base_path() / "subtitlocar.ico"
            if icon_path.exists():
                self.root.iconbitmap(str(icon_path))
        except Exception:
            pass

        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

        # Gather Windows fonts (after Tk() init)
        self._all_fonts = sorted(
            {f for f in tkfont.families() if not f.startswith("@")},
            key=str.lower
        )

        self._build_ui()
        self._load_to_ui()
        self._loading = False
        
        # Initialize Overlay Window Manager
        try:
            from overlay_window import OverlayWindowManager
            self.overlay_mgr = OverlayWindowManager()
            
            def _pos_changed(x, y):
                self.settings["overlayX"] = x
                self.settings["overlayY"] = y
                save_settings(self.settings)
            self.overlay_mgr.on_position_changed = _pos_changed
            
            # Apply initial settings
            self.overlay_mgr.update_settings(self.settings)
        except Exception as e:
            logger.error(f"Failed to initialize overlay window: {e}")

        # Closing hook
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        
        self.root.after(100, self._poll_status_queue)

    # ── Build UI ──────────────────────────────────────────────────────

    def _build_ui(self):
        style = ttk.Style()
        try:
            style.theme_use("vista")
        except Exception:
            pass
        style.configure("TCombobox", fieldbackground="white", background="white", foreground="black",
                        selectbackground=ACCENT, selectforeground="white")
        style.map("TCombobox",
                  fieldbackground=[("readonly", "white")],
                  foreground=[("readonly", "black")])
        # ドロップダウンリスト（展開時）の文字色と背景色を指定
        self.root.option_add("*TCombobox*Listbox.foreground", "black")
        self.root.option_add("*TCombobox*Listbox.background", "white")
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "white")

        # Header
        hdr = tk.Frame(self.root, bg=ACCENT, height=54)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        
        title_fr = tk.Frame(hdr, bg=ACCENT)
        title_fr.pack(side="left", fill="both", expand=True)
        
        tk.Label(title_fr, text="⚙  字幕設定パネル", font=("Segoe UI", 13, "bold"),
                 bg=ACCENT, fg="white").pack(side="left", padx=16, pady=4)
                 
        # インジケータ領域
        ind_fr = tk.Frame(hdr, bg=ACCENT)
        ind_fr.pack(side="right", padx=12, fill="y", pady=4)
        
        self._status_lbl = tk.Label(ind_fr, text="", font=("Segoe UI", 9),
                                    bg=ACCENT, fg="white")
        self._status_lbl.pack(side="top", anchor="e")
        
        # マイクとAIの動作インジケータ
        status_row = tk.Frame(ind_fr, bg=ACCENT)
        status_row.pack(side="bottom", anchor="e")
        
        self._mic_ind = tk.Label(status_row, text=" ● マイク ", font=("Segoe UI", 9, "bold"),
                                 bg=BG2, fg=MUTED, padx=4, pady=2, relief="flat")
        self._mic_ind.pack(side="left", padx=(0, 8))
        
        self._ai_ind = tk.Label(status_row, text=" ● AI翻訳 ", font=("Segoe UI", 9, "bold"),
                                bg=BG2, fg=MUTED, padx=4, pady=2, relief="flat")
        self._ai_ind.pack(side="left")

        # Scrollable body
        outer = tk.Frame(self.root, bg=BG)
        outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer, bg=BG, highlightthickness=0, bd=0)
        scrollbar = tk.Scrollbar(outer, orient="vertical", command=canvas.yview,
                                 bg=BG2, troughcolor=BG)
        self._frame = tk.Frame(canvas, bg=BG, padx=18, pady=10)
        self._frame.bind("<Configure>",
                         lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self._frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        canvas.bind_all("<MouseWheel>",
                        lambda e: canvas.yview_scroll(int(-1*(e.delta//120)), "units"))

        # --- Bottom Buttons ---
        bottom_frame = tk.Frame(self.root, bg=BG)
        bottom_frame.pack(side="bottom", fill="x", pady=20)
        
        btn_restart = tk.Button(bottom_frame, text="🔁 システム再起動", bg="#f59e0b", fg=FG, borderwidth=0,
                                activebackground="#d97706", activeforeground=FG, font=("Meiryo", 10, "bold"),
                                cursor="hand2", padx=20, pady=8, command=self._restart_app)
        btn_restart.pack(side="left", padx=20)
        
        btn_shutdown = tk.Button(bottom_frame, text="❌ システム終了", bg="#ef4444", fg=FG, borderwidth=0,
                                 activebackground="#dc2626", activeforeground=FG, font=("Meiryo", 11, "bold"),
                                 cursor="hand2", padx=20, pady=8, command=self._shutdown)
        btn_shutdown.pack(side="right", padx=20)

        # ── Sections ──────────────────────────────────────────────────

        self._section("表示設定")
        self._checkbox("showOriginal",   "日本語（原文）を表示")
        self._checkbox("showTranslated", "英語（翻訳）を表示")
        self._checkbox("showStatus",     "接続インジケーター")
        self._radio("textAlign",   "水平配置",
                    [("左寄せ", "left"), ("中央", "center"), ("右寄せ", "right")])
        self._radio("verticalPos", "垂直位置",
                    [("上付き", "top"), ("中央", "center_v"), ("下付き", "bottom")])
        self._scale("maxBlocks",       "最大表示ブロック数",       1,   6,   1,
                    fmt=lambda v: f"{int(v)} ブロック")
        self._scale("maxCharsPerLine", "1行最大文字数 (0=無制限)", 0, 160,   5,
                    fmt=lambda v: "無制限" if v == 0 else f"{int(v)} 文字")
        self._scale("displayDuration", "自動非表示時間",           0, 30000, 500,
                    fmt=lambda v: "OFF" if v == 0 else f"{v/1000:.1f} 秒")

        self._section("オーバーレイ表示設定（ゲーム画面等への表示）")
        self._checkbox("overlayEnabled", "ゲーム画面上に翻訳字幕をオーバーレイ表示")
        self._checkbox("overlayDragMode", "字幕の位置を調整する (ドラッグ移動許可)")
        self._radio("overlayBgStyle", "オーバーレイの背景スタイル",
                    [("背景なし(文字のみ)", "text_only"), ("半透明背景あり", "with_bg")])
        self._radio("overlaySize", "オーバーレイの枠幅",
                    [("小(35%)", "small"), ("中(50%)", "medium"), ("大(70%)", "large")])
        self._radio("overlayTransparency", "文字・背景の透明度 (薄さ)",
                    [("0% (はっきり)", "0"), ("30% (少し薄い)", "30"), ("60% (かなり薄い)", "60")])

        self._section("フォント")
        self._font_combobox("fontFamily", "フォント (通常)")
        self._font_combobox("fontFamilySecreAI", "フォント (SecreAI)")
        self._scale("fontSizeOrig",  "日本語サイズ", 12, 120, 2, fmt=lambda v: f"{int(v)} px")
        self._scale("fontSizeTrans", "英語サイズ",   12, 120, 2, fmt=lambda v: f"{int(v)} px")

        self._section("色・縁取り")
        self._color_picker("colorOrig",    "日本語テキスト色 (通常)")
        self._color_picker("colorTrans",   "英語テキスト色 (通常)")
        self._color_picker("colorOrigSecreAI",  "日本語テキスト色 (SecreAI)")
        self._color_picker("colorTransSecreAI", "英語テキスト色 (SecreAI)")
        self._color_picker("outlineColor", "縁取り色")
        self._scale("outlineWidth", "縁取り太さ (0=なし)", 0, 8, 1,
                    fmt=lambda v: "なし" if v == 0 else f"{int(v)} px")

        self._section("タイプライター")
        self._checkbox("typewriterOn", "タイプライターエフェクト")
        self._scale("typewriterSpeed", "文字速度 (ms/文字)", 5, 200, 5,
                    fmt=lambda v: f"{int(v)} ms")

        self._section("システム設定 ★再起動後に有効")
        self._radio("audioCaptureMode", "キャプチャ方式",
                    [("マイク", "microphone"), ("システム音声", "loopback")])
        self._audio_device_area()

        self._create_scale("micSensitivity", "マイク感度 (音量倍率):", 0.1, 5.0, 0.1)
        self._create_scale("vadThreshold", "無音判定レベル (ノイズ除去):", 0.01, 0.99, 0.01)
        self._scale("beamSize", "文字起こしの精度 (Beam Size):", 1, 10, 1, 
                    fmt=lambda v: f"{int(v)}")
        self._llm_settings_area()
        self._language_combobox("sttLanguage", "音声認識の言語", allow_auto=True)
        self._stt_model_combobox()
        self._gpu_device_combobox()
        self._language_combobox("transSourceLang", "翻訳元の言語")
        self._language_combobox("transTargetLang", "翻訳先の言語")

        # Buttons
        btn_f = tk.Frame(self._frame, bg=BG)
        btn_f.pack(fill="x", pady=(18, 8))
        self._mk_btn(btn_f, "テスト送信",  self._send_test,  ACCENT, "white" ).pack(side="left")
        self._mk_btn(btn_f, "字幕クリア",  self._send_clear, ACCENT2,"white" ).pack(side="left", padx=(8, 0))
        self._mk_btn(btn_f, "リセット",    self._reset,      BG2,    MUTED   ).pack(side="left", padx=(8, 0))
        
    # ── Widget factories ──────────────────────────────────────────────

    def _mk_btn(self, parent, text, cmd, bg, fg):
        return tk.Button(parent, text=text, command=cmd,
                         bg=bg, fg=fg, font=("Segoe UI", 10, "bold"),
                         bd=0, padx=14, pady=7, cursor="hand2",
                         activebackground=bg, activeforeground=fg, relief="flat")

    def _section(self, title):
        tk.Label(self._frame, text=title, font=("Segoe UI", 9, "bold"),
                 bg=BG, fg=ACCENT2).pack(anchor="w", pady=(14, 2))
        tk.Frame(self._frame, bg=BORDER, height=1).pack(fill="x", pady=(0, 8))

    def _row(self, label_text) -> tk.Frame:
        row = tk.Frame(self._frame, bg=BG)
        row.pack(fill="x", pady=4)
        tk.Label(row, text=label_text, font=("Segoe UI", 10),
                 bg=BG, fg=FG, anchor="w", width=22).pack(side="left")
        return row

    def _checkbox(self, key, label):
        var = tk.BooleanVar()
        self._vars[key] = var
        row = self._row(label)
        tk.Checkbutton(row, variable=var, bg=BG, fg=FG, selectcolor=ACCENT,
                       activebackground=BG, command=self._on_change).pack(side="left")

    def _llm_settings_area(self):
        """ローカルLLM設定（Ollama / LM Studio 選択式）"""
        import urllib.request
        
        # 1. プロバイダ選択
        prov_var = tk.StringVar()
        self._vars["llmProvider"] = prov_var
        row_prov = self._row("LLM プロバイダ")
        
        # URL配置用のコンテナフレーム（Tkinter pack時の順序崩れ防止）
        url_container = tk.Frame(self._frame, bg=BG)
        url_container.pack(fill="x")
        
        # 2. URL入力欄
        ollama_url_var = tk.StringVar()
        self._vars["ollamaUrl"] = ollama_url_var
        row_ollama = tk.Frame(url_container, bg=BG)
        tk.Label(row_ollama, text="Ollama 接続先", font=("Segoe UI", 10),
                 bg=BG, fg=FG, anchor="w", width=22).pack(side="left")
        ollama_ent = tk.Entry(row_ollama, textvariable=ollama_url_var, bg=BG2, fg=FG, bd=1, insertbackground=FG, font=("Segoe UI", 10))
        ollama_ent.pack(side="left", fill="x", expand=True, padx=(0, 10))
        
        lmstudio_url_var = tk.StringVar()
        self._vars["lmstudioUrl"] = lmstudio_url_var
        row_lmstudio = tk.Frame(url_container, bg=BG)
        tk.Label(row_lmstudio, text="LM Studio 接続先", font=("Segoe UI", 10),
                 bg=BG, fg=FG, anchor="w", width=22).pack(side="left")
        lmstudio_ent = tk.Entry(row_lmstudio, textvariable=lmstudio_url_var, bg=BG2, fg=FG, bd=1, insertbackground=FG, font=("Segoe UI", 10))
        lmstudio_ent.pack(side="left", fill="x", expand=True, padx=(0, 10))

        # 3. AIモデルコンボボックス
        model_var = tk.StringVar()
        self._vars["aiModel"] = model_var
        row_model = self._row("翻訳AIモデル")
        model_cb = ttk.Combobox(row_model, textvariable=model_var, state="normal", width=33)
        model_cb.pack(side="left")
        
        # 4. 「モデル一覧を取得」ボタン
        btn_fetch = tk.Button(row_model, text="モデル取得", bg=ACCENT, fg="white", 
                              font=("Segoe UI", 9, "bold"), bd=0, padx=8, pady=2, cursor="hand2",
                              activebackground=ACCENT, activeforeground="white")
        btn_fetch.pack(side="left", padx=(8, 0))

        # 接続テスト & モデル取得用ヘルパー関数
        def get_ollama_models(url: str) -> list[str]:
            url_resolved = url.replace("localhost", "127.0.0.1")
            try:
                req = urllib.request.Request(f"{url_resolved.rstrip('/')}/api/tags", method="GET")
                with urllib.request.urlopen(req, timeout=3.0) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    return [m["name"] for m in data.get("models", [])]
            except Exception as e:
                logger.warning(f"Ollamaモデル取得失敗: {e}")
            return []

        def get_lmstudio_models(url: str) -> list[str]:
            url_resolved = url.replace("localhost", "127.0.0.1")
            try:
                if not url_resolved.endswith("/models") and not url_resolved.endswith("/models/"):
                    if url_resolved.endswith("/v1") or url_resolved.endswith("/v1/"):
                        api_url = url_resolved.rstrip("/") + "/models"
                    else:
                        api_url = url_resolved.rstrip("/") + "/v1/models"
                else:
                    api_url = url_resolved
                req = urllib.request.Request(api_url, method="GET")
                with urllib.request.urlopen(req, timeout=3.0) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    models = []
                    if "data" in data:
                        for item in data["data"]:
                            if "id" in item:
                                models.append(item["id"])
                    return models
            except Exception as e:
                logger.warning(f"LM Studioモデル取得失敗: {e}")
            return []

        def fetch_models_async():
            btn_fetch.config(text="取得中...", state="disabled")
            prov = prov_var.get()
            url = lmstudio_url_var.get() if prov == "lmstudio" else ollama_url_var.get()

            def _task():
                if prov == "lmstudio":
                    fetched = get_lmstudio_models(url)
                else:
                    fetched = get_ollama_models(url)
                
                def _update_ui():
                    btn_fetch.config(text="モデル取得", state="normal")
                    if fetched:
                        # キャッシュの更新
                        if prov == "lmstudio":
                            self.settings["cachedLMStudioModels"] = fetched
                        else:
                            self.settings["cachedOllamaModels"] = fetched
                        save_settings(self.settings)
                        
                        # コンボボックスの更新
                        model_cb.config(values=fetched)
                        if model_var.get() not in fetched:
                            model_var.set(fetched[0])
                        self._on_change()
                    else:
                        logger.warning("モデル一覧が空、または取得できませんでした。")
                self.root.after(0, _update_ui)
            threading.Thread(target=_task, daemon=True).start()

        btn_fetch.config(command=fetch_models_async)

        # プロバイダ変更によるUI表示の切り替え
        def update_provider_ui(*_):
            prov = prov_var.get()
            if prov == "lmstudio":
                row_ollama.pack_forget()
                row_lmstudio.pack(fill="x", pady=4)
                cached = self.settings.get("cachedLMStudioModels", ["meta-llama-3-8b-instruct", "gemma-2-9b-it", "gemma-3-4b-it"])
            else:
                row_lmstudio.pack_forget()
                row_ollama.pack(fill="x", pady=4)
                cached = self.settings.get("cachedOllamaModels", ["gemma3:4b", "gemma2:9b", "llama3:8b", "phi3:mini"])
            
            # モデルのコンボボックス値キャッシュの切り替え
            model_cb.config(values=cached)
            # 現在の値がキャッシュに入っていなければ1個目にする
            curr = model_var.get()
            if curr not in cached and cached:
                model_var.set(cached[0])
            self._on_change()

        # プロバイダ用のラジオボタンを配置
        for text, value in [("Ollama", "ollama"), ("LM Studio", "lmstudio")]:
            tk.Radiobutton(row_prov, text=text, variable=prov_var, value=value,
                           bg=BG, fg=FG, selectcolor=ACCENT,
                           activebackground=BG, command=update_provider_ui
                           ).pack(side="left", padx=5)

        # 初期値のロード
        prov_var.set(self.settings.get("llmProvider", "ollama"))
        ollama_url_var.set(self.settings.get("ollamaUrl", "http://localhost:11434"))
        lmstudio_url_var.set(self.settings.get("lmstudioUrl", "http://localhost:1234/v1"))
        model_var.set(self.settings.get("aiModel", ""))

        # 初期値設定後にUI表示を反映
        update_provider_ui()

        # UI要素のwriteトレースをバインド (初期値設定が終わった後にバインドすることで無限ループや不要な_on_changeを回避)
        prov_var.trace_add("write", lambda *_: self._on_change())
        ollama_url_var.trace_add("write", lambda *_: self._on_change())
        lmstudio_url_var.trace_add("write", lambda *_: self._on_change())
        model_var.trace_add("write", lambda *_: self._on_change())
        
        # モデル選択イベントのバインド
        model_cb.bind("<<ComboboxSelected>>", lambda _: self._on_change())
        model_cb.bind("<Return>",             lambda _: self._on_change())
        model_cb.bind("<FocusOut>",           lambda _: self._on_change())

    def _radio(self, key, label, options):
        var = tk.StringVar()
        self._vars[key] = var
        row = self._row(label)
        for text, value in options:
            tk.Radiobutton(row, text=text, variable=var, value=value,
                           bg=BG, fg=FG, selectcolor=ACCENT,
                           activebackground=BG, command=self._on_change
                           ).pack(side="left", padx=5)

    def _scale(self, key, label, from_, to, resolution, fmt=None):
        var = tk.DoubleVar()
        self._vars[key] = var
        row = self._row(label)
        val_lbl = tk.Label(row, text="", font=("Segoe UI", 9, "bold"),
                           bg=BG, fg=ACCENT, width=10, anchor="e")
        val_lbl.pack(side="right")

        def _update(*_):
            v = var.get()
            val_lbl.config(text=fmt(v) if fmt else f"{v:.0f}")

        sc = tk.Scale(row, variable=var, from_=from_, to=to, resolution=resolution,
                      orient="horizontal", bg=BG, fg=FG, troughcolor=BG2,
                      highlightthickness=0, showvalue=False,
                      command=lambda _: (self._on_change(), _update()))
        sc.pack(side="left", fill="x", expand=True, padx=(0, 8))
        var.trace_add("write", _update)

    def _font_combobox(self, key, label):
        """Font picker using all Windows-installed fonts."""
        var = tk.StringVar()
        self._vars[key] = var
        row = self._row(label)
        cb = ttk.Combobox(row, textvariable=var, values=self._all_fonts,
                          state="normal", width=28)
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda _: self._on_change())
        cb.bind("<Return>",             lambda _: self._on_change())
        cb.bind("<FocusOut>",           lambda _: self._on_change())

    def _combobox(self, key, label, values, width=28):
        var = tk.StringVar()
        self._vars[key] = var
        row = self._row(label)
        cb = ttk.Combobox(row, textvariable=var, values=values,
                          state="readonly", width=width)
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda _: self._on_change())

    def _audio_device_area(self):
        """Microphone or Loopback device picker area."""
        self._dev_container = tk.Frame(self._frame, bg=BG)
        self._dev_container.pack(fill="x", pady=0)

        # Microphone Row
        self._mic_row = tk.Frame(self._dev_container, bg=BG)
        tk.Label(self._mic_row, text="マイクデバイス", font=("Segoe UI", 10),
                 bg=BG, fg=FG, anchor="w", width=22).pack(side="left")
        
        mic_devices = _get_mic_devices()
        self._vars["audioMicDevice"] = tk.StringVar()
        self._mic_cb = ttk.Combobox(self._mic_row, textvariable=self._vars["audioMicDevice"], 
                                   values=mic_devices, state="readonly", width=33)
        self._mic_cb.pack(side="left")
        self._mic_cb.bind("<<ComboboxSelected>>", lambda _: self._on_change())

        # Speaker Row
        self._spk_row = tk.Frame(self._dev_container, bg=BG)
        tk.Label(self._spk_row, text="スピーカーデバイス", font=("Segoe UI", 10),
                 bg=BG, fg=FG, anchor="w", width=22).pack(side="left")

        spk_devices = _get_speaker_devices()
        self._vars["audioLoopbackDevice"] = tk.StringVar()
        self._spk_cb = ttk.Combobox(self._spk_row, textvariable=self._vars["audioLoopbackDevice"], 
                                   values=spk_devices, state="readonly", width=33)
        self._spk_cb.pack(side="left")
        self._spk_cb.bind("<<ComboboxSelected>>", lambda _: self._on_change())

        # Update visibility initially
        self._update_device_visibility()
        
        # Override _on_change to update visibility
        orig_on_change = self._on_change
        def on_change_with_viz():
            orig_on_change()
            self._update_device_visibility()
        self._vars["audioCaptureMode"].trace_add("write", lambda *_: on_change_with_viz())

    def _update_device_visibility(self):
        mode = self._vars["audioCaptureMode"].get()
        if mode == "loopback":
            self._mic_row.pack_forget()
            self._spk_row.pack(fill="x", pady=4)
        else:
            self._spk_row.pack_forget()
            self._mic_row.pack(fill="x", pady=4)

    def _get_mode_index(self):
        # Helper to find where to re-pack
        return 0 # Placeholder, simpler to just hide/show

    def _audio_device_combobox(self):
        """DEPRECATED - merged into _audio_device_area"""
        pass



    def _language_combobox(self, key: str, label: str, allow_auto: bool = False):
        """Language picker for STT and Translation."""
        langs = ["Japanese", "English", "Chinese", "Korean", "Spanish", "French", "German", "Russian"]
        if allow_auto:
            langs.insert(0, "自動判定 (Auto)")
            
        var = tk.StringVar()
        self._vars[key] = var
        row = self._row(label)
        cb = ttk.Combobox(row, textvariable=var, values=langs,
                          state="readonly", width=33)
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda _: self._on_change())

    def _color_picker(self, key, label):
        var = tk.StringVar()
        self._vars[key] = var
        row = self._row(label)
        preview = tk.Label(row, text="  ", width=3, relief="solid", cursor="hand2")
        preview.pack(side="left", padx=(0, 6))
        hex_lbl = tk.Label(row, text="", font=("Consolas", 9), bg=BG, fg=MUTED)
        hex_lbl.pack(side="left", padx=(0, 8))

        def _update(*_):
            c = var.get()
            try:
                preview.config(bg=c); hex_lbl.config(text=c)
            except Exception:
                pass

        def _pick():
            result = colorchooser.askcolor(color=var.get(), title=label)
            if result[1]:
                var.set(result[1]); self._on_change()

        preview.bind("<Button-1>", lambda _: _pick())
        self._mk_btn(row, "変更", _pick, BG2, MUTED).pack(side="left")
        var.trace_add("write", _update)

    def _create_scale(self, key, label, from_, to, resolution=0.1):
        var = tk.DoubleVar()
        self._vars[key] = var
        row = self._row(label)
        sc = tk.Scale(row, variable=var, from_=from_, to=to, resolution=resolution,
                      orient="horizontal", bg=BG, fg=FG, troughcolor=BG2,
                      highlightthickness=0, showvalue=True, length=200,
                      command=lambda _: self._on_change())
        sc.pack(side="left")

    # ── State management ──────────────────────────────────────────────

    def _load_to_ui(self):
        for key, var in self._vars.items():
            val = self.settings.get(key)
            if val is None or val == "": # Fallback on empty string for essential UI fields
                val = DEFAULTS.get(key, "")
            
            if isinstance(var, tk.BooleanVar):
                var.set(bool(val))
            elif isinstance(var, tk.DoubleVar):
                var.set(float(val))
            else:
                var.set(str(val))

    def _read_from_ui(self) -> dict:
        INT_KEYS = {"maxBlocks", "fontSizeOrig", "fontSizeTrans",
                    "outlineWidth", "typewriterSpeed", "maxCharsPerLine", "displayDuration", "beamSize", "overlayTransparency"}
        result = {}
        for key, var in self._vars.items():
            val = var.get()
            if key in INT_KEYS:
                result[key] = int(float(val))
            elif isinstance(var, tk.BooleanVar):
                result[key] = bool(val)
            else:
                result[key] = val
        result["reconnectInterval"] = 3000
        return result

    def _ws_payload(self, s: dict) -> dict:
        """Build WebSocket payload (exclude local-only keys)."""
        return {k: v for k, v in s.items() if k not in LOCAL_KEYS}

    def _on_change(self):
        if self._loading:
            return
        self.settings = self._read_from_ui()
        save_settings(self.settings)
        # Update overlay window parameters dynamically
        if hasattr(self, "overlay_mgr") and self.overlay_mgr is not None:
            self.overlay_mgr.update_settings(self.settings)
        self._push({"type": "settings_update", "settings": self._ws_payload(self.settings)})
        self._flash_status("設定を送信")

    def _push(self, msg: dict):
        asyncio.run_coroutine_threadsafe(self.queue.put(msg), self.loop)

    def _send_test(self):
        self._push({
            "type":       "test_subtitle",
            "original":   "雀魂で対局中！絶対上がるぞ！",
            "translated": "Playing Mahjong Soul! I'm definitely winning!",
            "source":     "mic"
        })
        self._push({
            "type":       "test_subtitle",
            "original":   "【SecreAIからの入力テスト】",
            "translated": "[Test input from SecreAI]",
            "source":     "secreai"
        })
        self._flash_status("テスト送信済み")

    def _send_clear(self):
        self._push({"type": "clear_subtitles"})
        self._flash_status("字幕クリア")

    def _reset(self):
        self.settings = dict(DEFAULTS)
        save_settings(self.settings)
        self._load_to_ui()
        self._on_change()

    def _shutdown(self):
        """Save and send SIGINT to gracefully terminate the application."""
        logger.info("Saving settings and shutting down...")
        self.settings = self._read_from_ui()
        save_settings(self.settings)
        self.root.destroy()
        import os, signal
        os.kill(os.getpid(), signal.SIGINT)

    def _on_close(self):
        """Handle window 'X' button click."""
        self.settings = self._read_from_ui()
        save_settings(self.settings)
        self.root.destroy()

    def _restart_app(self):
        """Restart the entire application."""
        logger.info("Saving settings and restarting application...")
        self.settings = self._read_from_ui()
        save_settings(self.settings)
        self.root.destroy()
        import subprocess
        executable = sys.executable
        if getattr(sys, 'frozen', False) or getattr(sys, 'compiled', False):
            subprocess.Popen([executable] + sys.argv[1:])
        else:
            subprocess.Popen([executable] + sys.argv)
        import os, signal
        os.kill(os.getpid(), signal.SIGINT)

    def _flash_status(self, msg: str):
        self._status_lbl.config(text=msg)
        self.root.after(1800, lambda: self._status_lbl.config(text=""))

    def _poll_status_queue(self):
        if self.status_queue is not None:
            while not self.status_queue.empty():
                try:
                    msg = self.status_queue.get_nowait()
                    msg_type = msg.get("type")
                    status = msg.get("status")
                    
                    if msg_type == "mic":
                        mode = msg.get("mode", "microphone")
                        if mode == "loopback":
                            self._mic_ind.config(text=" ● システム音声 ")
                        else:
                            self._mic_ind.config(text=" ● マイク ")

                        if status == "active":
                            self._mic_ind.config(bg="#10b981", fg="white") # Emerald-500
                        else:
                            self._mic_ind.config(bg=BG2, fg=MUTED)
                    elif msg_type == "translation":
                        if status == "active":
                            self._ai_ind.config(bg="#f59e0b", fg="white") # Amber-500
                        else:
                            self._ai_ind.config(bg=BG2, fg=MUTED)
                    elif msg_type == "overlay_subtitle":
                        source = msg.get("source", "mic")
                        if source == "mic":
                            translated_text = msg.get("translated", "")
                            if hasattr(self, "overlay_mgr") and self.overlay_mgr is not None:
                                self.overlay_mgr.update_text(translated_text)
                except Exception:
                    pass
                    
        # Update overlay window's own mainloop step
        if hasattr(self, "overlay_mgr") and self.overlay_mgr is not None:
            self.overlay_mgr.run_step()
            
        self.root.after(100, self._poll_status_queue)

    def _stt_model_combobox(self):
        """STT Model picker (dynamic options based on language)."""
        var = tk.StringVar()
        self._vars["sttModel"] = var
        
        self._stt_row = self._row("音声認識モデル (STT)")
        self._stt_cb = ttk.Combobox(self._stt_row, textvariable=var,
                                    state="readonly", width=33)
        self._stt_cb.pack(side="left")
        self._stt_cb.bind("<<ComboboxSelected>>", lambda _: self._on_change())
        
        self._update_stt_model_options()
        
        # sttLanguageの変更を追従
        self._vars["sttLanguage"].trace_add("write", lambda *_: self._update_stt_model_options())
        
    def _update_stt_model_options(self):
        lang = self._vars["sttLanguage"].get()
        
        ja_models = [
            "large-v3-turbo (標準・高速高精度)",
            "small (標準・軽量)",
            "large-v3 (標準・超高精度)",
            "Kotoba-Whisper v2.2 (日本語特化・有志版)",
            "Kotoba-Whisper v2.0 (日本語特化・公式)"
        ]
        other_models = [
            "large-v3-turbo (標準・高速高精度)",
            "small (標準・軽量)",
            "large-v3 (標準・超高精度)"
        ]
        
        current_val = self._vars["sttModel"].get()
        
        if "Japanese" in lang:
            self._stt_cb.config(values=ja_models)
            # 値が空か選択肢にない場合、推奨される v2.2 を自動セット
            if current_val not in ja_models:
                self._vars["sttModel"].set("Kotoba-Whisper v2.2 (日本語特化・有志版)")
        else:
            self._stt_cb.config(values=other_models)
            # 日本語特化モデルが選ばれていた場合は、標準モデルに強制切り替え
            if "Kotoba-Whisper" in current_val or current_val not in other_models:
                self._vars["sttModel"].set("large-v3-turbo (標準・高速高精度)")

    def _gpu_device_combobox(self):
        """GPU device picker for STT."""
        gpus = _get_gpu_devices()
        var = tk.StringVar()
        self._vars["sttGpuDevice"] = var
        
        self._gpu_row = self._row("使用するGPU (STT用)")
        self._gpu_cb = ttk.Combobox(self._gpu_row, textvariable=var, values=gpus,
                                    state="readonly", width=33)
        self._gpu_cb.pack(side="left")
        self._gpu_cb.bind("<<ComboboxSelected>>", lambda _: self._on_change())
        
        # GPUが1つ以下の場合は選択の余地がないため無効化
        if len(gpus) <= 1:
            self._gpu_cb.config(state="disabled")
            self._vars["sttGpuDevice"].set(gpus[0])

    def run(self):
        self.root.mainloop()


# ── Entry point ────────────────────────────────────────────────────────

def start_settings_window(settings_queue: asyncio.Queue,
                           loop: asyncio.AbstractEventLoop,
                           status_queue=None) -> threading.Thread:
    def _run():
        win = SettingsWindow(settings_queue, loop, status_queue)
        win.run()

    t = threading.Thread(target=_run, daemon=True, name="SettingsUI")
    t.start()
    return t
