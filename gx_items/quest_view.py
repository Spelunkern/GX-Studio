from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTreeWidget, QTreeWidgetItem

from .quest_catalog import QuestReferences


def quest_panel(references: QuestReferences | None) -> QTreeWidget | None:
    if references is None or not (references.quests or references.mobs):
        return None
    tree = QTreeWidget()
    tree.setAccessibleName("Quest references")
    tree.setHeaderLabels(("ID", "Name", "Relation"))
    tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    tree.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    tree.setRootIsDecorated(False)
    tree.setItemsExpandable(False)
    tree.setIndentation(0)
    tree.setUniformRowHeights(True)
    tree.setAlternatingRowColors(True)
    header = tree.header()
    header.setStretchLastSection(False)
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
    header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
    tree.setColumnHidden(2, not references.mobs)
    visible_rows = 0
    for title, rows in (
        ("Quests", tuple((qid, name, "") for qid, name in references.quests)),
        ("Mobs", references.mobs),
    ):
        if not rows:
            continue
        group = QTreeWidgetItem(tree, (title,))
        group.setFirstColumnSpanned(True)
        bold = tree.font()
        bold.setBold(True)
        group.setFont(0, bold)
        group.setFlags(Qt.ItemFlag.ItemIsEnabled)
        for identifier, name, relation in rows:
            entry = QTreeWidgetItem(group, (str(identifier), name or "Unknown name", relation))
            entry.setToolTip(1, entry.text(1))
            entry.setFlags(Qt.ItemFlag.ItemIsEnabled)
        visible_rows += 1 + len(rows)
    tree.expandAll()
    row_height = tree.sizeHintForRow(0)
    tree.setFixedHeight(header.sizeHint().height() + min(visible_rows, 7) * row_height + 2 * tree.frameWidth())
    return tree
