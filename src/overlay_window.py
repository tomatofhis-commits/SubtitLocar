"""
overlay_window.py - Custom Overlay Window for displaying translated subtitles.
Runs in a separate thread/loop and draws text on top of all windows (click-through).
Uses a double-window design to allow independent opacity for background and text.
"""

import sys
import os
import ctypes
import logging
import tkinter as tk
from tkinter import font as tkfont
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Win32 API Constants
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x80000
WS_EX_TRANSPARENT = 0x20
HWND_TOPMOST = -1
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040

def init_dpi_awareness():
    """Ensure crisp text on high-DPI monitors."""
    try:
        if sys.platform == "win32":
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception as e:
        logger.debug(f"Failed to set DPI awareness: {e}")

def get_hwnd(widget: tk.Widget) -> int:
    """Get Windows HWND for a Tkinter widget."""
    widget.update_idletasks()
    hwnd = ctypes.windll.user32.GetParent(widget.winfo_id())
    if not hwnd:
        hwnd = widget.winfo_id()
    return hwnd

def apply_click_through(hwnd: int, enabled: bool = True):
    """Make the window transparent to mouse clicks (click-through)."""
    try:
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if enabled:
            style |= (WS_EX_LAYERED | WS_EX_TRANSPARENT)
        else:
            style &= ~WS_EX_TRANSPARENT
        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
    except Exception as e:
        logger.error(f"Failed to apply click-through: {e}")

def force_topmost(hwnd: int):
    """Ensure the window remains topmost without stealing focus."""
    try:
        ctypes.windll.user32.SetWindowPos(
            hwnd, HWND_TOPMOST, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW
        )
    except Exception as e:
        logger.error(f"Failed to set topmost: {e}")


