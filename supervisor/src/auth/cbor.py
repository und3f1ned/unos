"""A small, bounded CBOR decoder (RFC 8949) for what WebAuthn sends: the attestation object, the
credential public key (COSE_Key) and the authenticator's extensions (ADR-0409).

Only the major types that WebAuthn needs are read: unsigned and negative integers, byte and text
strings, arrays, maps, and the simple values false, true and null. Tags, floats, undefined, other
simple values and every indefinite length are refused, and so are a nesting deeper than
``max_depth``, an array or map of more than ``MAX_ITEMS`` items, a length beyond the input, a map
key that is neither an integer nor a text, a key given twice and a text that is not UTF-8. The
input comes from a browser, or from a restored database (ADR-0144): nothing it holds can make the
decoder allocate more than the input's size or recurse further than ``max_depth``.
"""
from __future__ import annotations

# The most items an array or a map may hold: a COSE_Key has at most 5, an attestation object 3, a
# certificate chain a few.
MAX_ITEMS = 64
# The deepest nesting that WebAuthn needs: the attestation object (1), its statement (2), a
# certificate chain in it (3) and its certificates (4).
MAX_DEPTH = 4

_FALSE, _TRUE, _NULL = 20, 21, 22


class CborError(ValueError):
    """The input is not CBOR that this decoder reads."""


def decode(data: bytes, *, max_depth: int = MAX_DEPTH) -> object:
    """The one item that ``data`` holds, all of it."""
    value, end = decode_prefix(data, max_depth=max_depth)
    if end != len(data):
        raise CborError("bytes after the CBOR item")
    return value


def decode_prefix(data: bytes, *, max_depth: int = MAX_DEPTH) -> tuple[object, int]:
    """The item at the start of ``data``, and where it ends: the authenticator data holds the
    credential's public key followed by more bytes."""
    if not isinstance(data, (bytes, bytearray)):
        raise CborError("CBOR input must be bytes")
    return _Reader(bytes(data), max_depth).item(0)


class _Reader:
    def __init__(self, data: bytes, max_depth: int) -> None:
        self.data = data
        self.pos = 0
        self.max_depth = max_depth

    def take(self, size: int) -> bytes:
        if size < 0 or size > len(self.data) - self.pos:
            raise CborError("CBOR item runs past the end of the input")
        chunk = self.data[self.pos:self.pos + size]
        self.pos += size
        return chunk

    def argument(self, info: int) -> int:
        if info < 24:
            return info
        if info == 24:
            return self.take(1)[0]
        if info == 25:
            return int.from_bytes(self.take(2), "big")
        if info == 26:
            return int.from_bytes(self.take(4), "big")
        if info == 27:
            return int.from_bytes(self.take(8), "big")
        # 28 to 30 are reserved; 31 is an indefinite length or a break.
        raise CborError("indefinite or reserved CBOR length")

    def count(self, info: int) -> int:
        items = self.argument(info)
        if items > MAX_ITEMS:
            raise CborError(f"CBOR array or map of more than {MAX_ITEMS} items")
        return items

    def item(self, depth: int) -> tuple[object, int]:
        head = self.take(1)[0]
        major, info = head >> 5, head & 0x1F
        if major == 0:
            return self.argument(info), self.pos
        if major == 1:
            return -1 - self.argument(info), self.pos
        if major == 2:
            return self.take(self.argument(info)), self.pos
        if major == 3:
            raw = self.take(self.argument(info))
            try:
                return raw.decode("utf-8"), self.pos
            except UnicodeDecodeError:
                raise CborError("CBOR text that is not UTF-8") from None
        if major in (4, 5):
            if depth >= self.max_depth:
                raise CborError("CBOR nested too deeply")
            items = self.count(info)
            if major == 4:
                return [self.item(depth + 1)[0] for _ in range(items)], self.pos
            result: dict[object, object] = {}
            for _ in range(items):
                key = self.item(depth + 1)[0]
                if isinstance(key, bool) or not isinstance(key, (int, str)):
                    raise CborError("CBOR map key that is neither an integer nor a text")
                if key in result:
                    raise CborError("CBOR map key given twice")
                result[key] = self.item(depth + 1)[0]
            return result, self.pos
        if major == 7 and info in (_FALSE, _TRUE, _NULL):
            return {_FALSE: False, _TRUE: True, _NULL: None}[info], self.pos
        # Tags (6), floats, undefined and the other simple values (7).
        raise CborError("CBOR type that WebAuthn does not use")
