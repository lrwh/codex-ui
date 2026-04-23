from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from desktop_app_window import *

def main() -> None:
    config = load_config()
    setup_qt_input_method_env(config.input_method_strategy)
    app = QApplication(sys.argv)
    icon = load_app_icon()
    if icon is not None and not icon.isNull():
        app.setWindowIcon(icon)
    window = MainWindow(config)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