class OverlayWindowManager:
    """
    Manages the overlay subtitle display on the desktop.
    Uses two stacked windows:
    1. BackgroundWindow (for translucent dark background)
    2. TextWindow (for crisp, solid/semi-solid text)
    """
    
    def __init__(self, font_family: str = "Noto Sans JP"):
        init_dpi_awareness()
        
        self.font_family = font_family
        self.current_text = ""
        
        # Default settings (will be updated dynamically by settings_ui/main)
        self.enabled = False
        self.size_mode = "medium"      # small / medium / large
        self.bg_style = "with_bg"      # text_only / with_bg
        self.transparency_level = 0    # 0 (0%), 30 (30%), 60 (60%)
        
        # Position cache: Base bottom-center position
        # X: center of screen, Y: 85% down the screen (default)
        self.screen_w = 1920
        self.screen_h = 1080
        self.base_x = 0
        self.base_y = 0
        
        # UI components
        self.root = None
        self.bg_win = None
        self.text_win = None
        
        # Text label and configuration
        self.label = None
        
        # Coordinates for dragging (when settings window is active)
        self.drag_start_x = 0
        self.drag_start_y = 0
        self.is_dragging = False
        
        self._setup_windows()
        
    def _setup_windows(self):
        """Create Tkinter root and the dual windows."""
        self.root = tk.Tk()
        self.root.withdraw()  # Hide main root window
        
        # Retrieve primary screen dimensions
        self.screen_w = self.root.winfo_screenwidth()
        self.screen_h = self.root.winfo_screenheight()
        
        # Default positioning (bottom-center)
        self.base_x = self.screen_w // 2
        self.base_y = int(self.screen_h * 0.85)
        
        # 1. Background Window
        self.bg_win = tk.Toplevel(self.root)
        self.bg_win.overrideredirect(True)
        self.bg_win.configure(bg="black")
        self.bg_win.wm_attributes("-topmost", True)
        self.bg_win.withdraw()
        
        # 2. Text Window
        self.text_win = tk.Toplevel(self.root)
        self.text_win.overrideredirect(True)
        self.text_win.configure(bg="#000100") # Custom transparent color key
        self.text_win.wm_attributes("-transparentcolor", "#000100")
        self.text_win.wm_attributes("-topmost", True)
        
        # Set up text rendering label
        self.label = tk.Label(
            self.text_win,
            text="",
            fg="white",
            bg="#000100",
            justify="center",
            anchor="center"
        )
        self.label.pack(fill="both", expand=True)
        self.text_win.withdraw()
        
        # Apply Windows API tweaks (click-through and topmost force)
        self.bg_hwnd = get_hwnd(self.bg_win)
        self.text_hwnd = get_hwnd(self.text_win)
        
        apply_click_through(self.bg_hwnd, True)
        apply_click_through(self.text_hwnd, True)
        
        # Bind drag events to the text label for position configuration
        self.label.bind("<Button-1>", self._start_drag)
        self.label.bind("<B1-Motion>", self._on_drag)
        self.label.bind("<ButtonRelease-1>", self._stop_drag)
        
    def _start_drag(self, event):
        if not self.is_draggable():
            return
        self.is_dragging = True
        self.drag_start_x = event.x_root
        self.drag_start_y = event.y_root
        
    def _on_drag(self, event):
        if not self.is_dragging:
            return
        dx = event.x_root - self.drag_start_x
        dy = event.y_root - self.drag_start_y
        
        # Update bottom-center position
        self.base_x += dx
        self.base_y += dy
        
        self.drag_start_x = event.x_root
        self.drag_start_y = event.y_root
        
        # Re-render to position correctly during drag
        self.update_text(self.current_text)
        
    def _stop_drag(self, event):
        self.is_dragging = False
        # Save positions to settings.json
        self.save_position()
        
    def is_draggable(self) -> bool:
        """Window is draggable only when UI settings allow it (e.g. settings window open)."""
        # Handled externally by settings_ui setting lock/unlock
        return getattr(self, "draggable_mode", False)
        
    def set_draggable_mode(self, enabled: bool):
        """Toggle whether overlay is interactive for position adjustment."""
        self.draggable_mode = enabled
        if enabled:
            # Disable click-through so user can drag it
            apply_click_through(self.text_hwnd, False)
            # Give a visual indication (e.g. highlight color)
            self.label.configure(bg="#112233")
            self.text_win.wm_attributes("-transparentcolor", "") # Temporarily disable transparent key to see boundaries
            self.update_text("【ドラッグして字幕位置を調整できます】\nDrag to reposition overlay")
        else:
            apply_click_through(self.text_hwnd, True)
            self.label.configure(bg="#000100")
            self.text_win.wm_attributes("-transparentcolor", "#000100")
            self.update_text(self.current_text)

    def load_position(self, x: int, y: int):
        """Set position from saved settings."""
        self.base_x = x
        self.base_y = y
        self.update_text(self.current_text)

    def save_position(self):
        """Triggered to notify settings structure that base coordinates changed."""
        # This will be bound to callback to settings system
        if hasattr(self, "on_position_changed"):
            self.on_position_changed(self.base_x, self.base_y)

    def update_settings(self, settings: dict):
        """Update display properties dynamically from settings."""
        self.enabled = settings.get("overlayEnabled", False)
        self.size_mode = settings.get("overlaySize", "medium")
        self.bg_style = settings.get("overlayBgStyle", "with_bg")
        self.transparency_level = int(settings.get("overlayTransparency", 0))
        
        # Load saved coordinates if present
        saved_x = settings.get("overlayX")
        saved_y = settings.get("overlayY")
        if saved_x is not None and saved_y is not None:
            self.base_x = int(saved_x)
            self.base_y = int(saved_y)
            
        # Refresh current view
        if self.enabled:
            self.update_text(self.current_text)
        else:
            self.hide()

    def hide(self):
        """Hide both windows."""
        if self.bg_win:
            self.bg_win.withdraw()
        if self.text_win:
            self.text_win.withdraw()

    def show(self):
        """Show both windows."""
        if not self.enabled:
            return
        if self.bg_win and self.bg_style == "with_bg":
            self.bg_win.deiconify()
            force_topmost(self.bg_hwnd)
        if self.text_win:
            self.text_win.deiconify()
            force_topmost(self.text_hwnd)

    def get_max_width_and_font(self) -> Tuple[int, int]:
        """Determine width of box and font size based on size mode."""
        # 1. Box width (Percentage of monitor width)
        if self.size_mode == "small":
            width_pct = 0.35
            base_font_size = 24
        elif self.size_mode == "large":
            width_pct = 0.70
            base_font_size = 48
        else: # medium (default)
            width_pct = 0.50
            base_font_size = 36
            
        width = int(self.screen_w * width_pct)
        return width, base_font_size

    def calculate_fitting_text(self, text: str, max_w: int, base_font_size: int) -> Tuple[str, int]:
        """
        Fits text to 3 lines maximum.
        Automatically wraps text, and if it overflows 3 lines,
        reduces font size dynamically until it fits.
        """
        size = base_font_size
        wrapped_text = ""
        
        while size >= 12:
            test_font = tkfont.Font(family=self.font_family, size=size)
            
            # Simple greedy wrapping algorithm
            lines = []
            words = text.strip()
            current_line = ""
            
            for char in words:
                # Handle manual newlines if present
                if char == "\n":
                    lines.append(current_line)
                    current_line = ""
                    continue
                    
                test_line = current_line + char
                # Measure width of current line
                line_w = test_font.measure(test_line)
                if line_w <= max_w - 40: # Subtract padding
                    current_line = test_line
                else:
                    lines.append(current_line)
                    current_line = char
            if current_line:
                lines.append(current_line)
                
            # Check if lines fit in 3 lines
            if len(lines) <= 3:
                wrapped_text = "\n".join(lines)
                break
                
            # If it exceeds 3 lines, reduce font size and retry
            size -= 2
            
        # Fallback if text is extremely long and size hits floor
        if size < 12 and len(lines) > 3:
            wrapped_text = "\n".join(lines[:3]) # Truncate to 3 lines
            
        return wrapped_text, size

    def update_text(self, text: str):
        """Update the displayed text and adjust geometry accordingly."""
        self.current_text = text
        
        if not self.enabled:
            self.hide()
            return
            
        if not text:
            self.hide()
            return

        # 1. Determine width and base font size
        max_width, base_font_size = self.get_max_width_and_font()
        
        # 2. Get wrapped text and fitted font size
        display_text, font_size = self.calculate_fitting_text(text, max_width, base_font_size)
        
        # 3. Configure text Label
        use_font = tkfont.Font(family=self.font_family, size=font_size, weight="bold")
        self.label.configure(text=display_text, font=use_font)
        
        # 4. Measure size needed for the label
        # Add some padding
        line_space = use_font.metrics("linespace")
        line_count = len(display_text.split("\n"))
        
        actual_h = (line_space * line_count) + 20
        actual_w = max_width
        
        # 5. Position calculations (Bottom-anchored, growing upwards)
        # base_x is the horizontal center, base_y is the bottom edge
        left_x = self.base_x - (actual_w // 2)
        top_y = self.base_y - actual_h
        
        # Boundary safety check (don't push off-screen)
        left_x = max(0, min(left_x, self.screen_w - actual_w))
        top_y = max(0, min(top_y, self.screen_h - actual_h))
        
        geom_str = f"{actual_w}x{actual_h}+{left_x}+{top_y}"
        
        # 6. Apply Geometry to windows
        self.text_win.geometry(geom_str)
        self.bg_win.geometry(geom_str)
        
        # 7. Apply alpha (transparency mapping)
        # Default: text opacity 100%, bg opacity 50%
        text_alpha = 1.0
        bg_alpha = 0.5
        
        if self.transparency_level == 30:
            text_alpha = 0.7
            bg_alpha = 0.25
        elif self.transparency_level == 60:
            text_alpha = 0.4
            bg_alpha = 0.10
            
        self.text_win.attributes("-alpha", text_alpha)
        self.bg_win.attributes("-alpha", bg_alpha)
        
        # 8. Show Windows
        self.show()

    def run_step(self):
        """Run Tkinter loop non-blocking step by calling update."""
        if self.root:
            try:
                self.root.update()
            except Exception:
                pass


# ── Simple testing environment ───────────────────────────────────────
if __name__ == "__main__":
    import time
    # Test stub
    logging.basicConfig(level=logging.DEBUG)
    overlay = OverlayWindowManager()
    overlay.enabled = True
    overlay.bg_style = "with_bg"
    overlay.size_mode = "medium"
    
    overlay.update_text("これはゲーム画面用字幕オーバーレイのテスト表示です。\n最大3行まで自動で改行され、はみ出す場合は自動縮小されます。\n下辺の位置を基準にして上に向かって伸びます。")
    
    print("Testing overlay... Ctrl+C to stop.")
    try:
        while True:
            overlay.run_step()
            time.sleep(0.02)
    except KeyboardInterrupt:
        print("Test stopped.")
