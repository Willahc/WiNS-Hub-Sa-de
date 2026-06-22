"""
wins_hub_saude_dbc.py
Pure-Python DATASUS .dbc -> .dbf decoder.

A .dbc file is a DBF whose record stream is compressed with the
PKWare Data Compression Library (DCL) "implode"/"explode" algorithm
(a.k.a. "blast"). This module ports Mark Adler's blast.c (zlib contrib)
to pure Python and wraps it with the DBC container logic so the result
can be read with dbfread.

Public API:
    decompress_blast(data, offset=0) -> bytes        # raw blast/explode decoder
    dbc_to_dbf(dbc_path, dbf_path)   -> dbf_path      # full container decode
"""

import struct


# ---------------------------------------------------------------------------
# blast / PKWare DCL explode  (port of Mark Adler's blast.c)
# ---------------------------------------------------------------------------

class _BitInput:
    __slots__ = ("data", "pos", "bitbuf", "bitcnt")

    def __init__(self, data, offset=0):
        self.data = data
        self.pos = offset
        self.bitbuf = 0
        self.bitcnt = 0

    def bits(self, need):
        """Return need bits from the stream (LSB first)."""
        val = self.bitbuf
        cnt = self.bitcnt
        data = self.data
        pos = self.pos
        while cnt < need:
            if pos >= len(data):
                raise EOFError("ran out of input in blast stream")
            val |= data[pos] << cnt
            pos += 1
            cnt += 8
        self.bitbuf = val >> need
        self.bitcnt = cnt - need
        self.pos = pos
        return val & ((1 << need) - 1)


class _Huffman:
    """Canonical Huffman decode table as built by blast.c construct()."""
    __slots__ = ("count", "symbol")

    def __init__(self, rep):
        # rep is a compact run-length encoding of code lengths, exactly
        # as in blast.c: high nibble = length, low nibble = repeat count.
        MAXBITS = 13
        length = []
        for code in rep:
            # blast.c construct(): high nibble = repeat count, low nibble = length
            c = (code >> 4) + 1
            n = code & 15
            length.extend([n] * c)

        count = [0] * (MAXBITS + 1)
        for ln in length:
            count[ln] += 1

        # generate offsets into symbol table for each length
        offs = [0] * (MAXBITS + 1)
        for i in range(1, MAXBITS):
            offs[i + 1] = offs[i] + count[i]

        symbol = [0] * len(length)
        for sym, ln in enumerate(length):
            if ln != 0:
                symbol[offs[ln]] = sym
                offs[ln] += 1

        self.count = count
        self.symbol = symbol


def _decode(bitin, h):
    """Decode one symbol using huffman table h. Mirrors blast.c decode()."""
    code = 0
    first = 0
    index = 0
    count = h.count
    # blast reads bits inverted (MSB of code stream); replicate exactly.
    bitbuf = bitin.bitbuf
    bitcnt = bitin.bitcnt
    data = bitin.data
    pos = bitin.pos
    length = 1
    while True:
        if bitcnt == 0:
            if pos >= len(data):
                raise EOFError("out of input in decode")
            bitbuf = data[pos]
            pos += 1
            bitcnt = 8
        # take one bit, inverted
        bit = (bitbuf & 1) ^ 1
        bitbuf >>= 1
        bitcnt -= 1
        code |= bit
        cnt = count[length]
        if code - cnt < first:
            bitin.bitbuf = bitbuf
            bitin.bitcnt = bitcnt
            bitin.pos = pos
            return h.symbol[index + (code - first)]
        index += cnt
        first += cnt
        first <<= 1
        code <<= 1
        length += 1
        if length > 13:
            raise ValueError("invalid code in blast stream")


