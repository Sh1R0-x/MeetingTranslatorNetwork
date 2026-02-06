import sys
from pathlib import Path

from PyQt6.QtGui import QFontDatabase
from PyQt6.QtWidgets import QApplication

from ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    fonts_dir = Path(__file__).resolve().parents[1] / "assets" / "fonts"
    if fonts_dir.exists():
        # Load all bundled fonts (Inter, IBM Plex Sans, JetBrains Mono, etc.).
        for font_path in fonts_dir.glob("*.ttf"):
            QFontDatabase.addApplicationFont(str(font_path))
    qss = Path(__file__).resolve().parent / "ui" / "style.qss"
    if qss.exists():
        app.setStyleSheet(qss.read_text(encoding="utf-8"))
    w = MainWindow()
    w.resize(1100, 700)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
