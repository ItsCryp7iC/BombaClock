import sys
import time
import threading
import json
from pathlib import Path
import os
import numpy as np
import cv2
import mss
import keyboard
from PyQt6.QtWidgets import (QApplication, QLabel, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QPushButton, QTextEdit, QFrame, QGraphicsOpacityEffect,
                             QColorDialog, QGroupBox, QFormLayout, QGraphicsDropShadowEffect, QSizePolicy,
                             QSlider, QStatusBar)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QPropertyAnimation, QEasingCurve, QPoint
from PyQt6.QtGui import QFont, QColor

# --- DEFAULT CONFIGURATION ---
DEFAULT_CONFIG = {
    "active_game": "Valorant",
    "Valorant": {
        "monitor_region": {"top": 15, "left": 921, "width": 81, "height": 78},
        "visual_confidence": 0.8,
        "spike_duration": 45,
        "defuse_warning_time": 7
    },
    "CS2": {
        "monitor_region": {"top": 0, "left": 913, "width": 94, "height": 46},
        "visual_confidence": 0.8,
        "spike_duration": 40,
        "defuse_warning_time": 10
    },
    "global_settings": {
        "manual_adjustment_ms": 100,
        "timer_font_size": 40,
        "timer_opacity": 0.75,
        "gui_opacity": 0.9,
        "gui_color": "#002e83",
        "timer_colors": {
            "normal": "#FFFFFF", "mid": "#FFFF00", "warn": "#FFA500", "danger": "#FF0000"
        },
        "key_shortcuts": {
            "timer_up": "page up", "timer_down": "page down", "timer_stop": "home" 
        }
    }
}

# --- Utility Functions ---
def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

def load_config():
    config_path = Path("config.json")
    if config_path.exists():
        with open(config_path, 'r') as f:
            try:
                user_config = json.load(f)
                for key, value in DEFAULT_CONFIG.items():
                    if isinstance(value, dict):
                        user_config.setdefault(key, {})
                        for sub_key, sub_value in value.items():
                            user_config[key].setdefault(sub_key, sub_value)
                    else:
                        user_config.setdefault(key, value)
                return user_config
            except json.JSONDecodeError: pass
    save_config(DEFAULT_CONFIG)
    return DEFAULT_CONFIG

def save_config(config_data):
    with open("config.json", 'w') as f:
        json.dump(config_data, f, indent=4)

config = load_config()

class VisualListener(QThread):
    spike_detected = pyqtSignal(float)
    critical_error = pyqtSignal(str)
    log_message = pyqtSignal(str)
    update_confidence = pyqtSignal(float)

    def __init__(self):
        super().__init__()
        self.debug_mode = False
        self.debug_window_active = False
        self.running = True
        self.load_template()

    def load_template(self):
        game = config['active_game']
        template_filename = 'valorant_spike.png' if game == 'Valorant' else 'cs2_c4.png'
        template_path = resource_path(template_filename)
        
        template_rgba = cv2.imread(template_path, cv2.IMREAD_UNCHANGED)
        if template_rgba is None:
            raise FileNotFoundError(f"Could not load template image at: {template_path}")
        if len(template_rgba.shape) < 3 or template_rgba.shape[2] != 4:
            self.template = cv2.cvtColor(template_rgba, cv2.COLOR_BGR2RGB)
            self.mask = None
        else:
            self.template = template_rgba[:, :, :3]
            self.mask = template_rgba[:, :, 3]
        self.th, self.tw = self.template.shape[:2]

    def run(self):
        self.log_message.emit(f"Listener started for {config['active_game']}. Scanning for bomb plant...")
        with mss.mss() as sct:
            while self.running:
                try:
                    game_config = config[config['active_game']]
                    img = sct.grab(game_config["monitor_region"])
                    
                    screen_bgr = np.array(img)[:, :, :3]
                    
                    sh, sw = screen_bgr.shape[:2]
                    if self.th > sh or self.tw > sw:
                        error_msg = (f"ERROR: Template image ({self.tw}x{self.th}) is larger than "
                                     f"the capture region ({sw}x{sh}) for {config['active_game']}. "
                                     "Please use the 'Calibrate' button.")
                        self.critical_error.emit(error_msg)
                        self.stop()
                        break

                    res = cv2.matchTemplate(screen_bgr, self.template, cv2.TM_CCOEFF_NORMED, mask=self.mask)
                    _, max_val, _, max_loc = cv2.minMaxLoc(res)

                    self.update_confidence.emit(max_val)

                    if self.debug_mode:
                        debug_screen = screen_bgr.copy()
                        if max_val > 0.4:
                            cv2.rectangle(debug_screen, max_loc, (max_loc[0] + self.tw, max_loc[1] + self.th), (0, 255, 0), 2)
                        cv2.imshow("Debug - Live Capture", debug_screen)
                        cv2.waitKey(1)
                        self.debug_window_active = True
                    elif self.debug_window_active:
                        cv2.destroyAllWindows()
                        self.debug_window_active = False

                    if max_val > game_config["visual_confidence"]:
                        self.spike_detected.emit(max_val)
                        time.sleep(1) 

                except Exception as e:
                    self.log_message.emit(f"Error in visual listener: {e}")
                    time.sleep(2)
                time.sleep(0.25)
        if self.debug_window_active:
            cv2.destroyAllWindows()

    def set_debug_mode(self, is_enabled):
        self.debug_mode = is_enabled

    def stop(self):
        self.running = False

