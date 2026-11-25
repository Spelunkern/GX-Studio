# GX Studio v0.1.0-beta.4

Portable builds are available for Windows x64, Linux x64, macOS Apple silicon,
and macOS Intel. Extract the whole archive to a writable folder and launch
GX Studio. The editor stores its settings and dated backups in that folder.

- **Legacy Data** opens client folders and SAH/SAF archives without connecting
  to SQLite or GX World. It remembers the last client source and can extract
  files to a folder you choose.
- Legacy mode detects supported EP4.5, EP5.4, and EP6.4 layouts and can convert
  supported EP4.5 and EP6.4 catalogs to GX EP5.4. It also opens EP7 clients with
  compatible layouts and supports read-only EP8 BinarySData tables and quest
  previews.
- The server **SVMap editor** shows the client minimap, named-area labels and
  source files, NPC patrols, portals, ladders, monster areas, and spawn boxes.
  Draw monster areas, search monsters by name, preview their models, and move
  selected spawn boxes after confirmation. Safe areas can be drawn and saved
  separately to the server's `SafeZones.INI`.
- **Monster.SData** distinguishes SQLite values that match the client from
  values that differ. Review and stage a SQLite-to-client import for a single
  monster or for all matching MobIDs.
- The skill editors display Silence and Darkness in the correct slots. The
  item editor shows all three `ReqOG` restrictions. The asset viewer opens
  WAV audio and TGA textures.

Archives contain the editor. Bring your own client and server data. The macOS
apps are ad hoc signed. Full client `.wld` editing remains outside this beta.
See the [user guide](https://github.com/Spelunkern/GX-Studio/blob/main/docs/editing.md)
and verify your download against `SHA256SUMS.txt`.
