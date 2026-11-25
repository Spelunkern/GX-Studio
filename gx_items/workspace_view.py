from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFileDialog, QGridLayout, QLabel, QLineEdit
from PySide6.QtWidgets import QMessageBox, QPushButton

from .gx_connection import probe, server_data_folder


class WorkspaceDialog(QDialog):
    def __init__(self, studio, focus="client"):
        super().__init__(studio)
        self.studio = studio
        self.setWindowTitle("Workspace Settings")
        self.resize(675, 205)
        layout = QGridLayout(self)
        layout.setColumnStretch(1, 1)
        self.fields = {}
        for row, (key, label, path, automatic) in enumerate((
            ("client", "Client Data", studio.client_data, False),
            ("server", "Server Data", studio.server_folder, True),
            ("sqlite", "SQLite", studio.manual_database, True),
        )):
            field = QLineEdit(str(path) if path else "")
            field.setPlaceholderText("Automatic" if automatic else "No folder selected")
            self.fields[key] = field
            heading = QLabel(label)
            heading.setBuddy(field)
            browse = QPushButton("Browse…")
            browse.setAutoDefault(False)
            browse.clicked.connect(lambda _, key=key: self.browse(key))
            clear = QPushButton("Automatic" if automatic else "Clear")
            clear.setAutoDefault(False)
            clear.clicked.connect(field.clear)
            layout.addWidget(heading, row, 0)
            layout.addWidget(field, row, 1)
            layout.addWidget(browse, row, 2)
            layout.addWidget(clear, row, 3)
        note = QLabel("Server Data contains GX World files such as game/NpcQuest.SData. "
                      "SQLite connects to the running GX server or the selected server’s runtime database.")
        note.setWordWrap(True)
        layout.addWidget(note, 3, 1, 1, 3)
        backups = QLabel(str(studio.backup_folder()))
        backups.setTextFormat(Qt.TextFormat.PlainText)
        backups.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(QLabel("Backups"), 4, 0)
        layout.addWidget(backups, 4, 1, 1, 3)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons, 5, 0, 1, 4)
        self.fields[focus].setFocus()

    def browse(self, key):
        field = self.fields[key]
        initial = field.text().strip() or str(self.studio.studio_dir)
        if key == "sqlite":
            path, _ = QFileDialog.getOpenFileName(
                self, "Select GX SQLite", initial, "SQLite (*.sqlite *.sqlite3 *.db);;All Files (*)",
            )
        else:
            title = "Select Client Data Folder" if key == "client" else "Select GX Server or Server Data Folder"
            path = QFileDialog.getExistingDirectory(self, title, initial)
        if path:
            field.setText(path)

    def accept(self):
        studio = self.studio
        paths = {key: studio.studio_dir / Path(value).expanduser() if (value := field.text().strip()) else None
                 for key, field in self.fields.items()}
        try:
            client, server, database = (paths[key] for key in ("client", "server", "sqlite"))
            if client is not None and client != studio.client_data:
                client = studio.resolve_client_data(client)
            if server is not None and server != studio.server_folder:
                server = server.resolve(strict=True)
                if server_data_folder(server) is None:
                    raise ValueError("Select a GX Server install folder or its data folder containing game/.")
            if database is not None and database != studio.manual_database:
                status = probe(database, "Selected manually")
                if not status.connected:
                    raise ValueError(f"Could not open GX SQLite.\n{database}\n\n{status.detail}")
                database = status.path
        except (OSError, RuntimeError, ValueError) as error:
            QMessageBox.warning(self, "Workspace Settings", str(error))
            return
        if studio.apply_workspace(client, server, database):
            super().accept()