class TimerLogic(QThread):
    update_timer_display = pyqtSignal(str, str)
    timer_finished = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.game_config = config[config['active_game']]
        self.time_left = float(self.game_config["spike_duration"])
        self.lock = threading.Lock()
        self.running = True

    def run(self):
        last_displayed_time = -1
        while self.running:
            with self.lock:
                if self.time_left < 0: break
                current_display_time = int(round(self.time_left))
            if current_display_time != last_displayed_time:
                self.update_timer_display.emit(str(current_display_time), self.get_color_for_time(current_display_time))
                last_displayed_time = current_display_time
            time.sleep(0.05)
            with self.lock:
                self.time_left -= 0.05
        if self.running:
            self.timer_finished.emit()

    def adjust_time(self, amount):
        with self.lock:
            self.time_left = max(0.0, min(float(self.game_config["spike_duration"]), self.time_left + amount))

    def get_color_for_time(self, seconds):
        colors = config["global_settings"]["timer_colors"]
        if seconds <= self.game_config["defuse_warning_time"]: return colors["danger"]
        elif seconds <= 10: return colors["warn"]
        elif seconds <= 20: return colors["mid"]
        else: return colors["normal"]
        
    def stop(self):
        self.running = False

class GlobalHotkeyListener(QThread):
    hotkey_triggered = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.running = True
        self.hotkeys = {}
        self.register_hotkeys()

    def register_hotkeys(self):
        for key in self.hotkeys:
            keyboard.remove_hotkey(key)
        self.hotkeys.clear()
        shortcuts = config["global_settings"]["key_shortcuts"]
        try:
            self.hotkeys['up'] = keyboard.add_hotkey(shortcuts["timer_up"], lambda: self.hotkey_triggered.emit("up"))
            self.hotkeys['down'] = keyboard.add_hotkey(shortcuts["timer_down"], lambda: self.hotkey_triggered.emit("down"))
            self.hotkeys['stop'] = keyboard.add_hotkey(shortcuts["timer_stop"], lambda: self.hotkey_triggered.emit("stop"))
        except Exception as e:
            print(f"Could not register hotkeys: {e}")
        
    def run(self):
        while self.running:
            time.sleep(1)

    def stop(self):
        self.running = False
        for key in self.hotkeys:
            keyboard.remove_hotkey(key)

class TimerOverlayWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setGeometry(100, 100, 450, 150)
        self.setWindowOpacity(config['global_settings']['timer_opacity'])

        layout = QVBoxLayout(self)
        self.timer_label = QLabel("WAITING", self)
        self.font = QFont("Arial", config['global_settings']['timer_font_size'], QFont.Weight.Bold)
        self.timer_label.setFont(self.font)
        self.timer_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        shadow_effect = QGraphicsDropShadowEffect(self)
        shadow_effect.setBlurRadius(3)
        shadow_effect.setOffset(2, 2)
        shadow_effect.setColor(QColor(0, 0, 0, 200))
        self.timer_label.setGraphicsEffect(shadow_effect)
        
        layout.addWidget(self.timer_label)
        self.reset()

    def update_display(self, time_str, color_hex):
        self.timer_label.setText(time_str)
        self.timer_label.setStyleSheet(f"color: {color_hex};")
        
    def set_font_size(self, size):
        self.font.setPointSize(size)
        self.timer_label.setFont(self.font)

    def reset(self):
        self.update_display("WAITING", config["global_settings"]["timer_colors"]["normal"])

    def mousePressEvent(self, event): self.old_pos = event.globalPosition().toPoint()
    def mouseMoveEvent(self, event):
        delta = event.globalPosition().toPoint() - self.old_pos
        self.move(self.x() + delta.x(), self.y() + delta.y())
        self.old_pos = event.globalPosition().toPoint()

class SettingsWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.is_timer_active = False
        self.key_listen_button = None
        self.timer_overlay = TimerOverlayWindow()
        self.initUI()
        self.setup_listeners()
        self.timer_overlay.show()

    def initUI(self):
        self.setGeometry(150, 150, 450, 680)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowOpacity(config['global_settings']['gui_opacity'])
        self.update_stylesheet()

        central_widget = QWidget()
        central_widget.setObjectName("CentralWidget")
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.create_title_bar(main_layout)
        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(10, 10, 10, 10)
        self.create_game_select_panel(content_layout)
        self.create_settings_panel(content_layout)
        self.create_log_panel(content_layout)
        main_layout.addLayout(content_layout)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.confidence_label = QLabel("Confidence: N/A")
        self.status_bar.addPermanentWidget(self.confidence_label)

    def update_stylesheet(self):
        self.setStyleSheet(f"""
            #CentralWidget {{ background-color: {config['global_settings']['gui_color']}; border-radius: 10px; }}
            QWidget {{ color: #ecf0f1; }}
            QGroupBox {{ font-weight: bold; border: 1px solid #4a6278; margin-top: 10px; border-radius: 5px;}}
            QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 5px 0 5px; }}
            QTextEdit {{ background-color: #34495e; color: #ecf0f1; border: 1px solid #2c3e50; border-radius: 3px; }}
            QStatusBar {{ background-color: {config['global_settings']['gui_color']}; border-top: 1px solid #4a6278; }}
            #TitleBar QPushButton {{ background-color: transparent; border: none; font-size: 14px; width: 30px; height: 30px; }}
            #TitleBar QPushButton:hover {{ background-color: #4a6278; }}
            #SettingsPanel QPushButton, #GameSelectPanel QPushButton {{ background-color: #34495e; border: 1px solid #4a6278; padding: 5px; border-radius: 3px;}}
            #SettingsPanel QPushButton:hover, #GameSelectPanel QPushButton:hover {{ background-color: #4a6278; }}
            #KeybindBtn:focus {{ background-color: #e67e22; }}
            #GameSelectPanel QPushButton[checkable=true]:checked {{ background-color: #16a085; border-color: #1abc9c; }}
        """)

    def create_title_bar(self, parent_layout):
        title_bar = QWidget()
        title_bar.setObjectName("TitleBar")
        title_bar.setFixedHeight(40)
        title_bar_layout = QHBoxLayout(title_bar)
        title_bar_layout.setContentsMargins(10, 0, 0, 0)
        title = QLabel("ClutchClock")
        title.setStyleSheet("font-weight: bold;")
        
        self.debug_btn = QPushButton("Bug", toolTip="Toggle Debug Mode")
        self.debug_btn.setFont(QFont("Webdings"))
        self.debug_btn.setCheckable(True)
        self.debug_btn.clicked.connect(self.toggle_debug_mode)

        self.overlay_on_top_btn = QPushButton("P", toolTip="Toggle Overlay Always On Top")
        self.overlay_on_top_btn.setFont(QFont("Webdings"))
        self.overlay_on_top_btn.setCheckable(True)
        self.overlay_on_top_btn.setChecked(True)
        self.overlay_on_top_btn.clicked.connect(self.toggle_overlay_on_top)
        
        minimize_btn = QPushButton("0", toolTip="Minimize", clicked=self.showMinimized)
        minimize_btn.setFont(QFont("Webdings"))
        close_btn = QPushButton("r", toolTip="Close", clicked=self.close)
        close_btn.setFont(QFont("Webdings"))
        
        title_bar_layout.addWidget(title)
        title_bar_layout.addStretch()
        title_bar_layout.addWidget(self.debug_btn)
        title_bar_layout.addWidget(self.overlay_on_top_btn)
        title_bar_layout.addWidget(minimize_btn)
        title_bar_layout.addWidget(close_btn)
        parent_layout.addWidget(title_bar)

    def create_game_select_panel(self, parent_layout):
        group_box = QGroupBox("Game Select")
        group_box.setObjectName("GameSelectPanel")
        layout = QHBoxLayout()
        
        self.valorant_btn = QPushButton("Valorant")
        self.valorant_btn.setCheckable(True)
        self.valorant_btn.clicked.connect(lambda: self.switch_game("Valorant"))
        
        self.cs2_btn = QPushButton("CS2")
        self.cs2_btn.setCheckable(True)
        self.cs2_btn.clicked.connect(lambda: self.switch_game("CS2"))

        if config['active_game'] == 'Valorant':
            self.valorant_btn.setChecked(True)
        else:
            self.cs2_btn.setChecked(True)

        layout.addWidget(self.valorant_btn)
        layout.addWidget(self.cs2_btn)
        group_box.setLayout(layout)
        parent_layout.addWidget(group_box)

    def create_settings_panel(self, parent_layout):
        group_box = QGroupBox("Settings")
        group_box.setObjectName("SettingsPanel")
        layout = QVBoxLayout()

        calibrate_btn = QPushButton("Calibrate Capture Region")
        calibrate_btn.clicked.connect(self.calibrate_region)
        layout.addWidget(calibrate_btn)

        timer_controls_layout = QHBoxLayout()
        timer_down_btn = QPushButton("Timer Down", clicked=lambda: self.adjust_timer(-config["global_settings"]["manual_adjustment_ms"]))
        self.stop_timer_btn = QPushButton("Stop Timer", clicked=self.force_stop_timer, enabled=False)
        timer_up_btn = QPushButton("Timer Up", clicked=lambda: self.adjust_timer(config["global_settings"]["manual_adjustment_ms"]))
        timer_controls_layout.addWidget(timer_down_btn)
        timer_controls_layout.addWidget(self.stop_timer_btn)
        timer_controls_layout.addWidget(timer_up_btn)
        layout.addLayout(timer_controls_layout)
        
        form_layout = QFormLayout()
        self.timer_size_slider = self.create_slider(20, 150, config['global_settings']['timer_font_size'], self.update_timer_size)
        self.timer_opacity_slider = self.create_slider(20, 100, int(config['global_settings']['timer_opacity'] * 100), self.update_timer_opacity)
        self.gui_color_btn = QPushButton()
        self.gui_color_btn.setStyleSheet(f"background-color: {config['global_settings']['gui_color']};")
        self.gui_color_btn.clicked.connect(self.pick_gui_color)
        self.gui_opacity_slider = self.create_slider(20, 100, int(config['global_settings']['gui_opacity'] * 100), self.update_gui_opacity)
        form_layout.addRow("Timer Size:", self.timer_size_slider)
        form_layout.addRow("Timer Opacity:", self.timer_opacity_slider)
        form_layout.addRow("GUI Color:", self.gui_color_btn)
        form_layout.addRow("GUI Opacity:", self.gui_opacity_slider)
        layout.addLayout(form_layout)
        layout.addWidget(QLabel("Keyboard Shortcut Setup", alignment=Qt.AlignmentFlag.AlignCenter, styleSheet="font-weight: bold; margin-top: 10px;"))
        self.keybind_buttons = {}
        keybind_layout = QFormLayout()
        for name, key in config["global_settings"]["key_shortcuts"].items():
            btn = QPushButton(key.title())
            btn.setObjectName("KeybindBtn")
            btn.clicked.connect(lambda _, n=name, b=btn: self.listen_for_key(n, b))
            self.keybind_buttons[name] = btn
            keybind_layout.addRow(f"{name.replace('_', ' ').title()}:", btn)
        layout.addLayout(keybind_layout)
        group_box.setLayout(layout)
        parent_layout.addWidget(group_box)

    def create_slider(self, min_val, max_val, current_val, callback):
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(min_val, max_val)
        slider.setValue(current_val)
        slider.valueChanged.connect(callback)
        return slider

    def update_timer_size(self, value):
        config['global_settings']['timer_font_size'] = value
        self.timer_overlay.set_font_size(value)
        save_config(config)

    def update_timer_opacity(self, value):
        opacity = value / 100.0
        config['global_settings']['timer_opacity'] = opacity
        self.timer_overlay.setWindowOpacity(opacity)
        save_config(config)

    def update_gui_opacity(self, value):
        opacity = value / 100.0
        config['global_settings']['gui_opacity'] = opacity
        self.setWindowOpacity(opacity)
        save_config(config)

    def pick_gui_color(self):
        new_color = QColorDialog.getColor(QColor(config["global_settings"]["gui_color"]), self)
        if new_color.isValid():
            config["global_settings"]["gui_color"] = new_color.name()
            self.gui_color_btn.setStyleSheet(f"background-color: {config['global_settings']['gui_color']};")
            self.update_stylesheet()
            save_config(config)

    def listen_for_key(self, name, btn):
        if self.key_listen_button:
            self.key_listen_button.setText(config["global_settings"]["key_shortcuts"][self.listening_for].title())
        self.key_listen_button = btn
        self.listening_for = name
        btn.setText("Press key...")
        btn.setFocus()

    def create_log_panel(self, parent_layout):
        group_box = QGroupBox("Log")
        layout = QVBoxLayout()
        self.info_log = QTextEdit()
        self.info_log.setReadOnly(True)
        layout.addWidget(self.info_log)
        group_box.setLayout(layout)
        parent_layout.addWidget(group_box)

    def setup_listeners(self):
        self.restart_visual_listener()
        
        self.hotkey_thread = GlobalHotkeyListener()
        self.hotkey_thread.hotkey_triggered.connect(self.handle_hotkey)
        self.hotkey_thread.start()

    def handle_hotkey(self, action):
        if action == "up":
            self.adjust_timer(config["global_settings"]["manual_adjustment_ms"])
        elif action == "down":
            self.adjust_timer(-config["global_settings"]["manual_adjustment_ms"])
        elif action == "stop":
            self.force_stop_timer()

    def toggle_debug_mode(self, checked):
        if hasattr(self, 'visual_thread'):
            self.visual_thread.set_debug_mode(checked)
            self.log_message(f"Debug mode {'ON' if checked else 'OFF'}.")

    def calibrate_region(self):
        self.log_message("Pausing scanner for calibration...")
        if hasattr(self, 'visual_thread'):
            self.visual_thread.stop()
            self.visual_thread.wait()

        self.log_message("Select bomb area and press ENTER or ESC.")
        self.hide()
        self.timer_overlay.hide()
        time.sleep(0.5)

        with mss.mss() as sct:
            sct_img = sct.grab(sct.monitors[1])
            img = np.array(sct_img)
            img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

        self.show()
        self.timer_overlay.show()

        roi = cv2.selectROI(f"Calibrate for {config['active_game']} (Press ENTER to confirm)", img, fromCenter=False)
        cv2.destroyAllWindows()

        if roi[2] > 0 and roi[3] > 0:
            x, y, w, h = roi
            config[config['active_game']]["monitor_region"] = {"top": int(y), "left": int(x), "width": int(w), "height": int(h)}
            save_config(config)
            self.log_message(f"New monitor region set for {config['active_game']}")
        else:
            self.log_message("Calibration cancelled.")

        self.restart_visual_listener()

    def start_timer(self, confidence):
        if not self.is_timer_active:
            self.is_timer_active = True
            self.stop_timer_btn.setEnabled(True)
            self.log_message(f"Bomb detected! Match: {confidence:.0%}.")
            self.timer_thread = TimerLogic()
            self.timer_thread.update_timer_display.connect(self.timer_overlay.update_display)
            self.timer_thread.timer_finished.connect(self.reset_timer)
            self.timer_thread.start()

    def force_stop_timer(self):
        if self.is_timer_active and hasattr(self, 'timer_thread'):
            self.timer_thread.stop()
            self.timer_thread.wait()
            self.reset_timer()
            self.log_message("Timer manually stopped.")

    def adjust_timer(self, amount_ms):
        if self.is_timer_active and hasattr(self, 'timer_thread'):
            amount_s = amount_ms / 1000.0
            self.timer_thread.adjust_time(amount_s)
            self.log_message(f"Timer adjusted by {amount_ms:.0f} ms.")

    def reset_timer(self):
        self.timer_overlay.reset()
        self.is_timer_active = False
        self.stop_timer_btn.setEnabled(False)
        self.log_message("Timer finished. Ready for next bomb.")

    def toggle_overlay_on_top(self, checked):
        if checked:
            self.timer_overlay.setWindowFlags(self.timer_overlay.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
            self.log_message("Timer overlay: Always on top ON.")
        else:
            self.timer_overlay.setWindowFlags(self.timer_overlay.windowFlags() & ~Qt.WindowType.WindowStaysOnTopHint)
            self.log_message("Timer overlay: Always on top OFF.")
        self.timer_overlay.show()

    def log_message(self, message): self.info_log.append(message)
    def handle_critical_error(self, message):
        self.log_message(message)
        self.timer_overlay.update_display("ERROR", config["global_settings"]["timer_colors"]["danger"])

    def update_confidence_label(self, value):
        self.confidence_label.setText(f"Confidence: {value:.0%}")
        if hasattr(self, 'visual_thread') and self.visual_thread.debug_mode:
            self.log_message(f"Debug Confidence: {value:.0%}")

    def keyPressEvent(self, event):
        if self.key_listen_button and self.listening_for:
            key_name = keyboard.read_key(suppress=True)
            self.key_listen_button.setText(key_name.title())
            config["global_settings"]["key_shortcuts"][self.listening_for] = key_name
            save_config(config)
            self.log_message(f"Set '{self.listening_for}' to '{key_name}'")
            self.key_listen_button.clearFocus()
            self.key_listen_button = None
            self.listening_for = None
            self.hotkey_thread.register_hotkeys()
            return
        super().keyPressEvent(event)

    def switch_game(self, game_name):
        if config['active_game'] == game_name:
            return
        
        self.valorant_btn.setChecked(game_name == "Valorant")
        self.cs2_btn.setChecked(game_name == "CS2")
        
        config['active_game'] = game_name
        save_config(config)
        self.log_message(f"Switched to {game_name} profile.")
        self.restart_visual_listener()

    def restart_visual_listener(self):
        if hasattr(self, 'visual_thread'):
            self.visual_thread.stop()
            self.visual_thread.wait()
        
        try:
            self.visual_thread = VisualListener()
            self.visual_thread.spike_detected.connect(self.start_timer)
            self.visual_thread.critical_error.connect(self.handle_critical_error)
            self.visual_thread.log_message.connect(self.log_message)
            self.visual_thread.update_confidence.connect(self.update_confidence_label)
            self.visual_thread.start()
        except FileNotFoundError as e:
            self.log_message(f"ERROR: {e}. Make sure you have the correct template image for {config['active_game']}.")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton: self.old_pos = event.globalPosition().toPoint()
    def mouseMoveEvent(self, event):
        if hasattr(self, 'old_pos') and self.old_pos is not None and event.buttons() == Qt.MouseButton.LeftButton:
            delta = event.globalPosition().toPoint() - self.old_pos
            self.move(self.x() + delta.x(), self.y() + delta.y())
            self.old_pos = event.globalPosition().toPoint()
    def mouseReleaseEvent(self, event): self.old_pos = None

    def closeEvent(self, event):
        if hasattr(self, 'hotkey_thread'): self.hotkey_thread.stop()
        if hasattr(self, 'visual_thread'): self.visual_thread.stop(); self.visual_thread.wait()
        if hasattr(self, 'timer_thread') and self.timer_thread.isRunning(): self.timer_thread.stop(); self.timer_thread.wait()
        self.timer_overlay.close()
        event.accept()
        os._exit(0)

def main():
    app = QApplication(sys.argv)
    window = SettingsWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
