import sys
import re
import os
import cv2
import datetime
import bisect
from PySide6.QtCore import QTimer, Qt, QPoint, QSize
from PySide6.QtWidgets import (
    QApplication, QLabel, QSplitter, QWidget, 
    QVBoxLayout, QHBoxLayout, QPushButton, QSlider, QSizePolicy, QFileDialog,
    QTableView, QHeaderView, QAbstractItemView, QFrame, QSpacerItem, QMenuBar, QMenu, QMainWindow, QDialog, QProgressBar, QMessageBox
)
from PySide6.QtGui import QImage, QPixmap, QStandardItemModel, QStandardItem, QColor, QFont, QIcon, QAction
import qdarktheme
import zipfile, json, tempfile, subprocess, pathlib

def resource_path(relative_path):
    if hasattr(sys, "_MEIPASS"):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

LOG_ICON_PATH = resource_path("logs.png")  
VIDEO_ICON_PATH = resource_path("video.png")
PLAY_ICON_PATH = resource_path("play.png") 
PAUSE_ICON_PATH = resource_path("pause.png")
RESTART_ICON_PATH = resource_path("restart.png")
END_ICON_PATH = resource_path("end.png")
PLUS2_ICON_PATH = resource_path("plus2.png")
MINUS2_ICON_PATH = resource_path("minus2.png")
ONEX_ICON_PATH = resource_path("1x.png")
HALFX_ICON_PATH = resource_path("point5.png")
POINT2X_ICON_PATH = resource_path("point2.png")
APPICON = resource_path("synclogs128.ico")

# ------------------- UTILIDADES -------------------

def get_file_creation_time_utc(path):
    ts = os.path.getctime(path)
    return datetime.datetime.utcfromtimestamp(ts)

def parse_logs(log_file):
    logs = []
    original_lines = []
    pattern = re.compile(r"\[(\d{4}\.\d{2}\.\d{2})-(\d{2}\.\d{2}\.\d{2}):(\d+)\]")
    with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            original_lines.append(line.rstrip("\n"))
            match = pattern.search(line)
            if match:
                date_str = match.group(1).replace(".", "-")
                time_str = match.group(2).replace(".", ":")
                ms_str = match.group(3)
                log_time = datetime.datetime.strptime(
                    f"{date_str} {time_str}.{ms_str}",
                    "%Y-%m-%d %H:%M:%S.%f"
                )
                logs.append((log_time, line.strip()))
    return logs, original_lines

