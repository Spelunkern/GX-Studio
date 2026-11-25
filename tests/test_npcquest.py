import unittest
from dataclasses import replace
from pathlib import Path
from struct import pack_into
from tempfile import TemporaryDirectory
from unittest.mock import patch

from gx_items import seed
from gx_items.catalog import WORD
from gx_items.install import replace_file
from gx_items.npcquest_data import EMPTY_FAMILY, NPC, NPC_FIELDS, QUEST_FIELDS, Gate, NPCQuestCatalog, Quest, empty_fields
from gx_items.npcquest_save import save_quest_catalog


class NPCQuestTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.client_path, self.server_path, self.backups = root / "client.SData", root / "server.SData", root / "backups"
        empty = NPCQuestCatalog(bytes(13 * WORD.size) + EMPTY_FAMILY * 256 + bytes(WORD.size))
        client = empty.data.draft()
        self.merchant = (1, 1)
        self.gatekeeper = (2, 1)
        client.npcs[self.merchant] = NPC(empty_fields(NPC_FIELDS) | {"MerchantType": 2}, "Merchant", "Welcome",
                                        ((24, 1),), links=((1,), ()))
        client.npcs[self.gatekeeper] = NPC(empty_fields(NPC_FIELDS), "Gatekeeper", "Welcome",
                                          gates=(Gate(label="Village"), Gate(), Gate()))
        for row in (1, 2):
            client.quests[row] = Quest(empty_fields(QUEST_FIELDS) | {"EmbeddedID": row}, f"Quest {row}",
                                       ("Client summary",) + ("",) * 7)
        client.item_links[(24, 1)] = ((1,), (1,))
        server = client.draft()
        server.npcs[self.gatekeeper] = replace(
            client.npcs[self.gatekeeper], welcome="Bienvenue",
            gates=(Gate(Gate().fields | {"MapID": 3, "Money": 20}, "Village du serveur"), Gate(), Gate())
        )
        server.quests[1] = replace(server.quests[1], texts=("Dialogue du serveur",) + ("",) * 7)
        server.item_links[(24, 1)] = ((1,), (2,))
        client_raw = seed.encode_like(seed.SIGNATURE.ljust(seed.HEADER_SIZE, b"\0"), empty.encode(client))
        self.client_path.write_bytes(client_raw)
        self.server_path.write_bytes(empty.encode(server))
        self.client = NPCQuestCatalog.open(self.client_path)
        self.server = NPCQuestCatalog.open(self.server_path)

    def save(self, draft, *, full=False):
        return save_quest_catalog(self.client_path, self.client, draft, self.backups,
                                  self.server_path, self.server, full=full)

    def test_save_merges_changed_fields_and_keeps_dialogue_and_encryption(self):
        draft = self.client.data.draft()
        npc = draft.npcs[self.gatekeeper]
        gate = replace(npc.gates[0], fields=npc.gates[0].fields | {"MapID": 5}, label="Aldea")
        draft.npcs[self.gatekeeper] = replace(npc, gates=(gate, *npc.gates[1:]))
        quest = draft.quests[1]
        draft.quests[1] = replace(quest, fields=quest.fields | {"Reward1XP": 500},
                                 texts=("¡Misión aceptada!", *quest.texts[1:]))
        draft.item_links[(24, 1)] = ((2,), (1,))

        backup, client, server = self.save(draft)

        self.assertEqual(seed.file_envelope(self.client_path), "Encrypted")
        self.assertEqual(seed.file_envelope(self.server_path), "Unencrypted")
        self.assertEqual((backup / "client-before.SData").read_bytes(), self.client.raw)
        self.assertEqual((backup / "server-before.SData").read_bytes(), self.server.raw)
        self.assertEqual(NPCQuestCatalog.open(self.client_path).data, draft)
        self.assertEqual(NPCQuestCatalog.open(self.server_path).data, server.data)
        self.assertEqual(client.data.quests[1].texts[0], "¡Misión aceptada!")
        self.assertEqual(server.data.quests[1].texts[0], "Dialogue du serveur")
        self.assertEqual(server.data.quests[1].fields["Reward1XP"], 500)
        gate = server.data.npcs[self.gatekeeper].gates[0]
        self.assertEqual((gate.fields["MapID"], gate.fields["Money"], gate.label), (5, 20, "Village du serveur"))
        self.assertEqual(server.data.item_links[(24, 1)], ((2,), (2,)))

    def test_install_synchronizes_all_rules_and_preserves_server_text_bytes(self):
        draft = self.client.data.draft()
        draft.npcs[self.merchant] = replace(draft.npcs[self.merchant], products=((31, 1), (24, 1)))
        draft.quests[3] = Quest(empty_fields(QUEST_FIELDS) | {"EmbeddedID": 3}, "Quest 3",
                                ("Draft dialogue",) + ("",) * 7)
        _, client, server = self.save(draft, full=True)
        self.assertEqual(server.data.shared(), client.data.shared())
        self.assertEqual(server.data.quests[3].texts, ("",) * 8)
        self.assertEqual(server.strings[("quest", 1, "Summary")], self.server.strings[("quest", 1, "Summary")])

    def test_failed_server_write_restores_client_and_retains_backups(self):
        draft = self.client.data.draft()
        draft.quests[1] = replace(draft.quests[1], name="Edited quest")

        def fail_server(path, raw):
            if path == self.server_path:
                raise OSError("file locked")
            replace_file(path, raw)

        with patch("gx_items.npcquest_save.replace_file", side_effect=fail_server):
            with self.assertRaisesRegex(OSError, "file locked"):
                self.save(draft)
        self.assertEqual(self.client_path.read_bytes(), self.client.raw)
        self.assertEqual(self.server_path.read_bytes(), self.server.raw)
        self.assertEqual(len(tuple(self.backups.glob("*/quest-rolled-back.json"))), 1)

    def test_encryption_status_distinguishes_legacy_and_unknown_formats(self):
        legacy = bytearray(self.client.raw)
        pack_into("<III", legacy, 40, 0, 0, len(self.client.plain))
        self.assertEqual(seed.envelope_label(legacy, len(legacy)), "Encrypted")
        self.assertFalse(NPCQuestCatalog(bytes(legacy)).writable)
        self.assertEqual(seed.envelope_label(seed.SIGNATURE, len(seed.SIGNATURE)), "Invalid header")
        self.assertEqual(seed.envelope_label(b"unknown", known=False), "Unknown")
