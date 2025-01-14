import sys
import asyncio
import os
import time
from pathlib import Path
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                            QHBoxLayout, QLabel, QLineEdit, QPushButton, 
                            QProgressBar, QFileDialog, QCheckBox, QRadioButton, 
                            QGroupBox, QComboBox)
from PyQt6.QtCore import QThread, pyqtSignal, Qt, QSettings, QSize
from PyQt6.QtGui import QIcon, QPixmap, QCursor
from GetMetadata import get_metadata
from LucidaDownloader import TrackDownloader

class ImageDownloader(QThread):
    finished = pyqtSignal(bytes)
    
    def __init__(self, url):
        super().__init__()
        self.url = url
        
    def run(self):
        import requests
        response = requests.get(self.url)
        if response.status_code == 200:
            self.finished.emit(response.content)

class MetadataFetcher(QThread):
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)
    
    def __init__(self, url, headless=True, service="tidal", use_fallback=False):
        super().__init__()
        self.url = url
        self.headless_mode = headless
        self.service = service
        self.use_fallback = use_fallback
        self.max_retries = 3

    def extract_track_id(self, url):
        if "track/" in url:
            return url.split("track/")[1].split("?")[0]
        return None

    async def fetch_metadata(self, track_id):
        import zendriver as zd
        from asyncio import sleep
        
        domain = "lucida.su" if self.use_fallback else "lucida.to"
        
        for attempt in range(self.max_retries):
            try:
                lucida_url = f"https://{domain}/?url=https%3A%2F%2Fopen.spotify.com%2Ftrack%2F{track_id}&country=auto&to={self.service}"
                browser = await zd.start(headless=self.headless_mode)
                try:
                    page = await browser.get(lucida_url)
                    return await get_metadata(page)
                finally:
                    await browser.stop()
            except Exception as e:
                if "refused" in str(e).lower() and attempt < self.max_retries - 1:
                    await sleep(2 * (attempt + 1))
                    continue
                raise e

    def run(self):
        try:
            track_id = self.extract_track_id(self.url)
            if not track_id:
                self.error.emit("Invalid Spotify URL")
                return

            metadata = asyncio.run(self.fetch_metadata(track_id))
            if metadata:
                self.finished.emit(metadata)
            else:
                self.error.emit("Failed to fetch track metadata")

        except Exception as e:
            error_msg = str(e)
            if "refused" in error_msg.lower():
                self.error.emit("Connection refused. Please check your internet connection and try again.")
            elif "timeout" in error_msg.lower():
                self.error.emit("Connection timed out. Please check your internet connection and try again.")
            else:
                self.error.emit(f"Error: {error_msg}")

class DownloaderWorker(QThread):
    progress = pyqtSignal(int)
    status = pyqtSignal(str)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)
    
    def __init__(self, metadata, output_dir, filename_format='title_artist', use_fallback=False):
        super().__init__()
        self.metadata = metadata
        self.output_dir = output_dir
        self.filename_format = filename_format
        self.use_fallback = use_fallback
        self.downloader = TrackDownloader(use_fallback=use_fallback)
        self.last_update_time = 0
        self.last_downloaded_size = 0
        
    def format_size(self, size_bytes):
        units = ['B', 'KB', 'MB', 'GB']
        index = 0
        while size_bytes >= 1024 and index < len(units) - 1:
            size_bytes /= 1024
            index += 1
        return f"{size_bytes:.2f}{units[index]}"
        
    def format_speed(self, speed_bytes):
        speed_bits = speed_bytes * 8
        
        if speed_bits >= 1024 * 1024:
            speed_mbps = speed_bits / (1024 * 1024)
            return f"{speed_mbps:.2f}Mbps"
        else:
            speed_kbps = speed_bits / 1024
            return f"{speed_kbps:.2f}Kbps"
        
    def progress_callback(self, downloaded_size, total_size):
        current_time = time.time()
        if current_time - self.last_update_time >= 0.5:
            progress = int((downloaded_size / total_size) * 100) if total_size > 0 else 0
            self.progress.emit(progress)
            
            time_diff = current_time - self.last_update_time
            if time_diff > 0:
                speed = (downloaded_size - self.last_downloaded_size) / time_diff
                status = f"Downloading... {self.format_size(downloaded_size)}/{self.format_size(total_size)} | {self.format_speed(speed)}"
                self.status.emit(status)
            
            self.last_update_time = current_time
            self.last_downloaded_size = downloaded_size
        
    def run(self):
        try:
            self.status.emit("Preparing...")
            self.downloader.set_progress_callback(self.progress_callback)
            self.downloader.set_filename_format(self.filename_format)
            self.progress.emit(0)
            downloaded_file = self.downloader.download(self.metadata, self.output_dir)
            self.progress.emit(100)
            self.finished.emit("Download complete!")
        except Exception as e:
            self.error.emit(f"Error: {str(e)}")

