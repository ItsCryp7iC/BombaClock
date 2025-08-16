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
import subprocess # Added for robust restart
from http.server import BaseHTTPRequestHandler, HTTPServer
import winreg
# --- NEW IMPORTS for Firewall Automation ---
import ctypes
import subprocess
# --- END NEW IMPORTS ---
from PyQt6.QtWidgets import (QApplication, QLabel, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QPushButton, QTextEdit, QFrame, QGraphicsOpacityEffect,
                             QColorDialog, QGroupBox, QFormLayout, QGraphicsDropShadowEffect, QSizePolicy,
                             QSlider, QStatusBar, QMessageBox, QSpinBox)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QPropertyAnimation, QEasingCurve, QPoint
from PyQt6.QtGui import QFont, QColor, QIcon, QFontDatabase, QScreen

# --- PRESET MONITOR REGIONS ---
PRESET_REGIONS = {
    "Valorant": {
        "1080p": {"top": 14, "left": 918, "width": 86, "height": 81},
        "1440p": {"top": 21, "left": 1225, "width": 111, "height": 10},
        "768p": {"top": 11, "left": 654, "width": 62, "height": 56}
    }
}


# --- DEFAULT CONFIGURATION ---
DEFAULT_CONFIG = {
    "active_game": "Valorant",
    "Valorant": {
        "monitor_region": {"top": 14, "left": 918, "width": 86, "height": 81},
        "visual_confidence": 0.25,
        "spike_duration": 45.0,
        "defuse_warning_time": 7
    },
    "CS2": {
        "spike_duration": 40.0,
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
                # Merge with default to ensure all keys exist
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

class GameListener(QThread):
    bomb_planted = pyqtSignal(float)
    critical_error = pyqtSignal(str)
    log_message = pyqtSignal(str)
    update_confidence = pyqtSignal(float)
    update_debug_frame = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.debug_mode = False
        self.running = True
        self.gsi_server = None
        if config['active_game'] == 'Valorant':
            self.load_template()

    def load_template(self):
        game = config['active_game']
        if game == 'Valorant':
            preset = self.get_active_preset()
            template_filename = f"valorant_{preset}.png"
            template_path = resource_path(template_filename)

            template_rgba = cv2.imread(template_path, cv2.IMREAD_UNCHANGED)
            if template_rgba is None:
                raise FileNotFoundError(f"Could not load template: {template_filename}")
            if len(template_rgba.shape) < 3 or template_rgba.shape[2] != 4:
                self.template = cv2.cvtColor(template_rgba, cv2.COLOR_BGR2RGB)
                self.mask = None
            else:
                self.template = template_rgba[:, :, :3]
                self.mask = template_rgba[:, :, 3]
            self.th, self.tw = self.template.shape[:2]

    def get_active_preset(self):
        active_game = config['active_game']
        current_region = config[active_game]['monitor_region']
        for preset, region in PRESET_REGIONS[active_game].items():
            if region == current_region:
                return preset
        return "1080p" # Default fallback

    def run(self):
        self.log_message.emit(f"Listener started for {config['active_game']}.")
        if config['active_game'] == 'Valorant':
            self.run_valorant_scanner()
        else: # CS2
            self.run_cs2_gsi_listener()

    def run_valorant_scanner(self):
        self.log_message.emit("Scanning screen for bomb plant...")
        with mss.mss() as sct:
            while self.running:
                try:
                    game_config = config['Valorant']
                    img = sct.grab(game_config["monitor_region"])
                    screen_bgr = np.array(img)[:, :, :3]

                    res = cv2.matchTemplate(screen_bgr, self.template, cv2.TM_CCOEFF_NORMED, mask=self.mask)
                    _, max_val, _, max_loc = cv2.minMaxLoc(res)
                    self.update_confidence.emit(max_val)

                    if self.debug_mode:
                        debug_screen = screen_bgr.copy()
                        if max_val > 0.4:
                            cv2.rectangle(debug_screen, max_loc, (max_loc[0] + self.tw, max_loc[1] + self.th), (0, 255, 0), 2)
                        self.update_debug_frame.emit(debug_screen)

                    if max_val > game_config["visual_confidence"]:
                        self.bomb_planted.emit(max_val)
                        time.sleep(1) # Prevent immediate re-trigger
                except Exception as e:
                    self.log_message.emit(f"Error in Valorant scanner: {e}")
                time.sleep(0.25)

    def run_cs2_gsi_listener(self):
        self.log_message.emit("Starting GSI server for CS2...")

        # Define handler class inside the method to give it access to 'self' (the QThread instance)
        class GSIRequestHandler(BaseHTTPRequestHandler):
            def do_POST(handler):
                length = int(handler.headers.get('content-length'))
                body = handler.rfile.read(length)
                
                # --- MODIFIED CODE: Removed the verbose logging ---
                # The line below was for debugging and is now removed for a cleaner log.
                # self.log_message.emit(f"GSI data received: {body.decode('utf-8', errors='ignore')}")
                # --- END MODIFICATION ---
                
                try:
                    payload = json.loads(body.decode('utf-8'))
                    bomb_state = payload.get('round', {}).get('bomb')
                    if bomb_state == 'planted':
                        self.bomb_planted.emit(1.0) # GSI is 100% confidence
                except (json.JSONDecodeError, UnicodeDecodeError):
                    # Ignore malformed data packets that can sometimes be sent
                    pass

                handler.send_response(200)
                handler.end_headers()

            def log_message(self, format, *args):
                return # Suppress console logs from the server

        try:
            self.gsi_server = HTTPServer(('127.0.0.1', 3000), GSIRequestHandler)
            self.log_message.emit("GSI server running. Waiting for CS2 data...")
            self.gsi_server.serve_forever()
        except Exception as e:
            self.critical_error.emit(f"Could not start GSI server: {e}. Is another app using port 3000?")

    def set_debug_mode(self, is_enabled):
        self.debug_mode = is_enabled

    def stop(self):
        self.running = False
        if self.gsi_server:
            # Shutdown needs to be run in a separate thread to not block
            threading.Thread(target=self.gsi_server.shutdown).start()

class TimerLogic(QThread):
    update_timer_display = pyqtSignal(str, str)
    timer_finished = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.game_config = config[config['active_game']]
        self.lock = threading.Lock()
        self.running = True
        self.end_time = time.monotonic() + float(self.game_config["spike_duration"])

    def run(self):
        last_displayed_time = -1
        while self.running:
            with self.lock:
                current_time = time.monotonic()
                if current_time >= self.end_time:
                    break

                time_left = self.end_time - current_time
                current_display_time = int(round(time_left))

            if current_display_time != last_displayed_time:
                self.update_timer_display.emit(str(current_display_time), self.get_color_for_time(current_display_time))
                last_displayed_time = current_display_time

            time.sleep(0.05)

        if self.running:
            self.update_timer_display.emit("0", self.get_color_for_time(0))
            self.timer_finished.emit()

    def adjust_time(self, amount):
        with self.lock:
            self.end_time += amount

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
        # Unregister all previous hotkeys before adding new ones
        for key_func in self.hotkeys.values():
            keyboard.remove_hotkey(key_func)
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
        for key_func in self.hotkeys.values():
            keyboard.remove_hotkey(key_func)

class TimerOverlayWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        screen_geometry = QApplication.primaryScreen().geometry()
        initial_x = (screen_geometry.width() - 450) // 2
        initial_y = 150
        self.setGeometry(initial_x, initial_y, 450, 150)

        self.setWindowOpacity(config['global_settings']['timer_opacity'])

        layout = QVBoxLayout(self)
        self.timer_label = QLabel("--", self)
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
        self.update_display("--", config["global_settings"]["timer_colors"]["normal"])

    def mousePressEvent(self, event): self.old_pos = event.globalPosition().toPoint()
    def mouseMoveEvent(self, event):
        delta = event.globalPosition().toPoint() - self.old_pos
        self.move(self.x() + delta.x(), self.y() + delta.y())
        self.old_pos = event.globalPosition().toPoint()

class SettingsWindow(QMainWindow):
    def __init__(self, title_font_family):
        super().__init__()
        self.title_font_family = title_font_family
        self.is_timer_active = False
        self.key_listen_button = None
        self.timer_overlay = TimerOverlayWindow()
        self.debug_window_visible = False
        self.initUI()
        self.setup_listeners()
        self.timer_overlay.show()
        # --- NEW: Check firewall status on startup ---
        self.check_and_add_firewall_rule()

    def initUI(self):
        self.setGeometry(150, 150, 450, 720) # Increased height for new button
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowOpacity(config['global_settings']['gui_opacity'])
        self.update_stylesheet()

        self.setWindowIcon(QIcon(resource_path('icon.ico')))

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

        self.confidence_spinbox.valueChanged.connect(self.update_confidence_threshold)
        self.update_ui_for_game()

    def update_stylesheet(self):
        self.setStyleSheet(f"""
            #CentralWidget {{ background-color: {config['global_settings']['gui_color']}; border-radius: 10px; }}
            QWidget {{ color: #ecf0f1; }}
            QMessageBox QLabel, QMessageBox QPushButton {{ color: black; }}
            QGroupBox {{ font-weight: bold; border: 1px solid #4a6278; margin-top: 10px; border-radius: 5px;}}
            QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 5px 0 5px; }}
            QTextEdit {{ background-color: #34495e; color: #ecf0f1; border: 1px solid #2c3e50; border-radius: 3px; }}
            QStatusBar {{ background-color: {config['global_settings']['gui_color']}; border-top: 1px solid #4a6278; }}
            #TitleBar QPushButton {{ background-color: transparent; border: none; font-size: 14px; width: 30px; height: 30px; }}
            #TitleBar QPushButton:hover {{ background-color: #4a6278; }}
            #SettingsPanel QPushButton, #GameSelectPanel QPushButton, #PresetsPanel QPushButton {{
                background-color: #34495e; border: 1px solid #4a6278; padding: 5px; border-radius: 3px;
            }}
            #SettingsPanel QPushButton:hover, #GameSelectPanel QPushButton:hover, #PresetsPanel QPushButton:hover {{
                background-color: #4a6278;
            }}
            #SettingsPanel QPushButton:disabled {{
                background-color: #2c3e50;
                color: #7f8c8d;
            }}
            #KeybindBtn:focus {{ background-color: #e67e22; }}
            #GameSelectPanel QPushButton[checkable=true]:checked, #PresetsPanel QPushButton[checkable=true]:checked {{
                background-color: #16a085; border-color: #1abc9c;
            }}
            QSpinBox {{
                background-color: #ecf0f1;
                color: black;
            }}
        """)

    def create_title_bar(self, parent_layout):
        title_bar = QWidget()
        title_bar.setObjectName("TitleBar")
        title_bar.setFixedHeight(40)
        title_bar_layout = QHBoxLayout(title_bar)
        title_bar_layout.setContentsMargins(10, 0, 0, 0)

        title = QLabel("PlantSense")
        title_font = QFont(self.title_font_family, 16)
        title.setFont(title_font)

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

        self.presets_group = QGroupBox("Resolution Presets")
        self.presets_group.setObjectName("PresetsPanel")
        presets_layout = QHBoxLayout()
        self.preset_1080p_btn = QPushButton("1080p")
        self.preset_1440p_btn = QPushButton("1440p")
        self.preset_768p_btn = QPushButton("768p")

        for btn in [self.preset_1080p_btn, self.preset_1440p_btn, self.preset_768p_btn]:
            btn.setCheckable(True)

        self.preset_1080p_btn.clicked.connect(lambda: self.set_region_preset("1080p"))
        self.preset_1440p_btn.clicked.connect(lambda: self.set_region_preset("1440p"))
        self.preset_768p_btn.clicked.connect(lambda: self.set_region_preset("768p"))

        presets_layout.addWidget(self.preset_1080p_btn)
        presets_layout.addWidget(self.preset_1440p_btn)
        presets_layout.addWidget(self.preset_768p_btn)
        self.presets_group.setLayout(presets_layout)
        layout.addWidget(self.presets_group)

        self.stop_timer_btn = QPushButton("Stop Timer", clicked=self.force_stop_timer, enabled=False)
        layout.addWidget(self.stop_timer_btn)

        duration_layout = QHBoxLayout()
        self.duration_label = QLabel()
        self.update_duration_label()

        timer_down_btn = QPushButton("Timer Down", clicked=lambda: self.adjust_duration(-config["global_settings"]["manual_adjustment_ms"]))
        timer_up_btn = QPushButton("Timer Up", clicked=lambda: self.adjust_duration(config["global_settings"]["manual_adjustment_ms"]))

        duration_layout.addWidget(self.duration_label)
        duration_layout.addStretch()
        duration_layout.addWidget(timer_down_btn)
        duration_layout.addWidget(timer_up_btn)
        layout.addLayout(duration_layout)

        form_layout = QFormLayout()

        self.confidence_spinbox = QSpinBox()
        self.confidence_spinbox.setRange(1, 100)
        self.confidence_spinbox.setSuffix("%")
        self.confidence_label_widget = QLabel("Confidence Threshold:")
        form_layout.addRow(self.confidence_label_widget, self.confidence_spinbox)

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
        
        self.reinstall_gsi_btn = QPushButton("Re-Configure GSI File")
        self.reinstall_gsi_btn.setToolTip("Attempts to find your CS2 installation and create the GSI config file.")
        self.reinstall_gsi_btn.clicked.connect(self.handle_gsi_reinstall)
        form_layout.addRow("CS2 Actions:", self.reinstall_gsi_btn)
        
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

    def update_confidence_threshold(self, value):
        active_game = config['active_game']
        confidence = value / 100.0
        config[active_game]['visual_confidence'] = confidence
        save_config(config)
        self.log_message(f"Set {active_game} confidence to {value}%")

    def pick_gui_color(self):
        color_dialog = QColorDialog(self)
        color_dialog.setStyleSheet("QWidget { color: black; }")
        current_color = QColor(config["global_settings"]["gui_color"])
        color_dialog.setCurrentColor(current_color)

        if color_dialog.exec():
            new_color = color_dialog.selectedColor()
            config["global_settings"]["gui_color"] = new_color.name()
            self.gui_color_btn.setStyleSheet(f"background-color: {config['global_settings']['gui_color']};")
            self.update_stylesheet()
            save_config(config)

    def listen_for_key(self, name, btn):
        if self.key_listen_button:
            # Reset previous button if user clicks another one
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
            self.adjust_duration(config["global_settings"]["manual_adjustment_ms"])
        elif action == "down":
            self.adjust_duration(-config["global_settings"]["manual_adjustment_ms"])
        elif action == "stop":
            self.force_stop_timer()

    def toggle_debug_mode(self, checked):
        if hasattr(self, 'game_listener_thread'):
            self.game_listener_thread.set_debug_mode(checked)
            self.log_message(f"Debug mode {'ON' if checked else 'OFF'}.")
            if not checked and self.debug_window_visible:
                cv2.destroyAllWindows()
                self.debug_window_visible = False

    def show_debug_frame(self, frame):
        cv2.imshow("Debug - Live Capture", frame)
        cv2.waitKey(1)
        self.debug_window_visible = True

    def set_region_preset(self, resolution):
        msg_box = QMessageBox(self)
        msg_box.setStyleSheet("QLabel, QPushButton { color: black; }")
        msg_box.setWindowTitle("Restart Required")
        msg_box.setText("Changing the resolution preset requires an application restart.")
        msg_box.setInformativeText("Do you want to save and restart now?")
        msg_box.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        msg_box.setDefaultButton(QMessageBox.StandardButton.Ok)

        if msg_box.exec() == QMessageBox.StandardButton.Ok:
            active_game = config['active_game']
            if resolution in PRESET_REGIONS[active_game]:
                new_region = PRESET_REGIONS[active_game][resolution]
                config[active_game]["monitor_region"] = new_region
                save_config(config)
                self.log_message(f"Set {active_game} monitor region to {resolution} preset. Restarting...")
                self.restart_application()
            else:
                self.log_message(f"No preset found for {resolution} in {active_game} profile.")

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

    def adjust_duration(self, amount_ms):
        if not self.is_timer_active: return # Don't adjust if timer isn't running
        amount_s = amount_ms / 1000.0
        self.timer_thread.adjust_time(amount_s)
        self.log_message(f"Adjusted timer by {amount_s:+.2f}s")


    def update_duration_label(self):
        active_game = config['active_game']
        duration = config[active_game]['spike_duration']
        self.duration_label.setText(f"Bomb Duration: {duration:.2f}s")

    def reset_timer(self):
        self.timer_overlay.reset()
        self.is_timer_active = False
        self.stop_timer_btn.setEnabled(False)
        self.log_message("Timer finished. Ready for next event.")

    def toggle_overlay_on_top(self, checked):
        if checked:
            self.timer_overlay.setWindowFlags(self.timer_overlay.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
            self.log_message("Timer overlay: Always on top ON.")
        else:
            self.timer_overlay.setWindowFlags(self.timer_overlay.windowFlags() & ~Qt.WindowType.WindowStaysOnTopHint)
            self.log_message("Timer overlay: Always on top OFF.")
        self.timer_overlay.show() # Re-apply flags

    def log_message(self, message): self.info_log.append(message)
    def handle_critical_error(self, message):
        self.log_message(f"CRITICAL: {message}")
        self.timer_overlay.update_display("ERROR", config["global_settings"]["timer_colors"]["danger"])

    def update_confidence_label(self, value):
        self.confidence_label.setText(f"Confidence: {value:.0%}")
        if hasattr(self, 'game_listener_thread') and self.game_listener_thread.debug_mode:
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

        if game_name == "CS2":
            self.log_message("--- Initializing CS2 GSI Configuration ---")
            success, message = self.setup_gsi_file()
            self.log_message(message)

        msg_box = QMessageBox(self)
        msg_box.setStyleSheet("QLabel, QPushButton { color: black; }")
        msg_box.setWindowTitle("Restart Required")
        msg_box.setText(f"Switching the game profile to {game_name} requires a restart.")
        msg_box.setInformativeText("Do you want to save and restart now?")
        msg_box.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        msg_box.setDefaultButton(QMessageBox.StandardButton.Ok)

        if msg_box.exec() == QMessageBox.StandardButton.Ok:
            config['active_game'] = game_name
            save_config(config)
            self.log_message(f"Switched to {game_name} profile. Restarting...")
            self.restart_application()
        else:
            # Revert button checks if user cancels
            self.valorant_btn.setChecked(config['active_game'] == "Valorant")
            self.cs2_btn.setChecked(config['active_game'] == "CS2")

    def restart_visual_listener(self):
        if hasattr(self, 'game_listener_thread'):
            self.game_listener_thread.stop()
            self.game_listener_thread.wait()

        try:
            self.game_listener_thread = GameListener()
            self.game_listener_thread.bomb_planted.connect(self.start_timer)
            self.game_listener_thread.critical_error.connect(self.handle_critical_error)
            self.game_listener_thread.log_message.connect(self.log_message)
            self.game_listener_thread.update_confidence.connect(self.update_confidence_label)
            self.game_listener_thread.update_debug_frame.connect(self.show_debug_frame)
            self.game_listener_thread.start()
        except FileNotFoundError as e:
            self.log_message(f"ERROR: {e}. Make sure you have the correct template image for {config['active_game']}.")

    def restart_application(self):
        # Gracefully stop all threads before restarting
        if hasattr(self, 'hotkey_thread'): self.hotkey_thread.stop()
        if hasattr(self, 'game_listener_thread'): self.game_listener_thread.stop(); self.game_listener_thread.wait()
        if hasattr(self, 'timer_thread') and self.timer_thread.isRunning(): self.timer_thread.stop(); self.timer_thread.wait()

        subprocess.Popen([sys.executable] + sys.argv)
        self.close()

    def update_preset_buttons(self):
        active_game = config['active_game']
        if active_game == 'Valorant':
            current_region = config[active_game]['monitor_region']
            self.preset_1080p_btn.setChecked(current_region == PRESET_REGIONS[active_game].get('1080p'))
            self.preset_1440p_btn.setChecked(current_region == PRESET_REGIONS[active_game].get('1440p'))
            self.preset_768p_btn.setChecked(current_region == PRESET_REGIONS[active_game].get('768p'))

    def update_ui_for_game(self):
        is_valorant = (config['active_game'] == 'Valorant')
        self.presets_group.setVisible(is_valorant)
        self.confidence_spinbox.setVisible(is_valorant)
        self.confidence_label_widget.setVisible(is_valorant)
        self.reinstall_gsi_btn.setVisible(not is_valorant)

        if is_valorant:
            self.confidence_spinbox.setValue(int(config['Valorant']['visual_confidence'] * 100))
            self.update_preset_buttons()
        else:
            self.confidence_label.setText("Confidence: GSI")

    def handle_gsi_reinstall(self):
        self.log_message("--- Manual CS2 GSI Re-Configuration Started ---")
        success, message = self.setup_gsi_file()
        self.log_message(message)

        msg_box = QMessageBox(self)
        msg_box.setStyleSheet("QLabel, QPushButton { color: black; }")
        if success:
            msg_box.setWindowTitle("Success")
            msg_box.setText("CS2 GSI file was configured successfully.")
            msg_box.setInformativeText("Please restart CS2 if it was running to apply the changes.")
            msg_box.setIcon(QMessageBox.Icon.Information)
        else:
            msg_box.setWindowTitle("Failure")
            msg_box.setText("Could not configure the CS2 GSI file automatically.")
            msg_box.setInformativeText("Please check the log for details on the paths that were checked.")
            msg_box.setIcon(QMessageBox.Icon.Warning)
        msg_box.exec()
        self.log_message("--- Manual CS2 GSI Re-Configuration Finished ---")
    
    # --- NEW METHOD: Automates firewall rule creation ---
    def check_and_add_firewall_rule(self):
        rule_name = "PlantSense GSI"
        program_path = sys.executable
        
        try:
            is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
        except Exception:
            is_admin = False # Assume not admin if check fails

        if not is_admin:
            self.log_message("--- Firewall Check ---")
            self.log_message("WARNING: Not running as Administrator.")
            self.log_message("Cannot add firewall rule automatically.")
            self.log_message("If CS2 timer fails, please close and re-run this app as an Administrator once.")
            self.log_message("--------------------")
            return

        self.log_message("--- Firewall Check (Running as Admin) ---")
        # Check if rule exists
        check_command = f'netsh advfirewall firewall show rule name="{rule_name}"'
        try:
            result = subprocess.run(check_command, capture_output=True, text=True, shell=True, creationflags=subprocess.CREATE_NO_WINDOW)
            if "No rules match the specified criteria" in result.stdout:
                self.log_message(f"Firewall rule '{rule_name}' not found. Attempting to create it...")
                # Add the rule
                add_command = (
                    f'netsh advfirewall firewall add rule name="{rule_name}" '
                    f'dir=in action=allow protocol=TCP localport=3000 '
                    f'program="{program_path}"'
                )
                add_result = subprocess.run(add_command, capture_output=True, text=True, shell=True, creationflags=subprocess.CREATE_NO_WINDOW)
                if add_result.returncode == 0:
                    self.log_message("✅ Firewall rule created successfully.")
                else:
                    self.log_message(f"❌ ERROR: Failed to create firewall rule. Details: {add_result.stderr}")
            else:
                self.log_message("✅ Firewall rule already exists.")
        except Exception as e:
            self.log_message(f"❌ ERROR checking/adding firewall rule: {e}")
        self.log_message("--------------------")

    def setup_gsi_file(self):
        gsi_content = r'''
"PlantSense GSI"
{
    "uri"               "http://127.0.0.1:3000"
    "timeout"           "5.0"
    "buffer"            "0.1"
    "throttle"          "0.1"
    "heartbeat"         "15.0"
    "data"
    {
        "round"         "1"
    }
}
'''
        log_messages = []
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam")
            steam_path = Path(winreg.QueryValueEx(key, "InstallPath")[0])
            winreg.CloseKey(key)
            log_messages.append(f"Found Steam installation in registry: {steam_path}")

            library_folders_path = steam_path / "steamapps" / "libraryfolders.vdf"
            csgo_cfg_path = None
            possible_paths = [steam_path] # Also check the main steam install path

            if library_folders_path.exists():
                with open(library_folders_path, 'r') as f:
                    for line in f:
                        if '"path"' in line:
                            path = Path(line.split('"')[3].replace('\\\\', '\\'))
                            possible_paths.append(path)

            log_messages.append(f"Checking {len(possible_paths)} possible Steam library location(s)...")

            for path in possible_paths:
                log_messages.append(f"--> Checking library: {path}")
                potential_path = path / "steamapps" / "common" / "Counter-Strike Global Offensive"
                if potential_path.exists() and potential_path.is_dir():
                    csgo_cfg_path = potential_path / "game" / "csgo" / "cfg"
                    log_messages.append(f"✔️ Found valid CS2 installation in this library.")
                    break
                else:
                    log_messages.append(f"    - No CS2 installation found here.")

            if csgo_cfg_path and csgo_cfg_path.exists():
                log_messages.append(f"Target CS2 config folder identified: {csgo_cfg_path}")
                gsi_file_path = csgo_cfg_path / "gamestate_integration_plantsense.cfg"
                log_messages.append(f"Attempting to write GSI file to: {gsi_file_path}")

                with open(gsi_file_path, 'w') as f:
                    f.write(gsi_content.strip())

                if gsi_file_path.exists():
                     log_messages.append("✅ CS2 GSI file created/updated successfully.")
                     return (True, "\n".join(log_messages))
                else:
                    log_messages.append("❌ ERROR: Failed to write GSI file to disk. Check permissions.")
                    return (False, "\n".join(log_messages))
            else:
                log_messages.append("❌ ERROR: Could not find the '.../game/csgo/cfg' directory in any Steam library.")
                return (False, "\n".join(log_messages))

        except FileNotFoundError:
            log_messages.append("❌ ERROR: Could not find Steam installation in Windows Registry. Is Steam installed?")
            return (False, "\n".join(log_messages))
        except Exception as e:
            log_messages.append(f"❌ An unexpected error occurred: {e}")
            return (False, "\n".join(log_messages))

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
        if hasattr(self, 'game_listener_thread'): self.game_listener_thread.stop(); self.game_listener_thread.wait()
        if hasattr(self, 'timer_thread') and self.timer_thread.isRunning(): self.timer_thread.stop(); self.timer_thread.wait()
        self.timer_overlay.close()
        if self.debug_window_visible:
            cv2.destroyAllWindows()
        event.accept()
        os._exit(0)

def main():
    app = QApplication(sys.argv)

    font_id = QFontDatabase.addApplicationFont(resource_path("MarckScript-Regular.ttf"))
    if font_id != -1:
        title_font_family = QFontDatabase.applicationFontFamilies(font_id)[0]
    else:
        title_font_family = "Arial" # Fallback font

    window = SettingsWindow(title_font_family)
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
