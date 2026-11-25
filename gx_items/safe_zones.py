"""Read and update the server's SafeZones.INI as authored on disk."""

from dataclasses import dataclass
from math import isfinite
from pathlib import Path
import re
from struct import Struct

from .equipment_models import ModelAssets
from .install import save_model_table

SECTION = re.compile(r"^\s*\[([^\]]+)\]")
BLOCK_COMMENT = re.compile(r'/\*.*?\*/', re.S)
FLOAT32 = Struct("<f")


@dataclass(frozen=True, slots=True)
class SafeZone:
    name: str
    map_id: int
    faction: str
    points: tuple[tuple[float, float], ...]

    def validated(self):
        if not self.name.strip() or any(token in self.name for token in ("[", "]", "\r", "\n", "/*", "*/")):
            raise ValueError("Enter a valid INI section name.")
        if self.faction.casefold() not in ("light", "fury", "both") or not 0 <= self.map_id <= 65535:
            raise ValueError("Choose Light, Fury or Both for a valid map ID.")
        if len(self.points) != 4:
            raise ValueError("A safe zone requires four vertices.")
        try:
            points = tuple(tuple(FLOAT32.unpack(FLOAT32.pack(value))[0] for value in point) for point in self.points)
        except OverflowError as error:
            raise ValueError("A safe-zone coordinate exceeds the native range.") from error
        if not all(isfinite(value) for point in points for value in point):
            raise ValueError("Safe-zone coordinates must be finite.")
        directions = []
        for i, (x, z) in enumerate(points):
            bx, bz = points[(i + 1) % 4]
            cx, cz = points[(i + 2) % 4]
            directions.append((bx - x) * (cz - z) - (bz - z) * (cx - x))
        if not all(direction * directions[0] > 0 for direction in directions):
            raise ValueError("Vertices must form a convex quadrilateral in perimeter order.")
        return SafeZone(self.name.strip(), self.map_id, self.faction.title(), points)


class SafeZones:
    def __init__(self, raw: bytes):
        self.raw = raw
        self.bom = raw.startswith(b"\xef\xbb\xbf")
        self.text = raw.decode("utf-8-sig", errors="surrogateescape")
        self.lines = self.text.splitlines(keepends=True)
        self.newline = "\r\n" if "\r\n" in self.text else "\n"
        fields = {}
        names = {}
        self.headers = {}
        clean = BLOCK_COMMENT.sub(lambda match: "\n" * match[0].count("\n"), self.text)
        current = None
        for index, line in enumerate(clean.splitlines()):
            if match := SECTION.match(line):
                name = match[1].strip()
                current = name.upper()
                names.setdefault(current, name)
                fields.setdefault(current, {})
                self.headers[index] = current
            elif current is not None and not line.lstrip().startswith(("//", "#", ";")):
                key, sep, value = line.partition("=")
                if sep and key.strip():
                    fields[current][key.strip().upper()] = value.split("//", 1)[0].split(";", 1)[0].strip().strip('"')
        records = []
        for key, values in fields.items():
            try:
                if values.keys() != {"MAP", "PROTECTEDFACTION", "VERTICES"}:
                    raise ValueError("Expected Map, ProtectedFaction and Vertices.")
                values_xz = tuple(float(value) for value in re.split(r"[(),\s]+", values["VERTICES"].strip(" ,()")) if value)
                points = tuple(zip(values_xz[::2], values_xz[1::2]))
                if len(values_xz) != 8:
                    raise ValueError("Expected four X, Z pairs.")
                records.append(SafeZone(names[key], int(values["MAP"]), values["PROTECTEDFACTION"], points).validated())
            except ValueError as error:
                raise ValueError(f"[{names[key]}] {error}") from error
        self.zones = tuple(records)

    @classmethod
    def open(cls, path):
        return cls(path.read_bytes())

    def for_map(self, map_id):
        return tuple(zone for zone in self.zones if zone.map_id == map_id)

    def encode_map(self, map_id, zones):
        zones = tuple(zone.validated() for zone in zones)
        old = {zone.name.upper(): zone for zone in self.for_map(map_id)}
        new = {zone.name.upper(): zone for zone in zones}
        reserved = {zone.name.upper() for zone in self.zones if zone.map_id != map_id}
        if len(new) != len(zones) or reserved.intersection(new):
            raise ValueError("Safe-zone section names must be unique across all maps.")
        if any(zone.map_id != map_id for zone in zones):
            raise ValueError("A safe zone belongs to a different map.")
        if old == new:
            return self.raw
        changed = {name for name in old | new if old.get(name) != new.get(name)}
        written = set()
        output = []
        active = None
        for index, line in enumerate(self.lines):
            if index in self.headers:
                active = self.headers[index]
                if active not in changed:
                    output.append(line)
                elif active in new and active not in written:
                    output.extend(self.section(new[active]))
                    written.add(active)
            elif active not in changed or not line.strip() or line.lstrip().startswith((";", "#", "//", "/*", "*", "*/")):
                output.append(line)
        for name, zone in new.items():
            if name in changed and name not in written:
                if output and not output[-1].endswith(("\n", "\r")):
                    output[-1] += self.newline
                output.extend((self.newline, *self.section(zone)))
        raw = "".join(output).encode("utf-8", errors="surrogateescape")
        return (b"\xef\xbb\xbf" if self.bom else b"") + raw

    def section(self, zone):
        vertices = " ".join(f"({x:.9g}, {z:.9g})" for x, z in zone.points)
        return tuple(line + self.newline for line in (
            f"[{zone.name}]", f"Map = {zone.map_id}", f"ProtectedFaction = {zone.faction}", f"Vertices = {vertices}",
        ))

    def save(self, path: Path, map_id: int, zones, backups):
        check_map_config(path, map_id)
        raw = self.encode_map(map_id, zones)
        type(self)(raw)
        return save_model_table(path, self, raw, backups, format_name="SafeZones", read=Path.read_bytes)


def check_map_config(path, map_id):
    if (config := ModelAssets(path.parent).resolve("Map.ini")) is None:
        return
    source = BLOCK_COMMENT.sub("", config.read_bytes().decode("utf-8-sig", errors="replace"))
    for line in source.splitlines():
        header = SECTION.match(line)
        if header:
            prefix, separator, identifier = header[1].strip().casefold().rpartition("_")
            if prefix == "set_zone" and separator and identifier.isascii() and identifier.isdecimal():
                if int(identifier) == map_id:
                    return
    raise ValueError(f"Map {map_id} is missing from server Map.ini.")
