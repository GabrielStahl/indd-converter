"""Decoders for the value encodings used inside InDesign objects."""
import struct


def read_string(b, o):
    """InDesign string: [u16 nchars][u16 flags|len][chars]. 0x4000 = 8-bit, 0x8000 = UTF-16."""
    n, fl = struct.unpack_from('<HH', b, o)
    o += 4
    ln = fl & 0x3fff
    if fl & 0x8000:
        return b[o:o + 2 * ln].decode('utf-16le', 'replace'), o + 2 * ln
    return b[o:o + ln].decode('cp1252', 'replace'), o + ln


def _value(vt, v):
    if len(v) == 8:
        return struct.unpack('<d', v)[0]
    if vt == 0x1b02:                     # string value: 02 02 00 + string
        return read_string(v, 3)[0] if len(v) >= 7 else ''
    if len(v) == 4:
        return struct.unpack('<I', v)[0]
    if len(v) == 2:
        return struct.unpack('<H', v)[0]
    if len(v) == 1:
        return v[0]
    return v


def attr_list(b, o=0):
    """Text attribute list: [u16 count] + count x [u32 attr][u16 size][u16 1][u32 vtype][u16 len][value].
    Returns ({attr: value}, new offset)."""
    n = struct.unpack_from('<H', b, o)[0]
    o += 2
    out = {}
    for _ in range(n):
        a, sz = struct.unpack_from('<IH', b, o)
        o += 6
        body = b[o:o + sz]
        o += sz
        if len(body) >= 8:
            _, vt, ln = struct.unpack_from('<HIH', body)
            out[a] = _value(vt, body[8:8 + ln])
    return out, o


def graphic_attrs(v):
    """Page-item graphic attributes (attr 0x6e03): [u32 count] + [u32 id][u16 size][u16 n][n x (u32 tag, u16 len, data)]."""
    n = struct.unpack_from('<I', v)[0]
    o, out = 4, {}
    for _ in range(n):
        aid, sz = struct.unpack_from('<IH', v, o)
        body = v[o + 6:o + 6 + sz]
        o += 6 + sz
        k = struct.unpack_from('<H', body)[0]
        p, vals = 2, []
        for _ in range(k):
            t, ln = struct.unpack_from('<IH', body, p)
            d = body[p + 6:p + 6 + ln]
            p += 6 + ln
            if t == 0x6e63:              # metadata tag, not a value
                continue
            vals.append(struct.unpack('<d', d)[0] if ln == 8 else
                        struct.unpack('<I', d)[0] if ln == 4 else d)
        out[aid] = vals[0] if len(vals) == 1 else vals
    return out