class ServiceComboBox(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setIconSize(QSize(16, 16))
        self.setup_items()
        
    def setup_items(self):
        current_dir = os.path.dirname(os.path.abspath(__file__))
        icons_dir = os.path.join(current_dir, 'icons')
        
        if not os.path.exists(icons_dir):
            os.makedirs(icons_dir)
            
        services = [
            {'id': 'tidal', 'name': 'Tidal', 'icon': 'tidal.png'},
            {'id': 'amazon', 'name': 'Amazon Music', 'icon': 'amazon.png'},
            {'id': 'qobuz', 'name': 'Qobuz', 'icon': 'qobuz.png'},
            {'id': 'deezer', 'name': 'Deezer', 'icon': 'deezer.png'}
        ]
        
        for service in services:
            icon_path = os.path.join(icons_dir, service['icon'])
            if not os.path.exists(icon_path):
                self.create_placeholder_icon(icon_path)
            
            icon = QIcon(icon_path)
            self.addItem(icon, service['name'], service['id'])
    
    def create_placeholder_icon(self, path):
        pixmap = QPixmap(16, 16)
        pixmap.fill(Qt.GlobalColor.transparent)
        pixmap.save(path)

class SpotifyFlacGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = QSettings('SpotifyFlac', 'Settings')
        self.setWindowTitle("Spotify FLAC")
        
        icon_path = os.path.join(os.path.dirname(__file__), "icon.svg")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        
        self.setFixedWidth(600)
        self.setFixedHeight(180)
        
        self.default_music_dir = str(Path.home() / "Music")
        if not os.path.exists(self.default_music_dir):
            os.makedirs(self.default_music_dir)
        
        self.metadata = None
        self.init_ui()
        self.url_input.textChanged.connect(self.validate_url)
        self.load_settings()
        self.setup_settings_persistence()
        
    def load_settings(self):
        headless = self.settings.value('headless', True, type=bool)
        fallback = self.settings.value('fallback', False, type=bool)
        service = self.settings.value('service', 'tidal')
        format_type = self.settings.value('format', 'title_artist')
        output_dir = self.settings.value('output_dir', self.default_music_dir)
        
        self.headless_checkbox.setChecked(headless)
        self.fallback_checkbox.setChecked(fallback)
        
        for i in range(self.service_combo.count()):
            if self.service_combo.itemData(i) == service:
                self.service_combo.setCurrentIndex(i)
                break
                
        self.format_title_artist.setChecked(format_type == 'title_artist')
        self.format_artist_title.setChecked(format_type == 'artist_title')
        self.dir_input.setText(output_dir)
        
    def setup_settings_persistence(self):
        self.headless_checkbox.stateChanged.connect(
            lambda x: self.settings.setValue('headless', bool(x)))
        self.fallback_checkbox.stateChanged.connect(
            lambda x: self.settings.setValue('fallback', bool(x)))
        self.service_combo.currentIndexChanged.connect(
            lambda i: self.settings.setValue('service', self.service_combo.itemData(i)))
        self.format_title_artist.toggled.connect(
            lambda x: self.settings.setValue('format', 'title_artist' if x else 'artist_title'))
        self.dir_input.textChanged.connect(
            lambda x: self.settings.setValue('output_dir', x))

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        self.main_layout = QVBoxLayout(central_widget)
        self.main_layout.setContentsMargins(10, 10, 10, 10)

        self.input_widget = QWidget()
        input_layout = QVBoxLayout(self.input_widget)
        input_layout.setSpacing(10)

        url_layout = QHBoxLayout()
        url_label = QLabel("Track URL:")
        url_label.setFixedWidth(100)
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Please enter track URL")
        self.url_input.setClearButtonEnabled(True)
        self.fetch_button = QPushButton("Fetch")
        self.fetch_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.fetch_button.setFixedWidth(100)
        self.fetch_button.setEnabled(False)
        self.fetch_button.clicked.connect(self.fetch_track_info)
        url_layout.addWidget(url_label)
        url_layout.addWidget(self.url_input)
        url_layout.addWidget(self.fetch_button)
        input_layout.addLayout(url_layout)

        dir_layout = QHBoxLayout()
        dir_label = QLabel("Output Directory:")
        dir_label.setFixedWidth(100)
        self.dir_input = QLineEdit(self.default_music_dir)
        self.dir_button = QPushButton("Browse")
        self.dir_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.dir_button.setFixedWidth(100)
        dir_layout.addWidget(dir_label)
        dir_layout.addWidget(self.dir_input)
        dir_layout.addWidget(self.dir_button)
        self.dir_button.clicked.connect(self.select_directory)
        input_layout.addLayout(dir_layout)

        settings_group = QGroupBox("Settings")
        settings_layout = QHBoxLayout(settings_group)
        settings_layout.setContentsMargins(10, 0, 10, 10)
        settings_layout.setSpacing(10)
        
        settings_container = QWidget()
        settings_container_layout = QHBoxLayout(settings_container)
        settings_container_layout.setContentsMargins(0, 0, 0, 0)
        settings_container_layout.setSpacing(10)
        
        self.headless_checkbox = QCheckBox("Headless")
        self.headless_checkbox.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.headless_checkbox.setChecked(True)
        settings_container_layout.addWidget(self.headless_checkbox)
        
        self.fallback_checkbox = QCheckBox("Fallback")
        self.fallback_checkbox.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.fallback_checkbox.setChecked(False)
        settings_container_layout.addWidget(self.fallback_checkbox)
        
        service_widget = QWidget()
        service_layout = QHBoxLayout(service_widget)
        service_layout.setContentsMargins(0, 0, 0, 0)
        service_layout.setSpacing(10)
        
        service_label = QLabel("Service:")
        self.service_combo = ServiceComboBox()
        self.service_combo.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        
        service_layout.addWidget(service_label)
        service_layout.addWidget(self.service_combo)
        
        settings_container_layout.addWidget(service_widget)
        
        format_widget = QWidget()
        format_layout = QHBoxLayout(format_widget)
        format_layout.setContentsMargins(0, 0, 0, 0)
        format_layout.setSpacing(10)
        
        format_label = QLabel("Filename:")
        self.format_title_artist = QRadioButton("Title")
        self.format_artist_title = QRadioButton("Artist")
        self.format_title_artist.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.format_artist_title.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.format_title_artist.setChecked(True)
        
        format_layout.addWidget(format_label)
        format_layout.addWidget(self.format_title_artist)
        format_layout.addWidget(self.format_artist_title)
        
        settings_container_layout.addWidget(format_widget)
        
        settings_layout.addStretch()
        settings_layout.addWidget(settings_container)
        settings_layout.addStretch()
        
        input_layout.addWidget(settings_group)
        self.main_layout.addWidget(self.input_widget)

        self.track_widget = QWidget()
        self.track_widget.hide()
        track_layout = QHBoxLayout(self.track_widget)
        track_layout.setContentsMargins(0, 0, 0, 0)
        track_layout.setSpacing(10)

        cover_container = QWidget()
        cover_layout = QVBoxLayout(cover_container)
        cover_layout.setContentsMargins(0, 0, 0, 0)
        cover_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        
        self.cover_label = QLabel()
        self.cover_label.setFixedSize(100, 100)
        self.cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cover_layout.addWidget(self.cover_label)
        track_layout.addWidget(cover_container)

        track_details_container = QWidget()
        track_details_layout = QVBoxLayout(track_details_container)
        track_details_layout.setContentsMargins(0, 0, 0, 0)
        track_details_layout.setSpacing(2)
        track_details_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.title_label = QLabel()
        self.title_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        self.title_label.setWordWrap(True)
        self.title_label.setMinimumWidth(400)
        
        self.artist_label = QLabel()
        self.artist_label.setStyleSheet("font-size: 12px;")
        self.artist_label.setWordWrap(True)
        self.artist_label.setMinimumWidth(400)

        track_details_layout.addWidget(self.title_label)
        track_details_layout.addWidget(self.artist_label)
        track_layout.addWidget(track_details_container, stretch=1)
        track_layout.addStretch()
        self.main_layout.addWidget(self.track_widget)

        self.download_button = QPushButton("Download")
        self.download_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.download_button.setFixedWidth(100)
        self.download_button.clicked.connect(self.button_clicked)
        self.download_button.hide()

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.cancel_button.setFixedWidth(100)
        self.cancel_button.clicked.connect(self.cancel_clicked)
        self.cancel_button.hide()

        self.open_button = QPushButton("Open")
        self.open_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.open_button.setFixedWidth(100)
        self.open_button.clicked.connect(self.open_output_directory)
        self.open_button.hide()

        download_layout = QHBoxLayout()
        download_layout.addStretch()
        download_layout.addWidget(self.open_button)
        download_layout.addWidget(self.download_button)
        download_layout.addWidget(self.cancel_button)
        download_layout.addStretch()
        self.main_layout.addLayout(download_layout)

        self.progress_bar = QProgressBar()
        self.progress_bar.hide()
        self.main_layout.addWidget(self.progress_bar)

        bottom_layout = QHBoxLayout()
        
        self.status_label = QLabel("")
        bottom_layout.addWidget(self.status_label, stretch=1)
        
        self.update_button = QPushButton()
        icon_path = os.path.join(os.path.dirname(__file__), "update.svg")
        if os.path.exists(icon_path):
            self.update_button.setIcon(QIcon(icon_path))
        self.update_button.setFixedSize(16, 16)
        self.update_button.setStyleSheet("""
            QPushButton {
                border: none;
                background: transparent;
            }
            QPushButton:hover {
                background: transparent;
            }
        """)
        self.update_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.update_button.setToolTip("Check for Updates")
        self.update_button.clicked.connect(self.open_update_page)
        
        bottom_layout.addWidget(self.update_button)
        
        self.main_layout.addLayout(bottom_layout)
        
    def open_update_page(self):
        import webbrowser
        webbrowser.open('https://github.com/afkarxyz/SpotifyFLAC/releases')
        
    def validate_url(self, url):
        url = url.strip()
        self.fetch_button.setEnabled(False)
        if not url:
            self.status_label.clear()
            return
        if "open.spotify.com/" not in url:
            self.status_label.setText("Please enter a valid Spotify URL")
            return
        if "/album/" in url:
            self.status_label.setText("Album URLs are not supported. Please enter a track URL.")
            return
        if "/playlist/" in url:
            self.status_label.setText("Playlist URLs are not supported. Please enter a track URL.")
            return
        if "/track/" not in url:
            self.status_label.setText("Please enter a valid Spotify track URL")
            return
        self.fetch_button.setEnabled(True)
        self.status_label.clear()

    def fetch_track_info(self):
        url = self.url_input.text().strip()
        if not url:
            self.status_label.setText("Please enter a Track URL")
            return
        self.fetch_button.setEnabled(False)
        self.status_label.setText("Fetching track information...")
        headless = self.headless_checkbox.isChecked()
        fallback = self.fallback_checkbox.isChecked()
        service = self.service_combo.currentData()
        self.fetcher = MetadataFetcher(url, headless=headless, service=service, use_fallback=fallback)
        self.fetcher.finished.connect(self.handle_track_info)
        self.fetcher.error.connect(self.handle_fetch_error)
        self.fetcher.start()

    def handle_track_info(self, metadata):
        self.metadata = metadata
        self.fetch_button.setEnabled(True)
        self.title_label.setText(metadata['title'].strip())
        self.artist_label.setText(metadata['artists'].strip())
        self.image_downloader = ImageDownloader(metadata['cover'])
        self.image_downloader.finished.connect(self.update_cover_art)
        self.image_downloader.start()
        self.input_widget.hide()
        self.track_widget.show()
        self.download_button.show()
        self.cancel_button.show()
        self.update_button.hide()
        self.status_label.clear()
        self.adjustWindowHeight()

    def adjustWindowHeight(self):
        title_height = self.title_label.sizeHint().height()
        artist_height = self.artist_label.sizeHint().height()
        base_height = 180
        additional_height = max(0, (title_height + artist_height) - 40)
        new_height = min(300, base_height + additional_height)
        self.setFixedHeight(int(new_height))

    def update_cover_art(self, image_data):
        pixmap = QPixmap()
        pixmap.loadFromData(image_data)
        scaled_pixmap = pixmap.scaled(100, 100, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.cover_label.setPixmap(scaled_pixmap)

    def handle_fetch_error(self, error):
        self.fetch_button.setEnabled(True)
        self.status_label.setText(f"Error fetching track info: {error}")

    def select_directory(self):
        directory = QFileDialog.getExistingDirectory(self, "Select Output Directory")
        if directory:
            self.dir_input.setText(directory)

    def open_output_directory(self):
        output_dir = self.dir_input.text().strip() or self.default_music_dir
        os.startfile(output_dir)

    def cancel_clicked(self):
        self.track_widget.hide()
        self.input_widget.show()
        self.download_button.hide()
        self.cancel_button.hide()
        self.progress_bar.hide()
        self.progress_bar.setValue(0)
        self.status_label.clear()
        self.metadata = None
        self.fetch_button.setEnabled(True)
        self.update_button.show()
        self.setFixedHeight(180)

    def button_clicked(self):
        if self.download_button.text() == "Clear":
            self.clear_form()
        else:
            self.start_download()

    def clear_form(self):
        self.url_input.clear()
        self.progress_bar.hide()
        self.progress_bar.setValue(0)
        self.status_label.clear()
        self.download_button.setText("Download")
        self.download_button.hide()
        self.cancel_button.hide()
        self.open_button.hide()
        self.track_widget.hide()
        self.input_widget.show()
        self.metadata = None
        self.update_button.show()
        self.setFixedHeight(180)

    def start_download(self):
        output_dir = self.dir_input.text().strip()
        if not self.metadata:
            self.status_label.setText("Please fetch track information first")
            return
        if not output_dir:
            output_dir = self.default_music_dir
            self.dir_input.setText(output_dir)
        
        self.download_button.hide()
        self.cancel_button.hide()
        self.progress_bar.show()
        self.progress_bar.setValue(0)
        self.status_label.setText("Preparing...")
        
        format_type = 'artist_title' if self.format_artist_title.isChecked() else 'title_artist'
        fallback = self.fallback_checkbox.isChecked()
        
        self.worker = DownloaderWorker(
            metadata=self.metadata, 
            output_dir=output_dir,
            filename_format=format_type,
            use_fallback=fallback
        )
        
        self.worker.progress.connect(self.update_progress)
        self.worker.status.connect(self.update_status)
        self.worker.finished.connect(self.download_finished)
        self.worker.error.connect(self.download_error)
        self.worker.start()

    def update_status(self, status):
        self.status_label.setText(status)

    def update_progress(self, value):
        self.progress_bar.setValue(value)

    def download_finished(self, message):
        self.progress_bar.hide()
        self.status_label.setText(message)
        self.open_button.show()
        self.download_button.setText("Clear") 
        self.download_button.show()
        self.cancel_button.hide()
        self.download_button.setEnabled(True)

    def download_error(self, error_message):
        self.progress_bar.hide()
        self.status_label.setText(error_message)
        self.download_button.setText("Retry")
        self.download_button.show()
        self.cancel_button.show()
        self.download_button.setEnabled(True)
        self.cancel_button.setEnabled(True)

def main():
    app = QApplication(sys.argv)
    window = SpotifyFlacGUI()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