def export_analysis(player, out_path, progress_dialog=None):
    tmpdir = tempfile.mkdtemp()

    if progress_dialog:
        progress_dialog.update_step(1, "Reencoding video...")
    recoded_video_path = os.path.join(tmpdir, "video.mp4")
    subprocess.run([
        "ffmpeg_binaries/bin/ffmpeg", "-hide_banner", "-y", "-i", player.video_path,
        "-b:v", "2M", "-preset", "fast", "-c:a", "aac",
        recoded_video_path
    ])

    if progress_dialog:
        progress_dialog.update_step(2, "Saving logs...")
    logs_path = os.path.join(tmpdir, "logs.txt")
    with open(logs_path, "w", encoding="utf-8") as f:
        f.write("\n".join(player.original_logs))

    if progress_dialog:
        progress_dialog.update_step(3, "Saving metadata...")
    meta = {
        "video_start_time": player.video_start_time.isoformat(),
        "fps": player.fps,
        "total_frames": player.total_frames
    }
    meta_path = os.path.join(tmpdir, "meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f)

    if progress_dialog:
        progress_dialog.update_step(4, "Creating cat crate...")
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(recoded_video_path, "video.mp4")
        z.write(logs_path, "logs.txt")
        z.write(meta_path, "meta.json")

def import_analysis(lupi_path):
    """Extrae un .lupi y devuelve (video_path, logs, original_logs, video_start_time, fps)."""
    tmpdir = tempfile.mkdtemp()

    with zipfile.ZipFile(lupi_path, "r") as z:
        z.extractall(tmpdir)

    video_path = os.path.join(tmpdir, "video.mp4")
    logs_path = os.path.join(tmpdir, "logs.txt")
    meta_path = os.path.join(tmpdir, "meta.json")

    # Metadata
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    video_start_time = datetime.datetime.fromisoformat(meta["video_start_time"])
    fps = meta["fps"]

    # Logs originales
    with open(logs_path, "r", encoding="utf-8") as f:
        original_logs = [line.rstrip("\n") for line in f]

    # Reparsear logs
    logs = []
    pattern = re.compile(r"\[(\d{4}\.\d{2}\.\d{2})-(\d{2}\.\d{2}\.\d{2}):(\d+)\]")
    for line in original_logs:
        match = pattern.search(line)
        if match:
            date_str = match.group(1).replace(".", "-")
            time_str = match.group(2).replace(".", ":")
            ms_str = match.group(3)
            log_time = datetime.datetime.strptime(
                f"{date_str} {time_str}.{ms_str}",
                "%Y-%m-%d %H:%M:%S.%f"
            )
            logs.append((log_time, line.strip()))

    return video_path, logs, original_logs, video_start_time, fps

def open_lupi_from_cold(from_file=None):
    path = from_file
    title = str(os.path.basename(path))
    if path:
        video_path, logs, original_logs, video_start_time, fps = import_analysis(path)
        player = LogVideoPlayer(video_path, logs, original_logs, video_start_time, fps, title)
        player.showMaximized()

# ------------------- REPRODUCTOR -------------------

class LogVideoPlayer(QMainWindow):
    def __init__(self, video_path, log_path_or_logs, original_logs=None, video_start_time=None, fps=None, title=None):
        super().__init__()
        self.video_path = video_path
        self.log_path = log_path_or_logs
        self.prevIdx = None
        
        if isinstance(log_path_or_logs, list) and original_logs is not None:
            # Modo desde .lupi
            self.logs = log_path_or_logs
            self.original_logs = original_logs
            self.video_start_time = video_start_time
            self.fps = fps
            self.title = f" | {title}"
            self.setWindowTitle(f"Lupi{self.title}")
        else:
            # Modo normal desde archivos
            self.logs, self.original_logs = parse_logs(log_path_or_logs)
            self.video_start_time = get_file_creation_time_utc(video_path)
            self.fps = max(1.0, cv2.VideoCapture(video_path).get(cv2.CAP_PROP_FPS))
            self.title = ""
            self.setWindowTitle(f"Lupi{self.title}")
            self.setWindowIcon(QIcon(APPICON))

        # Estado
        self.playing = False
        self.playback_speed = 1.0
        self.slider_dragging = False
        self.last_highlight_index = -1
        self.syncing_from_logs = False  # evita bucles
        
        # Video
        self.cap = cv2.VideoCapture(video_path)
        self.total_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))

        # Logs
        self.log_times = [t for t, _ in self.logs]
        self.log_lines = [s for _, s in self.logs]

        # ---------- UI ----------
        menubar = QMenuBar(self)
        file_menu = QMenu("File", self)
        menubar.addMenu(file_menu)

        new_action = QAction("New analysis", self)
        def restart_analysis():
            self.close()
            from synclogs4 import FileSelector  # Import aquí para evitar import circular si usas el mismo archivo
            selector = FileSelector(testing_mode=testing)
            selector.show()
        new_action.triggered.connect(restart_analysis)
        file_menu.addAction(new_action)

        open_lupi_action = QAction("Open synced logs", self)
        open_lupi_action.triggered.connect(self.open_lupi_analysis)
        file_menu.addAction(open_lupi_action)
        self.setMenuBar(menubar)

        export_action = QAction("Export synced logs", self)
        export_action.triggered.connect(self.export_current_analysis)
        file_menu.addAction(export_action)
                
        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(QApplication.instance().quit)
        file_menu.addAction(exit_action)        
                
        help_menu = QMenu("Help", self)
        menubar.addMenu(help_menu) 
        about_action = QAction("About", self)
        about_action.triggered.connect(self.show_about_dialog)
        help_menu.addAction(about_action)
                
        self.log_table = QTableView()
        self.log_model = QStandardItemModel(len(self.log_lines), 2)
        self.log_model.setHorizontalHeaderLabels(["Timestamp", "Console output"])
        
        video_end_time = self.video_start_time + datetime.timedelta(seconds=self.total_frames / self.fps)
        
        for row, (t, msg) in enumerate(self.logs):
            ts_item = QStandardItem(t.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3])
            ts_item.setEditable(False)
            
            clean_msg = re.sub(r"^\[\d{4}\.\d{2}\.\d{2}-\d{2}\.\d{2}\.\d{2}:\d+\]\s*", "", msg)

            msg_item = QStandardItem(clean_msg)
            msg_item.setEditable(False)
            
            if "unhandled exception" in clean_msg.lower():
                ts_item.setBackground(QColor("#2b0000"))  # fondo rojo muy oscuro
                msg_item.setBackground(QColor("#2b0000"))
                ts_item.setForeground(QColor("#ff9999"))  # texto rojo claro
                msg_item.setForeground(QColor("#ff9999"))
            elif self.video_start_time <= t <= video_end_time:
                ts_item.setBackground(QColor("#161600"))  # oscuro amarillento
                msg_item.setBackground(QColor("#161600"))
                ts_item.setForeground(QColor("#DCD69F"))  # texto amarillo claro
                msg_item.setForeground(QColor("#DCD69F"))
            
            self.log_model.setItem(row, 0, ts_item)
            self.log_model.setItem(row, 1, msg_item)
        
        self.log_table.setModel(self.log_model)
        self.log_table.verticalHeader().setVisible(False)
        self.log_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.log_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.log_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.log_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.log_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.log_table.setFont(QFont('Segoe UI', 9))
        self.log_table.setStyleSheet("""
            QTableView {
                background-color: #000000;
                color: #DDDDDD;
                gridline-color: #444444;
                selection-background-color: #00A00D;
                selection-color: #000000;
            }
            QHeaderView::section {
                background-color: #111111;
                color: #CCCCCC;
                padding: 4px;
                border: 1px solid #333333;
            }
            QScrollBar:vertical {width: 30px; height: 80px}
        """)
        self.log_table.verticalScrollBar().valueChanged.connect(self.on_log_scroll)

        self.info_label = QLabel("UTC: —    Frame: —")
        self.info_label.setAlignment(Qt.AlignCenter)
        self.info_label.setFont(QFont("Consolas", 14, QFont.Bold))

        left_layout = QVBoxLayout()
        left_layout.addWidget(self.log_table, stretch=8)
        left_layout.addWidget(self.info_label, stretch=1)
        left_widget = QWidget()
        left_widget.setLayout(left_layout)

        self.video_label = QLabel()
        self.video_label.setAlignment(Qt.AlignCenter)
        self.video_label.setStyleSheet("background-color: black;")
        self.video_label.setMinimumWidth(220)
        right_layout = QVBoxLayout()
        right_layout.addWidget(self.video_label)
        right_widget = QWidget()
        right_widget.setLayout(right_layout)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)
        splitter.setSizes([self.width() // 2, self.width() // 2])

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, max(0, self.total_frames - 1))
        self.slider.sliderPressed.connect(self.slider_start_drag)
        self.slider.sliderReleased.connect(self.slider_end_drag)
        self.slider.sliderMoved.connect(self.slider_drag_move)

        self.btn_start = QPushButton("")
        self.btn_start.setIcon(QIcon(RESTART_ICON_PATH))
        self.btn_start.setIconSize(QSize(32, 32))
        
        self.btn_back = QPushButton("")
        self.btn_back.setIcon(QIcon(MINUS2_ICON_PATH))
        self.btn_back.setIconSize(QSize(32, 32))
        
        self.btn_play = QPushButton("")
        self.btn_play.setIcon(QIcon(PLAY_ICON_PATH))
        self.btn_play.setIconSize(QSize(32, 32))
        
        self.btn_fwd = QPushButton("")
        self.btn_fwd.setIcon(QIcon(PLUS2_ICON_PATH))
        self.btn_fwd.setIconSize(QSize(32, 32))
        
        self.btn_end = QPushButton()
        self.btn_end.setIcon(QIcon(END_ICON_PATH))
        self.btn_end.setIconSize(QSize(32, 32))
        self.btn_end.clicked.connect(self.go_to_end)
        
        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setFrameShadow(QFrame.Sunken)
        
        self.btn_norm = QPushButton("")
        self.btn_norm.setIcon(QIcon(ONEX_ICON_PATH))
        self.btn_norm.setIconSize(QSize(32, 32))
        
        self.btn_half = QPushButton("")
        self.btn_half.setIcon(QIcon(HALFX_ICON_PATH))
        self.btn_half.setIconSize(QSize(32, 32))
        
        self.btn_quarter = QPushButton("")
        self.btn_quarter.setIcon(QIcon(POINT2X_ICON_PATH))
        self.btn_quarter.setIconSize(QSize(32, 32))
        
        self.speed_buttons = [self.btn_norm, self.btn_half, self.btn_quarter]
        
        controls = QHBoxLayout()
        controls.addStretch(1)
        for b in [self.btn_start, self.btn_back, self.btn_play, self.btn_fwd, self.btn_end]:
            controls.addWidget(b)
        controls.addSpacing(80)
        controls.addWidget(sep)
        controls.addSpacing(80)
        
        for b in [self.btn_norm, self.btn_half, self.btn_quarter]:
            controls.addWidget(b)

        controls.addStretch(1)
        
        self.btn_start.clicked.connect(self.go_to_start)
        self.btn_back.clicked.connect(lambda: self.seek_relative(-2))
        self.btn_fwd.clicked.connect(lambda: self.seek_relative(2))
        self.btn_play.clicked.connect(self.toggle_play)
        self.btn_end.clicked.connect(self.go_to_end)
        self.btn_norm.clicked.connect(lambda: self.set_speed(1.0))
        self.btn_half.clicked.connect(lambda: self.set_speed(0.5))
        self.btn_quarter.clicked.connect(lambda: self.set_speed(0.25))

        layout = QVBoxLayout()
        layout.addWidget(splitter)
        layout.addWidget(self.slider)
        layout.addLayout(controls)
        
        central_widget = QWidget()
        central_widget.setLayout(layout)
        self.setCentralWidget(central_widget)

        self.timer = QTimer()
        self.timer.timeout.connect(self.update_frame)

        self.set_speed(1.0)

    # --- Sincronización desde logs ---
    def show_about_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("About Lupi")
        dialog.setFixedSize(400, 280)

        layout = QVBoxLayout(dialog)

        # Label de atribuciones
        attribution_label = QLabel("Lupi/Synclogs 1.1", dialog)
        attribution_label.setStyleSheet("font-size: 20px")
        attribution_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(attribution_label)
        attribution_label2 = QLabel("© 2025 KovaTools. I forgor what I was supposed to write here.", dialog)
        attribution_label2.setAlignment(Qt.AlignCenter)
        attribution_label2.setFont(QFont("Segoe UI, 12"))
        layout.addWidget(attribution_label2)

        # Imágenes (100x100 cada una)
        img1 = QLabel(dialog)
        img1.setPixmap(QPixmap(resource_path("bcspoingus.png")).scaled(100, 100, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        img1.setAlignment(Qt.AlignCenter)
        img1.setStyleSheet("border-color: gray")

        img2 = QLabel(dialog)
        img2.setPixmap(QPixmap(resource_path("synclogs128.png")).scaled(100, 100, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        img2.setAlignment(Qt.AlignCenter)
        img2.setStyleSheet("border-color: gray")

        img_layout = QHBoxLayout()
        img_layout.addWidget(img1)
        img_layout.addWidget(img2)
        layout.addLayout(img_layout)

        # Botón OK
        ok_button = QPushButton("OK", dialog)
        ok_button.clicked.connect(dialog.accept)
        ok_button.setFixedHeight(52)
        ok_button.setFixedWidth(120)
        ok_button.setFont(QFont("Segoe UI", 18))
        ok_button.setStyleSheet("""
            QPushButton {
                background-color: qlineargradient(
                    spread:pad, 
                    x1:0, y1:0, x2:1, y2:0, 
                    stop:0 #00cfff, 
                    stop:1 #8a2be2
                );
                color: white;
                border: none;
                padding: 6px 20px;
                border-radius: 8px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: qlineargradient(
                    spread:pad, 
                    x1:0, y1:0, x2:1, y2:0, 
                    stop:0 #00b5e0, 
                    stop:1 #7a23d9
                );
            }
            QPushButton:pressed {
                background-color: qlineargradient(
                    spread:pad, 
                    x1:0, y1:0, x2:1, y2:0, 
                    stop:0 #009cc1, 
                    stop:1 #691cbf
                );
            }
        """)
        layout.addWidget(ok_button, alignment=Qt.AlignCenter)

        dialog.exec()
    
    def on_log_scroll(self):
        if self.syncing_from_logs:
            return
        first_row = self.log_table.rowAt(0)
        if 0 <= first_row < len(self.log_times):
            target_time = self.log_times[first_row]
            video_seconds = (target_time - self.video_start_time).total_seconds()
            if video_seconds < 0:
                return
            frame = int(video_seconds * self.fps)
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, frame)
            self.slider.setValue(frame)
            self.update_info_label(video_seconds, frame)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Space:
            self.toggle_play()

    def update_timer_interval(self):
        interval = int(1000 / (self.fps * self.playback_speed))
        self.timer.setInterval(interval)

    def toggle_play(self):
        if not self.playing:
            # Si está parado y en el último frame, volver al inicio
            if self.get_current_frame() >= self.total_frames - 1:
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                self.slider.setValue(0)
                self.update_log_highlight(0)
        self.playing = not self.playing
        self.btn_play.setIcon(QIcon(PLAY_ICON_PATH if not self.playing else PAUSE_ICON_PATH))
        if self.playing and not self.timer.isActive():
            self.timer.start()

    def set_speed(self, speed):
        self.playback_speed = speed
        self.update_timer_interval()
        for btn in self.speed_buttons:
            btn.setStyleSheet("")
        if speed == 1.0:
            self.speed_buttons[0].setStyleSheet("background-color: #007BFF; color: white;")
        elif speed == 0.5:
            self.speed_buttons[1].setStyleSheet("background-color: #007BFF; color: white;")
        elif speed == 0.25:
            self.speed_buttons[2].setStyleSheet("background-color: #007BFF; color: white;")

    def seek_relative(self, seconds):
        frame_shift = int(seconds * self.fps)
        new_frame = max(0, min(self.total_frames - 1, self.get_current_frame() + frame_shift))
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, new_frame)
        self.slider.setValue(new_frame)
        self.update_log_highlight(new_frame / self.fps)

    def go_to_start(self):
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        self.slider.setValue(0)
        self.update_log_highlight(0)

    def go_to_end(self):
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, self.total_frames - 1)
        self.slider.setValue(self.total_frames - 1)
        self.update_log_highlight(self.total_frames / self.fps)

    def slider_start_drag(self):
        self.slider_dragging = True

    def slider_end_drag(self):
        self.slider_dragging = False
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, self.slider.value())
        self.update_log_highlight(self.slider.value() / self.fps)

    def slider_drag_move(self, frame):
        self.update_log_highlight(frame / self.fps)

    def get_current_frame(self):
        return int(self.cap.get(cv2.CAP_PROP_POS_FRAMES))

    def highlight_log_line(self, index: int):
        if index == self.last_highlight_index or index < 0 or index >= len(self.log_lines):
            return
        self.last_highlight_index = index

        self.syncing_from_logs = True
        self.log_table.clearSelection()
        self.log_table.selectRow(index)
        self.log_table.scrollTo(self.log_model.index(index, 0), QTableView.PositionAtCenter)
        self.syncing_from_logs = False

    def update_info_label(self, video_seconds: float, frame_number: int):
        current_utc = self.video_start_time + datetime.timedelta(seconds=video_seconds)
        deltaInaccuracy = (1 / self.fps) * 1000
        ts = current_utc.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        self.info_label.setText(
            f"UTC: {ts} ±{deltaInaccuracy:.2f}ms   Frame: {frame_number}"
        )

    def update_log_highlight(self, video_seconds):
        current_video_time = self.video_start_time + datetime.timedelta(seconds=video_seconds)
        idx = bisect.bisect_right(self.log_times, current_video_time)-1
        self.prevIdx = idx
        
        if idx >= len(self.log_times) or current_video_time >= self.log_times[-1]:
            self.highlight_log_line(idx)
        elif idx > 0:
            self.highlight_log_line(idx)

    def update_frame(self):
        if self.slider_dragging:
            return
        if self.playing:
            ret, frame = self.cap.read()
        else:
            return
        if not ret:
            # Video terminado
            self.playing = False
            self.btn_play.setIcon(QIcon(PLAY_ICON_PATH))
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, self.total_frames - 1)
            self.slider.setValue(self.total_frames - 1)
            self.timer.stop()
            return
        # Letterbox
        label_w = self.video_label.width()
        label_h = self.video_label.height()
        frame_h, frame_w = frame.shape[:2]
        scale = min(label_w / frame_w, label_h / frame_h)
        new_w = max(1, int(frame_w * scale))
        new_h = max(1, int(frame_h * scale))
        resized_frame = cv2.copyMakeBorder(
            cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA),
            top=(label_h - new_h) // 2,
            bottom=(label_h - new_h + 1) // 2,
            left=(label_w - new_w) // 2,
            right=(label_w - new_w + 1) // 2,
            borderType=cv2.BORDER_CONSTANT,
            value=(0, 0, 0)
        )
        rgb_image = cv2.cvtColor(resized_frame, cv2.COLOR_BGR2RGB)
        qt_image = QImage(rgb_image.data, rgb_image.shape[1], rgb_image.shape[0],
                          rgb_image.strides[0], QImage.Format_RGB888)
        self.video_label.setPixmap(QPixmap.fromImage(qt_image))
        current_frame = self.get_current_frame()
        self.slider.setValue(current_frame)
        self.update_log_highlight(current_frame / self.fps)
        current_video_time = self.video_start_time + datetime.timedelta(seconds=current_frame / self.fps)
        deltaInaccuracy = (1/self.fps)*1000
        self.info_label.setText(
            f"UTC: {current_video_time.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} ±{deltaInaccuracy:.2f}ms   Frame: {current_frame}"
        )

    def export_current_analysis(self):
        out_path, _ = QFileDialog.getSaveFileName(self, "Export synced logs", "", "Lupi Analysis (*.lupi)")
        if not out_path:
            return
        if not out_path.lower().endswith(".lupi"):
            out_path += ".lupi"

        progress_dialog = ExportProgressDialog(self)
        progress_dialog.show()

        export_analysis(self, out_path, progress_dialog)

        progress_dialog.close()
        QMessageBox.information(self, "Export complete!", f"File saved as:\n{out_path}")

    def open_lupi_analysis(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select synced logs file", "", "Lupi Analysis (*.lupi)")
        if path:
            title = str(os.path.basename(path))
            video_path, logs, original_logs, video_start_time, fps = import_analysis(path)
            self.hide()
            self.player = LogVideoPlayer(video_path, logs, original_logs, video_start_time, fps, title)
        self.player.showMaximized()

# ------------------- PANTALLA INICIAL -------------------

class FileSelector(QWidget):
    def __init__(self, testing_mode):
        super().__init__()
        self.setWindowTitle("Lupi | Select Video and Log file")
        self.setFixedSize(500, 300)
        self.setWindowIcon(QIcon(APPICON))
        self.selected_video = None
        self.selected_log = None
        self.video_times = None
        self.logs = None
        self.flaggy = testing_mode
               
        videoicon = QIcon(VIDEO_ICON_PATH)
        logsicon = QIcon(LOG_ICON_PATH)

        self.log_label = QLabel("No file selected")
        self.log_label.setStyleSheet("color: gray")
        self.log_label.setAlignment(Qt.AlignCenter)
        self.video_label = QLabel("No file selected")
        self.video_label.setStyleSheet("color: gray")
        self.video_label.setAlignment(Qt.AlignCenter)
        self.status_label = QLabel("Select files to analyse")
        self.status_label.setStyleSheet("color: red; font-size: 16px")
        self.status_label.setAlignment(Qt.AlignCenter)

        btn_log = QPushButton("Open Log File")
        btn_log.setIcon(logsicon)
        btn_log.setFixedHeight(40)
        btn_log.setIconSize(QSize(36,36))
        btn_log.clicked.connect(self.select_log)
        btn_video = QPushButton("Open Video File")
        btn_video.setIcon(videoicon)
        btn_video.setFixedHeight(40)
        btn_video.setIconSize(QSize(36,36))
        btn_video.clicked.connect(self.select_video)

        self.start_btn = QPushButton("Start analysis")
        self.start_btn.setEnabled(False)
        self.start_btn.setFixedSize(180,42)
        self.start_btn.setStyleSheet("font-size: 16px;")
        self.start_btn.clicked.connect(self.start_player)

        left_layout = QVBoxLayout()
        left_layout.addWidget(self.log_label)
        left_layout.addWidget(btn_log)
        left_widget = QWidget()
        left_widget.setLayout(left_layout)
        left_widget.setStyleSheet("border: 2px solid #2b2b2b; border-radius: 6px")

        right_layout = QVBoxLayout()
        right_layout.addWidget(self.video_label)
        right_layout.addWidget(btn_video)
        right_widget = QWidget()
        right_widget.setLayout(right_layout)
        right_widget.setStyleSheet("border: 2px solid #2b2b2b; border-radius: 6px")
        
        top_layout = QHBoxLayout()
        top_layout.addWidget(left_widget)
        top_layout.addWidget(right_widget)
    
        bottom_left_layout = QVBoxLayout()
        bottom_left_layout.addWidget(self.start_btn)
        
        self.import_label = QLabel("or")
        self.import_label.setStyleSheet("color: gray; font-size: 20px")
        self.import_label.setAlignment(Qt.AlignCenter)
        
        self.import_btn = QPushButton("Import file")
        self.import_btn.setEnabled(True)
        self.import_btn.setFixedSize(180,42)
        self.import_btn.setStyleSheet("font-size: 16px; color: black; background-color: #348feb;")
        self.import_btn.clicked.connect(self.open_lupi_from_selector)
        
        bottom_right_layout = QHBoxLayout()
        bottom_right_layout.addWidget(self.import_label)
        bottom_right_layout.addWidget(self.import_btn)
        
        bottom_layout = QHBoxLayout()
        bottom_layout.addLayout(bottom_left_layout)
        bottom_layout.addLayout(bottom_right_layout)
        
        layout = QVBoxLayout()
        layout.addLayout(top_layout,stretch=10)
        layout.addWidget(self.status_label)
        layout.addLayout(bottom_layout)
        self.setLayout(layout)

    def select_log(self):
        if self.flaggy:
            path = LOG_PATH
        else:
            path, _ = QFileDialog.getOpenFileName(self, "Select Log File", "C:/", "Logging file (*.log)")
        if path:
            self.selected_log = path
            self.logs, self.original_lines = parse_logs(path)
            self.log_label.setWordWrap(True)
            self.log_label.setText(os.path.basename(path))
            self.check_compatibility()

    def select_video(self):
        if self.flaggy:
            path = VIDEO_PATH
        else:
            start_dir = os.path.join(os.path.expanduser("~"), "Videos")
            path, _ = QFileDialog.getOpenFileName(self, "Select Video File", start_dir,"Video Files (*.mp4 *.mkv *.mpg *.mov)")
        if path:
            self.selected_video = path
            self.video_start_time = get_file_creation_time_utc(path)
            self.video_label.setText(os.path.basename(path))
            self.check_compatibility()

    def check_compatibility(self):
        if self.selected_video and self.selected_log:
            log_times = [t for t, _ in self.logs]
            if log_times and min(log_times) <= self.video_start_time <= max(log_times):
                self.status_label.setStyleSheet("color: green; font-size: 16px")
                self.status_label.setText("Files are synchronous")
                self.start_btn.setEnabled(True)
                self.start_btn.setStyleSheet("background-color: green; font-size: 16px; color: white")
            else:
                self.status_label.setStyleSheet("color: red; font-size: 16px")
                self.status_label.setText("Files not synchronous")
                self.start_btn.setEnabled(False)

    def start_player(self):
        self.close()
        self.player = LogVideoPlayer(self.selected_video, self.selected_log, title="")
        self.player.showMaximized()

    def open_lupi_from_selector(self, fileFlag=False, open_from=None):
        if not fileFlag:
            path, _ = QFileDialog.getOpenFileName(self, "Select synced logs file", "", "Lupi Analysis (*.lupi)")
            title = str(os.path.basename(path))
            if path:
                video_path, logs, original_logs, video_start_time, fps = import_analysis(path)
                self.close()
                player = LogVideoPlayer(video_path, logs, original_logs, video_start_time, fps, title)
                player.showMaximized()
        else:
            path = open_from
            title = str(os.path.basename(path))
            if path:
                video_path, logs, original_logs, video_start_time, fps = import_analysis(path)
                player = LogVideoPlayer(video_path, logs, original_logs, video_start_time, fps, title)
                player.showMaximized()

# ------------------- BARRA DE PROGRESO DE EXPORTACIÓN -------------------

class ExportProgressDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Exporting Lupi...")
        self.setFixedSize(300, 120)

        layout = QVBoxLayout(self)

        self.label = QLabel("Starting export...", self)
        self.label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.label)

        self.progress_bar = QProgressBar(self)
        self.progress_bar.setRange(0, 4)  # 4 pasos
        layout.addWidget(self.progress_bar)

    def update_step(self, step, message):
        self.progress_bar.setValue(step)
        self.label.setText(message)
        QApplication.processEvents()

# ------------------- MAIN -------------------
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(APPICON))
    qdarktheme.setup_theme()
    testing = False
    ffile = True
    try:
        if sys.argv[1] and (os.path.splitext(sys.argv[1])[1] == '.lupi'):
            try:
                open_from_file = pathlib.Path(sys.argv[1])
                FileSelector.open_lupi_from_selector(FileSelector, fileFlag=ffile, open_from=open_from_file)
            except Exception as e:
                print(e)
                open_from_file = None
    except:
        open_from = None
        selector = FileSelector(testing_mode=testing)
        selector.show()
    sys.exit(app.exec())