# Static tables from blast.c
_LITCODE = _Huffman([
    11, 124, 8, 7, 28, 7, 188, 13, 76, 4, 10, 8, 12, 10, 12, 10, 8, 23, 8,
    9, 7, 6, 7, 8, 7, 6, 55, 8, 23, 24, 12, 11, 7, 9, 11, 12, 6, 7, 22, 5,
    7, 24, 6, 11, 9, 6, 7, 22, 7, 11, 38, 7, 9, 8, 25, 11, 8, 11, 9, 12, 8,
    12, 5, 38, 5, 38, 5, 11, 7, 5, 6, 21, 6, 10, 53, 8, 7, 24, 10, 27, 44,
    253, 253, 253, 252, 252, 252, 13, 12, 45, 12, 45, 12, 61, 12, 45, 44,
    173,
])
_LENCODE = _Huffman([2, 35, 36, 53, 38, 23])
_DISTCODE = _Huffman([2, 20, 53, 230, 247, 151, 248])

# length base and extra-bits tables from blast.c
_LENBASE = [3, 2, 4, 5, 6, 7, 8, 9, 10, 12, 16, 24, 40, 72, 136, 264]
_EXTRA = [0, 0, 0, 0, 0, 0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8]


def decompress_blast(data, offset=0):
    """Decompress a PKWare DCL ('blast') stream starting at offset."""
    bitin = _BitInput(data, offset)

    lit = bitin.bits(8)
    if lit > 1:
        raise ValueError("invalid literal flag in blast header: %d" % lit)
    dict_bits = bitin.bits(8)
    if dict_bits < 4 or dict_bits > 6:
        raise ValueError("invalid dictionary size in blast header: %d" % dict_bits)

    out = bytearray()
    while True:
        if bitin.bits(1):
            # length/distance pair
            sym = _decode(bitin, _LENCODE)
            length = _LENBASE[sym] + bitin.bits(_EXTRA[sym])
            if length == 519:
                break  # end of stream
            # distance
            sym = _decode(bitin, _DISTCODE)
            if length == 2:
                dist = (sym << 2) + bitin.bits(2)
            else:
                dist = (sym << dict_bits) + bitin.bits(dict_bits)
            dist += 1
            # copy length bytes from dist back
            start = len(out) - dist
            if start < 0:
                raise ValueError("invalid distance in blast stream")
            for i in range(length):
                out.append(out[start + i])
        else:
            # literal
            if lit:
                sym = _decode(bitin, _LITCODE)
            else:
                sym = bitin.bits(8)
            out.append(sym)
    return bytes(out)


# ---------------------------------------------------------------------------
# DBC container
# ---------------------------------------------------------------------------

def dbc_to_dbf(dbc_path, dbf_path):
    """Decode a DATASUS .dbc file into a plain .dbf file."""
    with open(dbc_path, "rb") as f:
        raw = f.read()

    # DBF header fields
    n_records = struct.unpack_from("<I", raw, 4)[0]
    header_len = struct.unpack_from("<H", raw, 8)[0]
    record_len = struct.unpack_from("<H", raw, 10)[0]

    expected = n_records * record_len

    header = raw[:header_len]

    # The blast stream starts at or just after the DBF header. DATASUS adds
    # a small CRC field after the header. Try the common offsets and validate
    # against expected decompressed size.
    last_err = None
    for delta in (header_len, header_len + 2, header_len + 0, header_len + 4,
                  header_len + 1, header_len + 8):
        try:
            decoded = decompress_blast(raw, delta)
        except Exception as e:  # noqa
            last_err = e
            continue
        if len(decoded) >= expected:
            decoded = decoded[:expected]
            records = decoded
            with open(dbf_path, "wb") as f:
                f.write(header)
                f.write(records)
                f.write(b"\x1a")  # DBF EOF marker
            return dbf_path
        last_err = ValueError(
            "decoded %d bytes, expected %d (offset %d)"
            % (len(decoded), expected, delta))
    raise RuntimeError("could not decode %s: %s" % (dbc_path, last_err))


if __name__ == "__main__":
    import sys
    src = sys.argv[1]
    dst = sys.argv[2] if len(sys.argv) > 2 else src[:-4] + ".dbf"
    out = dbc_to_dbf(src, dst)
    print("wrote", out)
