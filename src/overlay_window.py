"""
overlay_window.py - Custom Overlay Window for displaying translated subtitles.
Runs in a separate thread/loop and draws text on top of all windows (click-through).
Uses a double-window design to allow independent opacity for background and text.
Draws outlined text using tk.Canvas to match OBS subtitle styles.
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
SWP_NOZORDER = 0x0004
SWP_FRAMECHANGED = 0x0020

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
        # Force Windows to apply style changes immediately
        ctypes.windll.user32.SetWindowPos(
            hwnd, 0, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED | SWP_NOACTIVATE
        )
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
    2. TextWindow (for crisp, solid/semi-solid text with outline)
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
        
        # Text Styles (Synced with OBS settings)
        self.text_color = "#ffffff"
        self.outline_color = "#000000"
        self.outline_width = 2
        
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
        self.canvas = None
        
        # Coordinates for dragging
        self.drag_start_x = 0
        self.drag_start_y = 0
        self.is_dragging = False
        self.draggable_mode = False
        
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
        
        # Set up Canvas for outlined text rendering
        self.canvas = tk.Canvas(
            self.text_win,
            bg="#000100",
            highlightthickness=0,
            bd=0
        )
        self.canvas.pack(fill="both", expand=True)
        self.text_win.withdraw()
        
        # Apply Windows API tweaks (click-through and topmost force)
        self.bg_hwnd = get_hwnd(self.bg_win)
        self.text_hwnd = get_hwnd(self.text_win)
        
        apply_click_through(self.bg_hwnd, True)
        apply_click_through(self.text_hwnd, True)
        
        # Bind drag events to the canvas for position configuration
        self.canvas.bind("<Button-1>", self._start_drag)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._stop_drag)
        
    def _start_drag(self, event):
        if not self.draggable_mode:
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
        
    def set_draggable_mode(self, enabled: bool):
        """Toggle whether overlay is interactive for position adjustment."""
        self.draggable_mode = enabled
        if enabled:
            # Disable click-through so user can drag it
            apply_click_through(self.text_hwnd, False)
            # Temporarily disable transparent color key to visualize drag boundaries
            self.text_win.wm_attributes("-transparentcolor", "")
            self.canvas.configure(bg="#22263a") # Muted UI background color
            self.update_text("【ドラッグして字幕位置を調整できます】\nDrag this box to reposition overlay")
        else:
            apply_click_through(self.text_hwnd, True)
            # Restore transparent color key
            self.canvas.configure(bg="#000100")
            self.text_win.wm_attributes("-transparentcolor", "#000100")
            self.update_text(self.current_text)

    def load_position(self, x: int, y: int):
        """Set position from saved settings."""
        self.base_x = x
        self.base_y = y
        self.update_text(self.current_text)

    def save_position(self):
        """Triggered to notify settings structure that base coordinates changed."""
        if hasattr(self, "on_position_changed"):
            self.on_position_changed(self.base_x, self.base_y)

    def update_settings(self, settings: dict):
        """Update display properties dynamically from settings."""
        self.enabled = settings.get("overlayEnabled", False)
        self.size_mode = settings.get("overlaySize", "medium")
        self.bg_style = settings.get("overlayBgStyle", "with_bg")
        self.transparency_level = int(settings.get("overlayTransparency", 0))
        
        # Sync Font & Text Styles with OBS options
        self.font_family = settings.get("fontFamily", "Noto Sans JP")
        self.text_color = settings.get("colorTrans", "#ffffff")
        self.outline_color = settings.get("outlineColor", "#000000")
        self.outline_width = int(settings.get("outlineWidth", 2))
        self.base_font_size = int(settings.get("fontSizeTrans", 46))
        
        # Set drag mode
        drag_enabled = settings.get("overlayDragMode", False)
        
        # Load saved coordinates if present
        saved_x = settings.get("overlayX")
        saved_y = settings.get("overlayY")
        if saved_x is not None and saved_y is not None:
            self.base_x = int(saved_x)
            self.base_y = int(saved_y)
            
        # Update draggable mode state
        if drag_enabled != self.draggable_mode:
            self.set_draggable_mode(drag_enabled)
        else:
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
        # Background screen overlay
        if self.bg_win and self.bg_style == "with_bg" and not self.draggable_mode:
            self.bg_win.deiconify()
            force_topmost(self.bg_hwnd)
        else:
            # Hide background during dragging to avoid visual clutter
            self.bg_win.withdraw()
            
        if self.text_win:
            self.text_win.deiconify()
            force_topmost(self.text_hwnd)

    def get_max_width(self) -> int:
        """Determine width of box based on size mode."""
        if self.size_mode == "small":
            width_pct = 0.35
        elif self.size_mode == "large":
            width_pct = 0.70
        else: # medium (default)
            width_pct = 0.50
            
        return int(self.screen_w * width_pct)

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
                if char == "\n":
                    lines.append(current_line)
                    current_line = ""
                    continue
                    
                test_line = current_line + char
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
            
        if size < 12 and len(lines) > 3:
            wrapped_text = "\n".join(lines[:3]) # Truncate to 3 lines
            
        return wrapped_text, size

    def update_text(self, text: str):
        """Update the displayed text on the canvas and adjust geometry accordingly."""
        self.current_text = text
        
        if not self.enabled:
            self.hide()
            return
            
        if not text:
            self.hide()
            return

        # 1. Determine width
        max_width = self.get_max_width()
        
        # 2. Get wrapped text and fitted font size
        display_text, font_size = self.calculate_fitting_text(text, max_width, self.base_font_size)
        
        # 3. Configure font properties
        use_font = tkfont.Font(family=self.font_family, size=font_size, weight="bold")
        
        # 4. Measure size needed for the label
        line_space = use_font.metrics("linespace")
        line_count = len(display_text.split("\n"))
        
        actual_h = (line_space * line_count) + 20
        actual_w = max_width
        
        # 5. Position calculations (Bottom-anchored, growing upwards)
        left_x = self.base_x - (actual_w // 2)
        top_y = self.base_y - actual_h
        
        # Boundary safety check (don't push off-screen)
        left_x = max(0, min(left_x, self.screen_w - actual_w))
        top_y = max(0, min(top_y, self.screen_h - actual_h))
        
        geom_str = f"{actual_w}x{actual_h}+{left_x}+{top_y}"
        
        # 6. Apply Geometry to windows
        self.text_win.geometry(geom_str)
        self.bg_win.geometry(geom_str)
        
        # 7. Redraw Canvas text with outline (OBS style)
        self.canvas.delete("all")
        center_x = actual_w // 2
        center_y = actual_h // 2
        
        text_color_to_use = self.text_color
        outline_color_to_use = self.outline_color
        
        # In drag adjustment mode, use prominent colors for visibility
        if self.draggable_mode:
            text_color_to_use = "#ffe066" # Bright yellow
            outline_color_to_use = "#000000"
        
        # Draw outlines by offsetting in 8 directions (grid)
        if self.outline_width > 0 and not self.draggable_mode:
            w = self.outline_width
            for dx in range(-w, w + 1):
                for dy in range(-w, w + 1):
                    if dx == 0 and dy == 0:
                        continue
                    self.canvas.create_text(
                        center_x + dx, center_y + dy,
                        text=display_text,
                        font=use_font,
                        fill=outline_color_to_use,
                        anchor="center",
                        justify="center"
                    )
                    
        # Draw main foreground text
        self.canvas.create_text(
            center_x, center_y,
            text=display_text,
            font=use_font,
            fill=text_color_to_use,
            anchor="center",
            justify="center"
        )
        
        # 8. Apply alpha (transparency mapping)
        text_alpha = 1.0
        bg_alpha = 0.5
        
        if self.transparency_level == 30:
            text_alpha = 0.7
            bg_alpha = 0.25
        elif self.transparency_level == 60:
            text_alpha = 0.4
            bg_alpha = 0.10
            
        # During drag, force solid window visibility
        if self.draggable_mode:
            text_alpha = 1.0
            
        self.text_win.attributes("-alpha", text_alpha)
        self.bg_win.attributes("-alpha", bg_alpha)
        
        # 9. Show Windows
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
