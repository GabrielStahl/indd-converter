"""Low-level reader for the InDesign (.indd) object database.

Reverse-engineered layout (little-endian files, as written by InDesign on Windows/macOS):

  * The file is a sequence of 4096-byte pages. Pages 0 and 1 are master pages; the one
    with the higher sequence number (u64 at 0x108) is current. u32 at 0x118 = number of
    database pages; contiguous objects (XMP, previews) follow them.
  * Every other page ends in a 16-byte trailer  [u32 a][u32 type][u32 x][u32 checksum]:
        type 5  page map: u32 table at offset 0x80, logical page -> physical page
        type 6  index node: u32 count, then 16-byte entries
        type 8  raw 3952-byte chunk of a large object
        type 9  slotted data page, x = its logical page number
  * Slotted pages hold records  [u16 size][u16 slot][body]. Slot 0 is free space.
    Bit 15 of the slot marks an object continued elsewhere: its body starts with
    [u16 0][u16 next slot][u32 next logical page].
  * Location index entries:  (u32 chunk, u32 uid, u16 len, u16 slot, u32 logical page);
    slot 0 means "raw chunk on *physical* page".
  * Class index entries:     (u32 0, u32 uid, u32 class id, u32 0).
  * An object body is a list of attributes  [u32 id][u32 length][data].
"""
import struct
from collections import defaultdict

P = 4096


def parse_attrs(b):
    """Split an object body into [(attr id, bytes)]. Raises ValueError if it isn't one."""
    o, out = 0, []
    while o + 8 <= len(b):
        a, ln = struct.unpack_from('<II', b, o)
        if o + 8 + ln > len(b):
            raise ValueError('not an attribute list')
        out.append((a, b[o + 8:o + 8 + ln]))
        o += 8 + ln
    if o != len(b):
        raise ValueError('trailing bytes')
    return out


class InddDB:
    def __init__(self, path):
        self.d = d = open(path, 'rb').read()
        if d[:16] != bytes.fromhex('0606edf5d81d46e5bd31efe7fe74b71d'):
            raise ValueError('not an InDesign document')
        if d[0x18] != 1:
            raise NotImplementedError('big-endian (old Mac) InDesign files are not supported')
        seqs = [struct.unpack_from('<Q', d, o + 0x108)[0] for o in (0, P)]
        self.master = 0 if seqs[0] >= seqs[1] else P
        self.db_pages = struct.unpack_from('<I', d, self.master + 0x118)[0]
        self.trailer = {p: struct.unpack_from('<IIII', d, p * P + P - 16)
                        for p in range(2, min(self.db_pages, len(d) // P))}
        self._slot_cache = {}
        self._load_pagemap()
        self._load_index()

    # ---------------------------------------------------------------- pages
    def _load_pagemap(self):
        best = None
        for p, (a, t, x, ck) in self.trailer.items():
            if t != 5:
                continue
            n = (P - 16 - 0x80) // 4
            m = list(struct.unpack_from('<%dI' % n, self.d, p * P + 0x80))
            score = sum(1 for lp, v in enumerate(m)
                        if v in self.trailer and self.trailer[v][2] == lp)
            if best is None or score > best[0]:
                best = (score, p, m)
        if best is None:
            raise ValueError('no page map found')
        self.pagemap = best[2]

    def phys(self, lp):
        return self.pagemap[lp] if 0 <= lp < len(self.pagemap) else 0

    def slots(self, lp):
        """{slot: (continued?, bytes)} for a logical data page."""
        if lp in self._slot_cache:
            return self._slot_cache[lp]
        out, p = {}, self.phys(lp)
        if p:
            base, o = p * P, 0
            while o < P - 20:
                sz, slot = struct.unpack_from('<HH', self.d, base + o)
                if sz < 4 or o + sz > P - 16:
                    break
                if slot:
                    out[slot & 0x7fff] = (bool(slot & 0x8000), self.d[base + o + 4: base + o + sz])
                o += sz
        self._slot_cache[lp] = out
        return out

    # ---------------------------------------------------------------- index
    def _load_index(self):
        loc = defaultdict(dict)      # uid -> chunk -> (page, slot, len)
        cls = {}
        for lp, p in enumerate(self.pagemap):
            if not p or self.trailer.get(p, (0, 0))[1] != 6:
                continue
            base = p * P
            cnt = struct.unpack_from('<I', self.d, base)[0]
            if not cnt or cnt > (P - 20) // 16:
                continue
            ents = [struct.unpack_from('<IIHHI', self.d, base + 4 + 16 * i) for i in range(cnt)]
            ok = 0
            for chunk, uid, ln, slot, page in ents[:8]:
                s = self.slots(page).get(slot)
                if chunk >= 1 and (slot == 0 or (s is not None and (s[0] or abs(len(s[1]) - ln) < 4))):
                    ok += 1
            if ok >= min(4, len(ents)):
                for chunk, uid, ln, slot, page in ents:
                    loc[uid][chunk] = (page, slot, ln)
                continue
            ents2 = [struct.unpack_from('<IIII', self.d, base + 4 + 16 * i) for i in range(cnt)]
            if all(e[0] == 0 and e[3] == 0 for e in ents2):
                for _, uid, c, _ in ents2:
                    cls[uid] = c
        self.loc, self.cls = loc, cls

    def _chain(self, lp, slot, ln):
        out, seen = b'', set()
        while len(out) < ln and (lp, slot) not in seen:
            seen.add((lp, slot))
            s = self.slots(lp).get(slot)
            if s is None:
                return None
            cont, body = s
            if not cont:
                out += body
                break
            _, nslot, npage = struct.unpack_from('<HHI', body)
            out += body[8:]
            lp, slot = npage, nslot
        return out[:ln]

    def obj(self, uid):
        """Raw bytes of an object (all chunks concatenated), or None."""
        chunks = self.loc.get(uid)
        if not chunks:
            return None
        out = b''
        for c in sorted(chunks):
            page, slot, ln = chunks[c]
            if slot == 0:
                out += self.d[page * P: page * P + ln]
                continue
            piece = self._chain(page, slot, ln)
            if piece is None:
                return None
            out += piece
        return out

    def uids(self):
        return sorted(self.loc)
