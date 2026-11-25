"""Regular EP5 SData envelopes, adapted from the GX/Parsec implementation."""

from struct import Struct, pack_into, unpack_from
from pathlib import Path
from zlib import crc32

import numpy as np

from .seed_constants import ROUND_KEYS, SBOXES

SIGNATURE = b"0001CBCEBC5B2784D3FC9A2A9DB84D1C3FEB6E99"
BLOCK = Struct(">4I")
HEADER_SIZE = 64
KEYS = tuple(zip(ROUND_KEYS[::2], ROUND_KEYS[1::2]))
SBOX_ARRAYS = np.asarray(SBOXES, dtype=np.uint32)
CHUNK_SIZE = 1024 * 1024


def envelope_label(header: bytes, size: int | None = None, *, known: bool = True) -> str:
    if not header.startswith(SIGNATURE):
        if known and len(header) < 4:
            return "Invalid SData"
        return "Unencrypted" if known else "Unknown"
    if len(header) < HEADER_SIZE or (size is not None and size % BLOCK.size):
        return "Invalid header"
    checksum, length, alternate = unpack_from("<III", header, 40)
    length = length if checksum else alternate
    if length == 0 or (size is not None and length > size - HEADER_SIZE):
        return "Invalid header"
    return "Encrypted"


def file_envelope(path: Path, *, known: bool = True) -> str:
    try:
        with path.open("rb") as file:
            return envelope_label(file.read(HEADER_SIZE), path.stat().st_size, known=known)
    except OSError:
        return "Unavailable"


def episode_label(catalog, *, canonical=False) -> str:
    """Label a validated EP5 catalog and mark the standard GX file formats."""
    if canonical or (catalog is not None and catalog.writable):
        return 'EP5.4 <span style="color:#b7f0bc; background-color:#30583a;">(GX)</span>'
    return "EP5" if catalog is not None else ""


def _substitute(word: np.ndarray) -> np.ndarray:
    a, b, c, d = SBOX_ARRAYS
    return a[word & 255] ^ b[(word >> 8) & 255] ^ c[(word >> 16) & 255] ^ d[word >> 24]


def _transform(data: bytes, decrypt: bool) -> bytes:
    keys = KEYS[::-1] if decrypt else KEYS
    output = bytearray(len(data))
    for offset in range(0, len(data), CHUNK_SIZE):
        end = min(offset + CHUNK_SIZE, len(data))
        # SEED's de/encryption blocks are independent. uint32 retains the cipher's modular arithmetic.
        words = np.frombuffer(data, dtype=">u4", count=(end - offset) // 4, offset=offset)
        left0, left1, right0, right1 = words.reshape(-1, 4).T.astype(np.uint32, order="C")
        for key0, key1 in keys:
            t0 = right0 ^ key0
            t1 = _substitute(t0 ^ right1 ^ key1)
            t0 = _substitute(t0 + t1)
            t1 = _substitute(t0 + t1)
            left0, right0 = right0, left0 ^ (t0 + t1)
            left1, right1 = right1, left1 ^ t1
        encoded = np.frombuffer(output, dtype=">u4", count=words.size, offset=offset).reshape(-1, 4)
        encoded.T[:] = right0, right1, left0, left1
    return bytes(output)


def decode(raw: bytes, *, binary=False) -> tuple[bytes, bool]:
    if not raw.startswith(SIGNATURE):
        return raw, True
    if len(raw) < HEADER_SIZE or len(raw) % BLOCK.size:
        raise ValueError("The encryption header is incomplete.")
    checksum, size, alternate_size = unpack_from("<III", raw, 40)
    if not checksum:
        if binary:
            checksum = size
        size = alternate_size
    if not 0 < size <= len(raw) - HEADER_SIZE:
        raise ValueError("The encryption header declares an invalid size.")
    plain = _transform(raw[HEADER_SIZE:], decrypt=True)[:size]
    if checksum and crc32(plain) != checksum:
        if binary:
            # Some BinarySData envelopes include zero alignment bytes in the declared length.
            end = len(plain)
            minimum = max(1, end - (BLOCK.size - 1))
            while end > minimum and plain[end - 1] == 0:
                end -= 1
                if crc32(memoryview(plain)[:end]) == checksum:
                    return plain[:end], True
        raise ValueError("The encrypted file checksum is incorrect.")
    return plain, bool(checksum)


def encode_like(original: bytes, plain: bytes) -> bytes:
    if not original.startswith(SIGNATURE):
        return plain
    checksum = crc32(plain)
    if not checksum:
        raise ValueError("The checksum cannot be represented in the original encryption format.")
    header = bytearray(original[:HEADER_SIZE])
    pack_into("<II", header, 40, checksum, len(plain))
    padded = plain + bytes(-len(plain) % BLOCK.size)
    return bytes(header) + _transform(padded, decrypt=False)
