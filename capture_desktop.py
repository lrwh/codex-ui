import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from desktop_app import MainWindow, load_config, setup_qt_input_method_env


def main() -> int:
    output = sys.argv[1] if len(sys.argv) > 1 else "desktop-preview.png"
    config = load_config()
    setup_qt_input_method_env(config.input_method_strategy)
    app = QApplication(sys.argv)
    window = MainWindow(config)
    window.show()

    def capture() -> None:
        pixmap = window.grab()
        pixmap.save(output)
        app.quit()

    QTimer.singleShot(600, capture)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
