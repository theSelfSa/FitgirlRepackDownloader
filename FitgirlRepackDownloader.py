#!/usr/bin/env python3
"""
FitGirl Repacks Downloader - Definitive Version
A modern, multi-threaded downloader with a data-driven, glassmorphism UI,
and full download controls, including session persistence.
"""
import os
import re
import sys
import time
import json
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup
from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtCore import Qt
import ctypes

def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)

# --- Global Configuration ---
DOWNLOADS_FOLDER = os.path.join(os.path.expanduser("~"), "Downloads")
SESSION_FILE = "session.json"
HEADERS = {
    'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
    'accept-language': 'en-US,en;q=0.5',
    'referer': 'https://fitgirl-repacks.site/',
    'user-agent': "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
}

# --- Downloader Worker Thread (Stable) ---
class DownloaderWorker(QtCore.QThread):
    log_signal = QtCore.pyqtSignal(str)
    file_progress_signal = QtCore.pyqtSignal(int)
    overall_progress_signal = QtCore.pyqtSignal(int, int)
    file_info_signal = QtCore.pyqtSignal(str, int)
    speed_signal = QtCore.pyqtSignal(float)
    link_started_signal = QtCore.pyqtSignal(str)
    link_completed_signal = QtCore.pyqtSignal(str)
    link_failed_signal = QtCore.pyqtSignal(str)

    def __init__(self, links, download_folder, parent=None):
        super().__init__(parent)
        self.links = links
        self.download_folder = download_folder
        self.active = True
        self.is_paused = False
        self._lock = QtCore.QMutex()

    def stop(self):
        self.active = False

    def toggle_pause(self):
        with QtCore.QMutexLocker(self._lock):
            self.is_paused = not self.is_paused
        self.log_signal.emit("⏸️ Download Paused." if self.is_paused else "▶️ Download Resumed.")

    def run(self):
        os.makedirs(self.download_folder, exist_ok=True)
        self.log_signal.emit("🚀 Starting download session...")
        total_links = len(self.links)
        
        for idx, link in enumerate(self.links.copy()):
            if not self.active:
                self.log_signal.emit("🛑 Download Stopped by user.")
                break
            try:
                self.link_started_signal.emit(link)
                self.log_signal.emit(f"🔗 Processing Link {idx + 1}/{total_links}...")
                file_name, download_url = self.process_link(link)
                output_path = os.path.join(self.download_folder, file_name)
                with requests.head(download_url, headers=HEADERS, timeout=10) as head:
                    total_size = int(head.headers.get('content-length', 0))
                self.file_info_signal.emit(file_name, total_size)
                self.log_signal.emit(f"🔽 Starting: {file_name} ({total_size / (1024*1024):.2f} MB)")
                self.download_file(download_url, output_path, total_size)
                if self.active:
                    self.log_signal.emit(f"✅ Finished: {file_name}")
                    self.link_completed_signal.emit(link)
            except Exception as e:
                self.log_signal.emit(f"❌ Error on Link {idx + 1}: {e}")
                self.link_failed_signal.emit(link)
            finally:
                if self.active:
                    self.overall_progress_signal.emit(idx + 1, total_links)
            time.sleep(2)
        
        self.log_signal.emit("🏁 Session finished.")

    def _check_pause(self):
        while True:
            with QtCore.QMutexLocker(self._lock):
                if not self.is_paused: break
            time.sleep(0.1)

    def process_link(self, link):
        self._check_pause()
        if not self.active: raise Exception("Session stopped.")
        try:
            response = requests.get(link, headers=HEADERS, timeout=30)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            return self.extract_filename(soup, link), self.extract_download_url(soup)
        except requests.HTTPError as e:
            raise Exception(f"HTTP Error {e.response.status_code}")
        except Exception as e:
            raise Exception(f"Could not process link page: {e}")

    def download_file(self, url, path, total_size):
        if total_size > 4 * 1024 * 1024 and 'bytes' in requests.head(url, headers=HEADERS, timeout=10).headers.get('Accept-Ranges', ''):
            self.chunked_download(url, path, total_size)
        else:
            self.single_thread_download(url, path, total_size)

    def chunked_download(self, url, path, total_size):
        chunk_size = 4 * 1024 * 1024
        with open(path, 'wb') as f: f.truncate(total_size)
        downloaded = 0
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(self.download_chunk, url, start, min(start + chunk_size, total_size), path): start for start in range(0, total_size, chunk_size)}
            for future in as_completed(futures):
                self._check_pause()
                if not self.active: executor.shutdown(wait=False, cancel_futures=True); break
                chunk_data_size = future.result()
                if chunk_data_size: downloaded += chunk_data_size; self.update_speed_metrics(downloaded, time.time())

    def download_chunk(self, url, start, end, path):
        headers = {**HEADERS, 'Range': f'bytes={start}-{end-1}'}
        for attempt in range(3):
            self._check_pause()
            if not self.active: return None
            try:
                with requests.get(url, headers=headers, stream=True, timeout=20) as r:
                    r.raise_for_status()
                    with self._lock:
                        with open(path, 'r+b') as f: f.seek(start); f.write(r.content)
                    return len(r.content)
            except Exception:
                time.sleep(1)
        return None

    def single_thread_download(self, url, path, total_size):
        downloaded = 0
        with requests.get(url, stream=True, timeout=20) as r:
            r.raise_for_status()
            with open(path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    self._check_pause()
                    if not self.active: break
                    f.write(chunk); downloaded += len(chunk); self.update_speed_metrics(downloaded, time.time())
    
    def update_speed_metrics(self, downloaded, current_time):
        self.file_progress_signal.emit(downloaded)
        if not hasattr(self, '_last_update_time'): self._last_update_time = current_time; self._last_downloaded = downloaded
        time_diff = current_time - self._last_update_time
        if time_diff > 0.5:
            speed = (downloaded - self._last_downloaded) / time_diff; self.speed_signal.emit(speed)
            self._last_update_time = current_time; self._last_downloaded = downloaded

    def extract_filename(self, soup, fallback_url):
        try: return re.sub(r'[\\/*?:"<>|]', "", soup.find('meta', {'name': 'title'})['content'])
        except: return os.path.basename(fallback_url).split("?")[0]

    def extract_download_url(self, soup):
        for script in soup.find_all('script'):
            if 'function download' in script.text:
                match = re.search(r'window\.open\(["\'](https?://[^\s"\'\)]+)', script.text)
                if match: return match.group(1)
        raise Exception("Download URL not found in page.")

# --- Background Container for Perfect Rounded Corners ---
class BackgroundContainer(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setBrush(QtGui.QColor("#1E1F22"))
        painter.setPen(QtGui.QColor("#444"))
        painter.drawRoundedRect(self.rect(), 15, 15)

# --- Main Application Window ---
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("FitGirl Repacks Downloader")
        self.setWindowIcon(QtGui.QIcon(resource_path("fitgirl.ico")))
        self.setMinimumSize(950, 750)
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAcceptDrops(True)
        self.download_folder = DOWNLOADS_FOLDER
        self.input_file_path = ""
        self.worker = None
        self.init_ui()
        self.apply_stylesheet()
        self.load_session()

    def init_ui(self):
        self.container = BackgroundContainer()
        self.setCentralWidget(self.container)
        
        main_layout = QtWidgets.QVBoxLayout(self.container)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(15)

        self.tray_icon = QtWidgets.QSystemTrayIcon(self.style().standardIcon(QtWidgets.QStyle.SP_ComputerIcon), self)
        self.tray_icon.show()

        top_bar_layout = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("FitGirl Repacks Downloader")
        title.setObjectName("windowTitle")
        self.close_btn = QtWidgets.QPushButton("✕")
        self.close_btn.setObjectName("controlBtn")
        top_bar_layout.addWidget(title); top_bar_layout.addStretch(); top_bar_layout.addWidget(self.close_btn)

        controls_group = QtWidgets.QGroupBox("SETUP")
        controls_layout = QtWidgets.QGridLayout(controls_group)
        self.input_file_le = QtWidgets.QLineEdit(placeholderText="Select a link file, paste, or drag & drop...")
        self.browse_input_btn = QtWidgets.QPushButton("Browse File...")
        self.paste_btn = QtWidgets.QPushButton("Paste Links")
        self.save_dir_le = QtWidgets.QLineEdit(self.download_folder)
        self.browse_save_btn = QtWidgets.QPushButton("Browse...")
        
        source_layout = QtWidgets.QHBoxLayout()
        source_layout.addWidget(self.input_file_le); source_layout.addWidget(self.browse_input_btn); source_layout.addWidget(self.paste_btn)
        dir_layout = QtWidgets.QHBoxLayout()
        dir_layout.addWidget(self.save_dir_le); dir_layout.addWidget(self.browse_save_btn)
        
        controls_layout.addWidget(QtWidgets.QLabel("Source:"), 0, 0); controls_layout.addLayout(source_layout, 0, 1)
        controls_layout.addWidget(QtWidgets.QLabel("Save Directory:"), 1, 0); controls_layout.addLayout(dir_layout, 1, 1)

        content_layout = QtWidgets.QHBoxLayout()
        queue_group = QtWidgets.QGroupBox("DOWNLOAD QUEUE")
        self.list_widget = QtWidgets.QListWidget()
        queue_layout = QtWidgets.QVBoxLayout(queue_group); queue_layout.addWidget(self.list_widget)

        logs_group = QtWidgets.QGroupBox("LOGS")
        self.log_text = QtWidgets.QTextEdit(readOnly=True)
        logs_layout = QtWidgets.QVBoxLayout(logs_group); logs_layout.addWidget(self.log_text)
        content_layout.addWidget(queue_group, 1); content_layout.addWidget(logs_group, 2)

        status_group = QtWidgets.QGroupBox("STATUS")
        status_layout = QtWidgets.QGridLayout(status_group)
        self.file_label = QtWidgets.QLabel("Current File: None")
        self.speed_label = QtWidgets.QLabel("Speed: 0.00 MB/s")
        self.file_progress_bar = QtWidgets.QProgressBar()
        self.overall_progress_bar = QtWidgets.QProgressBar()
        
        # --- Corrected Status Layout ---
        status_layout.addWidget(self.file_label, 0, 0, 1, 1)
        status_layout.addWidget(self.speed_label, 0, 1, 1, 1, alignment=Qt.AlignRight)
        status_layout.addWidget(QtWidgets.QLabel("File Progress:"), 1, 0)
        status_layout.addWidget(self.file_progress_bar, 1, 1)
        status_layout.addWidget(QtWidgets.QLabel("Overall Progress:"), 2, 0)
        status_layout.addWidget(self.overall_progress_bar, 2, 1)

        self.status_label = QtWidgets.QLabel("👋 Welcome! Load links or drag & drop a .txt file.")
        self.status_label.setObjectName("statusBar")
        
        self.clear_btn = QtWidgets.QPushButton("Clear"); self.clear_btn.setObjectName("actionButton")
        self.download_btn = QtWidgets.QPushButton("START DOWNLOAD"); self.download_btn.setObjectName("actionButton")
        self.pause_btn = QtWidgets.QPushButton("Pause"); self.pause_btn.setObjectName("actionButton")
        self.stop_btn = QtWidgets.QPushButton("Stop"); self.stop_btn.setObjectName("actionButton")

        self.action_stack = QtWidgets.QStackedLayout()
        pause_stop_widget = QtWidgets.QWidget()
        pause_stop_layout = QtWidgets.QHBoxLayout(pause_stop_widget)
        pause_stop_layout.setContentsMargins(0,0,0,0); pause_stop_layout.setSpacing(10)
        pause_stop_layout.addWidget(self.pause_btn); pause_stop_layout.addWidget(self.stop_btn)
        self.action_stack.addWidget(self.download_btn); self.action_stack.addWidget(pause_stop_widget)
        
        bottom_actions_layout = QtWidgets.QHBoxLayout()
        bottom_actions_layout.addWidget(self.clear_btn); bottom_actions_layout.addStretch(); bottom_actions_layout.addLayout(self.action_stack)

        main_layout.addLayout(top_bar_layout); main_layout.addWidget(controls_group); main_layout.addLayout(content_layout, 1); main_layout.addWidget(status_group); main_layout.addLayout(bottom_actions_layout); main_layout.addWidget(self.status_label)
        
        self.close_btn.clicked.connect(self.close)
        self.browse_input_btn.clicked.connect(self.select_input_file)
        self.paste_btn.clicked.connect(self.load_from_clipboard)
        self.clear_btn.clicked.connect(self.clear_session)
        self.browse_save_btn.clicked.connect(self.select_download_directory)
        self.download_btn.clicked.connect(self.start_download)
        self.pause_btn.clicked.connect(self.toggle_pause_resume)
        self.stop_btn.clicked.connect(self.stop_download)

    def apply_stylesheet(self):
        self.setStyleSheet("""
            #container { background-color: transparent; }
            QGroupBox { background-color: rgba(42, 46, 55, 0.7); border: 1px solid rgba(255, 193, 7, 0.2); border-radius: 15px; margin-top: 1ex; font-family: 'Segoe UI Semibold'; font-size: 9pt; color: #FFC107; }
            QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top center; padding: 0 10px; }
            QLabel { color: #E0E0E0; background-color: transparent; font-family: 'Segoe UI'; }
            QLabel#windowTitle { font-size: 11pt; font-weight: bold; color: #E0E0E0; padding-left: 5px; }
            QLabel#statusBar { color: #b0b0b0; font-family: 'Segoe UI Semibold'; padding: 5px; }
            QLineEdit, QListWidget, QTextEdit { background-color: rgba(0, 0, 0, 0.4); border: 1px solid rgba(255, 193, 7, 0.1); border-radius: 8px; color: #E0E0E0; padding: 8px; }
            QListWidget::item:hover, QListWidget::item:selected { background-color: rgba(255, 193, 7, 0.2); border: none; }
            QPushButton { background-color: transparent; color: #FFC107; border: 1px solid #FFC107; border-radius: 8px; padding: 8px; font-family: 'Segoe UI Semibold'; }
            QPushButton:hover { background-color: rgba(255, 193, 7, 0.2); color: #FFFFFF; }
            QPushButton:pressed { background-color: rgba(255, 193, 7, 0.3); }
            QPushButton#actionButton { font-size: 10pt; padding: 10px; min-width: 120px; }
            QPushButton#controlBtn { border: none; font-size: 12pt; color: #888; border-radius: 4px; max-width: 30px; }
            QPushButton#controlBtn:hover { color: #fff; background-color: #E94560; }
            QProgressBar { border: 1px solid rgba(0,0,0,0.2); border-radius: 4px; text-align: center; color: #1E1F22; font-family: 'Segoe UI Semibold'; background-color: rgba(0, 0, 0, 0.3); max-height: 10px; }
            QProgressBar::chunk { background-color: #FFC107; border-radius: 3px; }
            QScrollBar:vertical { border: none; background: transparent; width: 12px; margin: 0px; }
            QScrollBar::handle:vertical { background: #FFC107; min-height: 25px; border-radius: 6px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; background: transparent; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
            QScrollBar:horizontal { border: none; background: transparent; height: 12px; margin: 0px; }
            QScrollBar::handle:horizontal { background: #FFC107; min-width: 25px; border-radius: 6px; }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0px; background: transparent; }
            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }
        """)

    def _update_link_status(self, link_text, prefix, color=None):
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if link_text in item.text():
                base_link = item.text().split(" ", 1)[-1]; item.setText(f"{prefix} {base_link}")
                if color: item.setForeground(color)
                return

    def _populate_queue_with_links(self, links, source_name):
        if not links: self.log(f"⚠️ No links found from {source_name}."); self.show_status_message("⚠️ No valid links found."); return
        self.list_widget.clear()
        for link in links: self.list_widget.addItem(f"🕒 {link}")
        self.log(f"📥 Loaded {len(links)} links from {source_name}.")
        self.show_status_message(f"✅ Successfully loaded {len(links)} links.")

    def show_status_message(self, message, timeout=5000):
        self.status_label.setText(message)
        if timeout > 0: QtCore.QTimer.singleShot(timeout, lambda: self.status_label.setText("👋 Welcome! Load links or drag & drop a .txt file." if not self.worker else self.status_label.text()))

    def select_input_file(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Select Input File", "", "Text Files (*.txt)")
        if path:
            self.input_file_path = path; self.input_file_le.setText(path)
            try:
                with open(self.input_file_path, 'r') as f: links = re.findall(r'https?://[^\s#]+', f.read())
                self._populate_queue_with_links(links, f"file '{os.path.basename(path)}'")
            except Exception as e: self.log(f"❌ Error reading file: {e}")

    def load_from_clipboard(self):
        clipboard = QtWidgets.QApplication.clipboard(); pasted_text = clipboard.text()
        if not pasted_text: self.log("📋 Clipboard is empty."); self.show_status_message("📋 Clipboard is empty."); return
        links = re.findall(r'https?://[^\s#]+', pasted_text)
        self.input_file_le.setText("Loaded from clipboard")
        self._populate_queue_with_links(links, "clipboard")

    def select_download_directory(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Download Directory", self.download_folder)
        if path: self.download_folder = path; self.save_dir_le.setText(path); self.show_status_message(f"📁 Save directory set to: {path}")

    def clear_session(self):
        if self.worker: self.worker.stop()
        self.list_widget.clear(); self.log_text.clear(); self.input_file_le.clear()
        self.on_download_finished(was_cleared=True)
        self.log("🧹 Session cleared. Ready for a fresh start.")
        self.show_status_message("✨ Cleared. Ready for new links.")

    def start_download(self):
        if self.worker and self.worker.isRunning(): return
        links_to_download = [self.list_widget.item(i).text().split(" ", 1)[-1] for i in range(self.list_widget.count()) if not self.list_widget.item(i).text().startswith(("✅", "❌"))]
        if not links_to_download: self.log("✅ All files in the queue are already processed."); return
        self.reset_ui_for_new_download(len(links_to_download))
        self.worker = DownloaderWorker(links_to_download, self.download_folder)
        self.worker.log_signal.connect(self.log)
        self.worker.file_progress_signal.connect(self.update_file_progress)
        self.worker.overall_progress_signal.connect(self.update_overall_progress)
        self.worker.file_info_signal.connect(self.update_file_info)
        self.worker.speed_signal.connect(lambda s: self.speed_label.setText(f"Speed: {s/(1024*1024):.2f} MB/s"))
        self.worker.link_started_signal.connect(lambda link: self._update_link_status(link, "➡️"))
        self.worker.link_completed_signal.connect(lambda link: self._update_link_status(link, "✅", QtGui.QColor("#2ca02c")))
        self.worker.link_failed_signal.connect(lambda link: self._update_link_status(link, "❌", QtGui.QColor("#E94560")))
        self.worker.finished.connect(self.on_download_finished)
        self.worker.start()
        self.action_stack.setCurrentIndex(1)

    def toggle_pause_resume(self):
        if self.worker: self.worker.toggle_pause(); self.pause_btn.setText("Resume" if self.worker.is_paused else "Pause")
    def stop_download(self):
        if self.worker: self.worker.stop()

    def on_download_finished(self, was_cleared=False):
        was_running = self.worker is not None
        if self.worker: self.worker = None
        self.action_stack.setCurrentIndex(0); self.pause_btn.setText("Pause")
        if was_cleared: self.reset_ui_for_new_download(0); return
        if was_running:
            is_complete = self.overall_progress_bar.value() == self.overall_progress_bar.maximum()
            self.file_label.setText("Session Complete!"); self.speed_label.setText("Speed: 0.00 MB/s")
            self.file_progress_bar.setRange(0, 100); self.file_progress_bar.setValue(0)
            if is_complete:
                self.overall_progress_bar.setFormat("Completed!")
                self.tray_icon.showMessage("Download Complete", "All files have been successfully downloaded.", QtWidgets.QSystemTrayIcon.Information, 5000)
            else: self.overall_progress_bar.setFormat("Stopped.")
            self.show_status_message("👍 Ready for next session (or drag & drop a file).", timeout=0)

    def reset_ui_for_new_download(self, total_links):
        self.file_label.setText("Current File: Waiting..."); self.speed_label.setText("Speed: 0.00 MB/s")
        self.file_progress_bar.setRange(0, 100); self.file_progress_bar.setValue(0); self.file_progress_bar.setTextVisible(False)
        if total_links > 0:
            self.overall_progress_bar.setRange(0, total_links); self.overall_progress_bar.setFormat(f"0 / {total_links} Files")
        else:
            self.overall_progress_bar.setRange(0, 100); self.overall_progress_bar.setFormat("Waiting for links...")
        self.overall_progress_bar.setValue(0); self.overall_progress_bar.setTextVisible(True)

    def log(self, message): self.log_text.append(f"[{datetime.now().strftime('%T')}] {message}")
    def update_file_progress(self, downloaded):
        if self.file_progress_bar.maximum() > 0: self.file_progress_bar.setValue(downloaded)

    def update_overall_progress(self, completed, total):
        self.overall_progress_bar.setValue(completed); self.overall_progress_bar.setFormat(f"{completed} / {total} Files")

    def update_file_info(self, filename, total_size):
        self.file_label.setText(f"Downloading: {filename[:40]}..."); self.file_progress_bar.setRange(0, total_size if total_size > 0 else 0)
        self.file_progress_bar.setValue(0); self.file_progress_bar.setTextVisible(total_size > 0); self.file_progress_bar.setFormat("%p%")

    def save_session(self):
        session_data = []
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i); text = item.text()
            status, link = text.split(" ", 1); session_data.append({"status": status, "link": link})
        try:
            with open(SESSION_FILE, 'w') as f: json.dump(session_data, f, indent=4)
            self.log("💾 Session saved.")
        except Exception as e: self.log(f"❌ Could not save session: {e}")

    def load_session(self):
        if not os.path.exists(SESSION_FILE): self.log("ℹ️ No previous session found."); return
        try:
            with open(SESSION_FILE, 'r') as f: session_data = json.load(f)
            self.list_widget.clear()
            status_map = {"✅": QtGui.QColor("#2ca02c"), "❌": QtGui.QColor("#E94560")}
            for item_data in session_data:
                item = QtWidgets.QListWidgetItem(f"{item_data['status']} {item_data['link']}")
                if item_data['status'] in status_map: item.setForeground(status_map[item_data['status']])
                self.list_widget.addItem(item)
            self.log(f"🔄 Session restored with {len(session_data)} links.")
        except Exception as e: self.log(f"❌ Could not load session: {e}")

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and event.mimeData().urls()[0].isLocalFile():
            if event.mimeData().urls()[0].toLocalFile().endswith('.txt'): event.acceptProposedAction()

    def dropEvent(self, event):
        file_path = event.mimeData().urls()[0].toLocalFile()
        self.input_file_path = file_path; self.input_file_le.setText(file_path)
        try:
            with open(self.input_file_path, 'r') as f: links = re.findall(r'https?://[^\s#]+', f.read())
            self._populate_queue_with_links(links, f"file '{os.path.basename(file_path)}'")
        except Exception as e: self.log(f"❌ Error reading dropped file: {e}")

    def mousePressEvent(self, event): self.oldPos = event.globalPos()
    def mouseMoveEvent(self, event):
        delta = QtCore.QPoint(event.globalPos() - self.oldPos)
        self.move(self.x() + delta.x(), self.y() + delta.y()); self.oldPos = event.globalPos()
    def closeEvent(self, event): self.save_session(); self.worker.stop() if self.worker else None; event.accept()

if __name__ == "__main__":
    myappid = 'mycompany.myproduct.subproduct.version'
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())
