"""Graphical entry point for Transass Desktop."""

from __future__ import annotations

import os
import sys
import webbrowser
from pathlib import Path

from .dialogs import choose_directory
from .instance import InstanceAlreadyRunning, SingleInstanceLock
from .paths import default_paths
from .runtime import LocalRuntime


def _load_local_environment() -> None:
    """Load the repository/bundle .env before resolving Desktop paths."""
    core_root = Path(os.environ["TRANSASS_CORE_ROOT"]) if os.environ.get("TRANSASS_CORE_ROOT") else Path(__file__).resolve().parents[3]
    source_root = core_root / "src" / "subtranslate"
    if not source_root.is_dir():
        return
    source_text = str(source_root)
    if source_text not in sys.path:
        sys.path.insert(0, source_text)
    from runtime_config import load_project_env  # type: ignore[import-not-found]

    load_project_env(core_root)


def main() -> int:
    try:
        from PySide6.QtCore import QSettings, QUrl, Qt
        from PySide6.QtGui import QDesktopServices, QIcon, QKeySequence
        from PySide6.QtWidgets import (
            QApplication,
            QDialog,
            QFrame,
            QHBoxLayout,
            QLabel,
            QMainWindow,
            QMessageBox,
            QPushButton,
            QVBoxLayout,
        )
        from PySide6.QtWebEngineWidgets import QWebEngineView
    except ImportError as error:
        print("Dependência Desktop ausente: instale PySide6 para executar o Transass.", file=sys.stderr)
        print(f"Detalhe: {error}", file=sys.stderr)
        return 2

    _load_local_environment()
    paths = default_paths()
    instance_lock = SingleInstanceLock(paths.instance_lock)
    try:
        instance_lock.acquire()
    except InstanceAlreadyRunning as error:
        print(str(error), file=sys.stderr)
        return 3

    application = QApplication(sys.argv)
    application.setApplicationName("Transass")
    application.setApplicationDisplayName("Transass")
    application.setOrganizationName("Transass")
    try:
        from _version import __version__  # type: ignore[import-not-found]
    except (ImportError, AttributeError):
        __version__ = "2.5.0"
    application.setApplicationVersion(str(__version__))
    icon_candidates = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        icon_candidates.append(Path(meipass) / "transass_logo.png")
    icon_candidates.append(Path(__file__).resolve().parents[3] / "src/subtranslate/transass_logo.png")
    app_icon = next((candidate for candidate in icon_candidates if candidate.is_file()), None)
    if app_icon is not None:
        application.setWindowIcon(QIcon(str(app_icon)))
    # Keep the native menus visually aligned with the dark Transass web UI.
    # The stylesheet is limited to menu widgets, so the embedded web page
    # keeps its own CSS and remains independent of the Qt palette.
    application.setStyleSheet(
        """
        QMenuBar {
            background: #0e1722;
            color: #e6edf3;
            border-bottom: 1px solid #26394d;
            padding: 4px;
        }
        QMenuBar::item {
            background: transparent;
            padding: 6px 10px;
            border-radius: 6px;
        }
        QMenuBar::item:selected {
            background: #20344c;
            color: #f4f8fc;
        }
        QMenu {
            background: #111b29;
            color: #e6edf3;
            border: 1px solid #2c4661;
            padding: 5px;
        }
        QMenu::item {
            background: transparent;
            padding: 7px 28px 7px 10px;
            border-radius: 5px;
        }
        QMenu::item:selected {
            background: #20344c;
            color: #f4f8fc;
        }
        QMenu::separator {
            height: 1px;
            background: #26394d;
            margin: 4px 8px;
        }
        QMessageBox {
            background: #111b29;
            color: #e6edf3;
            border: 1px solid #2c4661;
        }
        QMessageBox QLabel {
            color: #e6edf3;
            background: transparent;
        }
        QMessageBox QPushButton {
            min-width: 92px;
            padding: 7px 14px;
            border: 1px solid #38526e;
            border-radius: 8px;
            background: #202e3e;
            color: #edf4fb;
        }
        QMessageBox QPushButton:hover {
            background: #293b4f;
            border-color: #5b7898;
        }
        """
    )
    runtime = LocalRuntime()
    try:
        url = runtime.start()
    except Exception as error:
        print(f"Não foi possível iniciar o Transass: {error}", file=sys.stderr)
        instance_lock.release()
        return 1

    window = QMainWindow()
    window.setWindowTitle("Transass")
    if app_icon is not None:
        window.setWindowIcon(QIcon(str(app_icon)))
    window.resize(1280, 820)
    view = QWebEngineView(window)
    view.setUrl(QUrl(url))
    window.setCentralWidget(view)

    menu_bar = window.menuBar()
    file_menu = menu_bar.addMenu("Arquivo")
    choose_action = file_menu.addAction("Escolher pasta de mídia…")

    def choose_media_folder() -> None:
        selected = choose_directory(window, initial=paths.media_root)
        if selected is None:
            return
        paths.config_root.mkdir(parents=True, exist_ok=True)
        paths.selected_media_folder_file.write_text(
            str(selected) + "\n", encoding="utf-8"
        )
        QMessageBox.information(window, "Pasta selecionada", "A pasta será usada na próxima abertura do Transass.")

    choose_action.triggered.connect(choose_media_folder)

    # The menu bar is useful during setup but can be hidden for a cleaner,
    # app-like window.  Keep the toggle action active while hidden so the user
    # can restore it with the same shortcut.
    view_menu = menu_bar.addMenu("Exibir")
    menu_bar_action = view_menu.addAction("Mostrar barra de menus")
    menu_bar_action.setCheckable(True)
    menu_bar_action.setShortcut(QKeySequence("Ctrl+Shift+M"))
    menu_bar_action.setShortcutContext(Qt.ApplicationShortcut)
    settings = QSettings("Transass", "Transass")
    menu_bar_visible = settings.value("interface/menu_bar_visible", True, type=bool)
    menu_bar_action.setChecked(menu_bar_visible)
    menu_bar.setVisible(menu_bar_visible)

    def set_menu_bar_visible(visible: bool) -> None:
        visible = bool(visible)
        menu_bar_action.setChecked(visible)
        menu_bar.setVisible(visible)
        settings.setValue("interface/menu_bar_visible", visible)
        settings.sync()

    menu_bar_action.toggled.connect(set_menu_bar_visible)
    window.addAction(menu_bar_action)

    help_menu = menu_bar.addMenu("Ajuda")
    github_action = help_menu.addAction("Abrir GitHub")
    docs_action = help_menu.addAction("Abrir documentação")
    help_menu.addSeparator()
    shortcuts_action = help_menu.addAction("Atalhos de teclado")
    about_action = help_menu.addAction("Sobre o Transass")
    api_keys_action = help_menu.addAction("Tutorial: chaves de API")
    github_url = QUrl("https://github.com/Eltonfk/TRANSASS")

    def open_url(url: QUrl) -> None:
        # Some Linux desktop sessions report success from Qt without actually
        # dispatching the URL. Try the Python/system browser first, then Qt,
        # and give the user a recoverable fallback if neither is available.
        address = str(url.toString())
        try:
            opened = bool(webbrowser.open(address, new=2))
        except Exception:
            opened = False
        if not opened:
            try:
                opened = bool(QDesktopServices.openUrl(url))
            except Exception:
                opened = False
        if not opened:
            QApplication.clipboard().setText(address)
            QMessageBox.warning(
                window,
                "Não foi possível abrir o link",
                f"O endereço foi copiado para a área de transferência:\n{address}",
            )

    github_action.triggered.connect(lambda checked=False: open_url(github_url))
    docs_action.triggered.connect(
        lambda checked=False: open_url(QUrl("https://github.com/Eltonfk/TRANSASS#readme"))
    )
    shortcuts_action.triggered.connect(
        lambda: QMessageBox.information(
            window,
            "Atalhos de teclado",
            "Ctrl+Shift+M — mostrar ou ocultar a barra de menus",
        )
    )
    about_action.triggered.connect(
        lambda: QMessageBox.about(
            window,
            "Sobre o Transass",
            f"Transass {__version__}\n\n"
            "Tradutor de legendas para português brasileiro.\n"
            "O aplicativo desktop executa o núcleo localmente e mantém seus dados no computador.",
        )
    )

    def show_api_key_tutorial() -> None:
        """Show a short, safe guide with official key-creation links."""
        dialog = QDialog(window)
        dialog.setWindowTitle("Como obter chaves de API")
        dialog.setMinimumSize(620, 620)
        dialog.setStyleSheet(
            """
            QDialog { background: #111b29; color: #e6edf3; }
            QLabel { color: #dbe7f3; }
            QLabel#tutorialTitle { color: #f4f8fc; font-size: 18px; font-weight: 700; }
            QLabel#tutorialIntro { color: #9eafc1; }
            QFrame#tutorialCard {
                background: #172536;
                border: 1px solid #2c4661;
                border-radius: 10px;
            }
            QLabel#tutorialCardTitle { color: #84b8ff; font-size: 14px; font-weight: 700; }
            QLabel#tutorialSteps { color: #c7d5e3; }
            QPushButton {
                min-height: 34px;
                padding: 6px 12px;
                border: 1px solid #38526e;
                border-radius: 8px;
                background: #202e3e;
                color: #edf4fb;
            }
            QPushButton:hover { background: #293b4f; border-color: #5b7898; }
            QPushButton#closeTutorial { background: #247fdd; border-color: #559bff; }
            """
        )
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        title = QLabel("Chaves API para motores online")
        title.setObjectName("tutorialTitle")
        layout.addWidget(title)
        intro = QLabel(
            "Crie a chave no site oficial do provedor e cole-a em "
            "⚙ Motor. O Transass mantém a credencial localmente e não a "
            "exibe na interface. Nunca publique a chave em repositórios, "
            "prints ou mensagens."
        )
        intro.setObjectName("tutorialIntro")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        def add_provider_card(
            heading: str,
            steps: str,
            links: tuple[tuple[str, str], ...],
        ) -> None:
            card = QFrame(dialog)
            card.setObjectName("tutorialCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(14, 12, 14, 12)
            card_layout.setSpacing(8)
            card_title = QLabel(heading)
            card_title.setObjectName("tutorialCardTitle")
            card_layout.addWidget(card_title)
            card_steps = QLabel(steps)
            card_steps.setObjectName("tutorialSteps")
            card_steps.setWordWrap(True)
            card_layout.addWidget(card_steps)
            buttons = QHBoxLayout()
            buttons.setSpacing(8)
            for label, address in links:
                button = QPushButton(label)
                button.clicked.connect(lambda _checked=False, url=address: open_url(QUrl(url)))
                buttons.addWidget(button)
            buttons.addStretch(1)
            card_layout.addLayout(buttons)
            layout.addWidget(card)

        add_provider_card(
            "Google Gemini",
            "1. Abra o Google AI Studio e entre na sua conta.\n"
            "2. Acesse API Keys e escolha Criar chave.\n"
            "3. Copie a chave, depois selecione Gemini no Transass e cole-a.\n"
            "4. Restrinja a chave à Gemini API quando essa opção estiver disponível.",
            (
                ("Abrir AI Studio / chave", "https://aistudio.google.com/app/apikey"),
                ("Guia oficial", "https://ai.google.dev/gemini-api/docs/api-key"),
            ),
        )
        add_provider_card(
            "NVIDIA NIM",
            "1. Abra o catálogo NVIDIA NIM e entre na sua conta NVIDIA.\n"
            "2. Clique em Get API Key e copie a chave gerada.\n"
            "3. No Transass, selecione NVIDIA, informe a chave e use o ID do modelo exibido no catálogo.\n"
            "4. O modelo precisa ter namespace, como meta/... ou qwen/....",
            (
                ("Abrir catálogo / chave", "https://build.nvidia.com/explore/discover?api-key=true"),
                ("Documentação NIM", "https://docs.nvidia.com/nim/large-language-models/latest/get-started/"),
            ),
        )
        add_provider_card(
            "Ollama local",
            "1. Instale o Ollama para Linux ou Windows e deixe o aplicativo em execução.\n"
            "2. No terminal, baixe um modelo, por exemplo:  ollama pull qwen3.5:9b\n"
            "3. Confirme com  ollama list  ou teste com  ollama run qwen3.5:9b.\n"
            "4. No Transass, escolha Ollama, informe exatamente qwen3.5:9b, clique em Testar motor e salve.\n"
            "O Windows normalmente inicia o Ollama sozinho; no Linux, use  ollama serve  se ele ainda não estiver rodando.",
            (
                ("Baixar Ollama", "https://ollama.com/download"),
                ("Guia rápido", "https://docs.ollama.com/quickstart"),
                ("Biblioteca Qwen 3.5", "https://ollama.com/library/qwen3.5"),
            ),
        )

        close_button = QPushButton("Fechar")
        close_button.setObjectName("closeTutorial")
        close_button.clicked.connect(dialog.accept)
        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_row.addWidget(close_button)
        layout.addLayout(close_row)
        dialog.exec()

    api_keys_action.triggered.connect(show_api_key_tutorial)

    original_close_event = window.closeEvent

    def close_event(event):
        runtime.stop()
        original_close_event(event)

    window.closeEvent = close_event  # type: ignore[method-assign]
    window.show()
    result = application.exec()
    runtime.stop()
    instance_lock.release()
    return int(result)


if __name__ == "__main__":
    raise SystemExit(main())
