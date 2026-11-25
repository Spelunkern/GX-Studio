# Editing with GX Studio

Set the three sources in **File → Workspace Settings…**. The workspace tree
shows each source and its status. Hover a row to see the full path. A
read-only SQLite connection can be browsed. Use a writable copy of the
runtime database for changes. The editor accepts Unicode paths and writes
edited text in UTF-8. It preserves the source bytes of untouched legacy
text and records wherever the format supports it.

## Files, SQLite and server rules

| Action | Destination |
| --- | --- |
| **Save** in Item, Cash, Skill or NpcSkill | Applies the staged changes to the client SData and corresponding gameplay fields in the active workspace SQLite database. |
| **Install…** in those catalogs | Reconciles the entire client catalog with its SQLite table. It can insert, update or remove definitions. The confirmation shows both paths and the affected table. |
| **Export Copy…** | Writes a copy for offline work or review. **Link SQLite for Export…** in Items lets that copy include a SQLite snapshot. Install still uses the active workspace connection. |
| **Save** in NpcQuest | Writes the client file and updates changed gameplay rules in the linked GX World file. Without a linked server file, a confirmation offers a client-only save. |
| **Install Rules to Server…** in NpcQuest | Reconciles the entire gameplay rule set. The server retains its own dialogue and file encryption format. |
| **Save Client Monster.SData…** and **Save SQLite Mobs…** | Separate commits for the client and the active server database. |
| **Save** in model, cloak and emblem tables | Writes the client table and keeps a dated copy of the original. |

SQLite definitions use `PS_GameDefs_Items`, `PS_GameDefs_ProductList`,
`PS_GameDefs_Skills` (SkillID and SkillLevel), and `PS_GameDefs_NpcSkills`
(SkillID and Rank). Monster definitions use `PS_GameDefs_Mobs` and
`PS_GameDefs_MobItems`. Running GX processes may cache these definitions.
Reload the client or restart GX World as indicated in the save confirmation.

For direct item edits, open **SQLite → PS_GameDefs_Items**. Save confirms the
database path and changed columns. The editor checks that the affected values
still match the ones you opened. If they changed outside Studio, refresh and
review the new values before saving.

### Backups and recovery

Studio stores dated backups in `backups/` beside the portable launcher.
SQLite backups include committed WAL contents. Saves that coordinate more
than one file keep their recovery data in the dated transaction folder.
A pending manifest is used to verify or roll back interrupted operations.
Keep the dated folder intact and reopen the same source when retrying. If
Studio reports that the targets have changed since an interrupted operation,
compare the current files with the backup before restoring anything. Shut
down GX World and GX Login before replacing a runtime database from backup,
including any associated WAL files.

## Items and the Item Mall

Open Item.SData, choose a family and double-click a row. **Ctrl+F** searches
by name or ID. **Grades** groups definitions by their loot grade. The icon
picker uses client icon atlases. **Enchant ID → Browse…** searches valid
consumable materials by name or ID. Quest items show related quests, drop
mobs and objectives when the corresponding client or database data is
available.

An equipment form previews the model selected by its native ITM, MLT or
cloak entry. Drag to rotate. Armor previews use eligible body variants.
For cloaks, **Chest fit** shows the native fit presets. Lapis displays the
base link percentage and gold cost for its cost tier. The Debuff selector
uses the client NPC skill icon and description. Lapisia shows the gold
cost, with enchantment chance determined in GX World.

Cash.SData opens in **Small Icons** and remembers the view. Select a product
to edit its text, code, price, icon and up to 24 item ID/quantity pairs.
Choose a bundle slot and use **Browse Items…** to find an item. Hidden
category-zero entries are preserved. GX uses one active price.

## Player and NPC skills

**Toon Skills** and **Noss Skills** group player skills. Changing **Spell
effect** updates the fields available for that effect, including supported
trap damage, reflection channels and transformation targets. The rank cells
open rank-specific values. **Edit Text…** opens names and descriptions for
every rank. Chain skills can specify the previous skill.

NpcSkill uses the combined GX NPC skill schema. Select a rank when several
ranks share the same SkillID. The form exposes the target, duration, range,
effect, damage or healing, and associated icons and stats.

## NPCs and quests

NpcQuest opens in **Merchants**. The tabs also cover gatekeepers, standard
NPCs and quests. Merchant stock shows item icons and supports search.
**Edit → Item Links…** edits the client’s item-to-quest links. Quest Details
groups requirements, objectives, dialogue and rewards. Its contextual
**Additional Fields…** dialog includes GX flags and timers. Use **File →
Export Sentences of a Quest…** for a UTF-8 dialogue export.

For a workspace catalog, **Server Data Settings…** links the GX World copy
at `game/NpcQuest.SData`. For a standalone client file, **Select Server File…**
can link its matching server file. Look at the two source summaries for paths,
encryption and rule differences before saving.

## Monsters

Double-click a monster to open the compact record view. Blue controls show
SQLite-backed fields. Focusing a field shows the source, native name and
editable range. **Source Values…** exposes the remaining client and server
fields. The model button opens the preview. **Drops >>** shows the nine
drop slots and the client items in the selected grade. Slots 1–6 use integer
percentages, and slots 7–9 accept 0.0001% increments. Native limits include
4,294,967,295 HP and 255 UTF-8 bytes for a client monster name.

## Models, effects and other assets

- **MON** stores monster, NPC and vehicle meshes, textures, animation
  references, sounds, height and effect bindings. **Bone Effects…** edits
  emitter-to-bone assignments. Preview configured action effects from the
  control under the model.
- **ITM/IT2** stores equipment meshes, textures, effects and native model
  flags. The preview can show the enchantment effect resolved from a
  compatible SEFF lookup.
- **MLT** edits character model tables and preserves the table's native
  MLT/ML2 signature. **ALT** exposes animation references.
- **Cloak** includes PC geometry or placement data, CTL texture tables and
  the EmblemFront, EmblemBack and EmblemList catalogs. Choose a texture to
  preview its DDS. Appending keeps existing one-based emblem IDs intact.
- **Open Asset…** opens individual supported files for inspection. DDS,
  PNG and other supported images, bitmap fonts and Ogg playback are
  available. EFT and SEFF default to the full configured effect where
  sequence information is present. The component selector isolates
  emitters. Previews bound drawing work for responsiveness.

This beta omits `.wld` and `.svmap` editing and viewing, portions of GX
World’s server-only catalog formats, and the locked Pets, Wings and Costumes
item sections. A source with an unrecognized layout may be unavailable for
editing. Keep the original files to compare results during beta testing.
