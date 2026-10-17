"""Turn the raw InDesign object database into a document model:
pages, page items (text frames, shapes, images), stories, styles and swatches."""
import struct
from inddb import InddDB, parse_attrs
from values import read_string, attr_list, graphic_attrs

# Object class ids (reverse engineered)
C_SPREAD, C_PAGE, C_SPLINE, C_GROUP = 0x501, 0x50f, 0x6201, 0x401
C_MULTICOL, C_FRAME, C_IMAGE = 0x263, 0x227, 0x1702
C_STORY, C_TEXT, C_PARA_RUNS, C_CHAR_RUNS = 0x201, 0xca18, 0x27f, 0xca17
C_STYLE, C_COLOR, C_NONE_SWATCH, C_FONT = 0x205, 0x1f05, 0x6e0b, 0x3e03

# Text attribute ids
A_FILL, A_FONTSTYLE, A_SIZE, A_STROKE = 0x1b01, 0x1b02, 0x1b03, 0x1b12
A_CAPS, A_LEADING, A_AUTOLEAD, A_FONT = 0x1b11, 0x1b13, 0x1b1a, 0x1b2b

# Graphic attribute ids
G_STROKE_W, G_STROKE, G_FILL = 0x6e65, 0x6e64, 0x6e68

IDENT = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def mmul(a, b):
    """Affine matrices (a, b, c, d, tx, ty): result applies b first, then a."""
    return (a[0] * b[0] + a[2] * b[1], a[1] * b[0] + a[3] * b[1],
            a[0] * b[2] + a[2] * b[3], a[1] * b[2] + a[3] * b[3],
            a[0] * b[4] + a[2] * b[5] + a[4], a[1] * b[4] + a[3] * b[5] + a[5])


def apply(m, x, y):
    return m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]


def parse_path(v):
    """Attr 0x162b: [u32 nsub] per subpath: [u32 npts] points, [u16 open flag].
    Point type 2 = corner (anchor only); otherwise (in-handle, anchor, out-handle)."""
    o = 4
    subs = []
    for _ in range(struct.unpack_from('<I', v)[0]):
        n = struct.unpack_from('<I', v, o)[0]
        o += 4
        pts = []
        for _ in range(n):
            t = struct.unpack_from('<I', v, o)[0]
            o += 4
            if t == 2:
                x, y = struct.unpack_from('<2d', v, o)
                o += 16
                pts.append(((x, y), (x, y), (x, y)))
            else:
                c = struct.unpack_from('<6d', v, o)
                o += 48
                pts.append(((c[0], c[1]), (c[2], c[3]), (c[4], c[5])))
        closed = True
        if o + 2 <= len(v):
            closed = struct.unpack_from('<H', v, o)[0] == 0
            o += 2
        subs.append({'pts': pts, 'closed': closed})
    return subs


def decode_text(v):
    """Text data (0xca18 / attr 0x262): runs of 8-bit or UTF-16 segments."""
    o = 10
    out = ''
    for _ in range(struct.unpack_from('<H', v, 8)[0]):
        rb, nch = struct.unpack_from('<II', v, o)
        end, o, got = o + 4 + rb, o + 8, 0
        while got < nch and o < end:
            fl = struct.unpack_from('<H', v, o)[0]
            o += 2
            ln = fl & 0x3fff
            if fl & 0x8000:
                out += v[o:o + 2 * ln].decode('utf-16le', 'replace')
                o += 2 * ln
            else:
                out += v[o:o + ln].decode('cp1252', 'replace')
                o += ln
            got += ln
        o = end
    return out


def decode_runs(v, para=False):
    """Style runs: [u32 bytes][u32 nchars][u32 style uid][attr list] (+ paragraph lengths)."""
    o = 10
    runs = []
    for _ in range(struct.unpack_from('<H', v, 8)[0]):
        rb, nch, style = struct.unpack_from('<III', v, o)
        end = o + 4 + rb
        attrs, o2 = attr_list(v, o + 12)
        r = {'n': nch, 'style': style, 'attrs': attrs}
        if para:
            k = struct.unpack_from('<H', v, o2)[0]
            r['paras'] = [x & 0xffffff for x in struct.unpack_from('<%dI' % k, v, o2 + 2)]
        runs.append(r)
        o = end
    return runs


class Document:
    def __init__(self, path):
        self.db = db = InddDB(path)
        self.A = {}
        for u in db.uids():
            b = db.obj(u)
            try:
                self.A[u] = dict(parse_attrs(b)) if b else {}
            except ValueError:
                self.A[u] = {}
        self._hierarchy()
        self._swatches()
        self._fonts()
        self._styles()
        self._pages()
        self._items()

    def cls(self, u):
        return self.db.cls.get(u, 0)

    def of_class(self, c):
        return [u for u in self.db.uids() if self.cls(u) == c]

    def u32(self, u, attr, off=0):
        v = self.A.get(u, {}).get(attr)
        return struct.unpack_from('<I', v, off)[0] if v and len(v) >= off + 4 else None

    # ------------------------------------------------------------ structure
    def _hierarchy(self):
        self.parent, self.children, self.spread_of = {}, {}, {}
        for u, a in self.A.items():
            v = a.get(0x15b)
            if v and len(v) >= 12:
                sp, par, n = struct.unpack_from('<III', v)
                self.spread_of[u], self.parent[u] = sp, par
                self.children[u] = list(struct.unpack_from('<%dI' % n, v, 12)) if n else []

    def transform(self, u):
        v = self.A.get(u, {}).get(0x151)
        return struct.unpack('<6d', v) if v and len(v) == 48 else IDENT

    def to_spread(self, u):
        m, p = self.transform(u), self.parent.get(u)
        while p and p in self.parent:
            m = mmul(self.transform(p), m)
            p = self.parent.get(p)
        return m

    # ------------------------------------------------------------ resources
    def _swatches(self):
        self.colors = {}
        for u in self.of_class(C_COLOR):
            a = self.A[u]
            v = a.get(0x1f10)
            name = read_string(v, 3)[0] if v and v[0] == 0 else ''     # '' = unnamed colour
            model, vals = None, []
            v = a.get(0x1f01)
            if v and len(v) >= 6:
                n = struct.unpack_from('<H', v, 4)[0]
                vals = list(struct.unpack_from('<%dd' % n, v, 6))
                model = {3: 'RGB', 4: 'CMYK'}.get(n)
            if name == 'Paper':
                vals = [0.0, 0.0, 0.0, 0.0]
            self.colors[u] = {'name': name, 'model': model, 'values': vals}
        self.none_swatches = set(self.of_class(C_NONE_SWATCH))

    def _fonts(self):
        self.fonts = {}
        for u in self.of_class(C_FONT):
            v = self.A[u].get(0x3e05)
            if v:
                self.fonts[u] = read_string(v, 5)[0]

    def _styles(self):
        self.styles = {}
        for u in self.of_class(C_STYLE):
            a = self.A[u]
            v = a.get(0x230)
            if not v or len(v) < 33:
                continue
            props = attr_list(a[0x23f])[0] if 0x23f in a else {}
            self.styles[u] = {'name': read_string(v, 29)[0], 'based': struct.unpack_from('<I', v, 4)[0],
                              'props': props}
        self.no_para_style = next((u for u, s in self.styles.items() if s['name'] == '[No paragraph style]'), None)

    def style_props(self, sid):
        chain, s = [], sid
        while s in self.styles and s not in chain:
            chain.append(s)
            s = self.styles[s]['based']
        if self.no_para_style and self.no_para_style not in chain and sid in self.styles \
                and self.styles[sid]['name'] != '[No character style]':
            chain.append(self.no_para_style)
        out = {}
        for s in reversed(chain):
            out.update(self.styles[s]['props'])
        return out

    # ------------------------------------------------------------ pages
    def _pages(self):
        self.pages = []
        for u in self.of_class(C_PAGE):
            a = self.A[u]
            self.pages.append({
                'uid': u, 'spread': self.spread_of.get(u),
                'bounds': struct.unpack('<4d', a[0x5dd]) if 0x5dd in a else (0, 0, 595.28, 841.89),
                'transform': struct.unpack('<6d', a[0x5cc]) if 0x5cc in a else IDENT,
                'master': self.cls(self.spread_of.get(u)) != C_SPREAD})
        self.body_pages = sorted((p for p in self.pages if not p['master']), key=lambda p: (p['spread'], p['uid']))
        self.master_pages = [p for p in self.pages if p['master']]

    # ------------------------------------------------------------ stories
    def story(self, sid):
        """Text + paragraph runs + character runs, read via the story's strand list (attr 0x223)."""
        st = {'uid': sid, 'text': '', 'pruns': [], 'cruns': []}
        v = self.A.get(sid, {}).get(0x223)
        strands = []
        if v and len(v) >= 14:
            first, n = struct.unpack_from('<II', v, 6)
            strands = [first] + list(struct.unpack_from('<%dI' % n, v, 14))
        text_parts = []
        for s in strands:
            r = self.A.get(s, {}).get(0x261)
            if not r:
                continue
            k = struct.unpack_from('<H', r)[0]
            for i in range(k):
                ln, data = struct.unpack_from('<II', r, 2 + 8 * i)
                body = self.A.get(data, {}).get(0x262)
                if body is None:
                    continue
                c = self.cls(data)
                if c == C_TEXT:
                    text_parts.append(decode_text(body))
                elif c == C_PARA_RUNS:
                    st['pruns'] += decode_runs(body, para=True)
                elif c == C_CHAR_RUNS:
                    st['cruns'] += decode_runs(body)
        st['text'] = ''.join(text_parts)
        return st

    def _story_for_frame(self, frame):
        fl = self.u32(frame, 0x220)           # -> frame list (0x228) -> story
        return self.u32(fl, 0x2ab, 4) if fl else None

    # ------------------------------------------------------------ page items
    def _items(self):
        self.items = []
        body_spreads = {p['spread'] for p in self.body_pages}
        kids = {}
        for u, p in self.parent.items():
            kids.setdefault(p, []).append(u)
        for u in self.of_class(C_SPLINE):
            if self.spread_of.get(u) not in body_spreads or 0x162b not in self.A[u]:
                continue
            a = self.A[u]
            g = graphic_attrs(a[0x6e03]) if 0x6e03 in a else {}
            shape = a.get(0x6204)
            it = {'uid': u, 'spread': self.spread_of[u], 'path': parse_path(a[0x162b]),
                  'm': self.to_spread(u), 'local_m': self.transform(u),
                  'in_group': self.cls(self.parent.get(u)) == C_GROUP,
                  'shape': struct.unpack_from('<H', shape, 2)[0] if shape and len(shape) >= 4 else 0,
                  'fill': g.get(G_FILL), 'stroke': g.get(G_STROKE), 'stroke_w': g.get(G_STROKE_W) or 0.0,
                  'kind': 'shape', 'story': None, 'image': None}
            for c in set(self.children.get(u, [])) | set(kids.get(u, [])):
                if self.cls(c) == C_MULTICOL:
                    it['kind'] = 'text'
                    for f in set(self.children.get(c, [])) | set(kids.get(c, [])):
                        if self.cls(f) == C_FRAME:
                            it['story'] = self._story_for_frame(f)
                elif self.cls(c) == C_IMAGE:
                    it['kind'] = 'image'
                    it['image'] = self._image(c)
            self.items.append(it)
        self.items.sort(key=lambda i: i['uid'])

    def _image(self, u):
        a = self.A[u]
        data = None
        cands = [self.u32(u, 0x8cbc, 8), self.u32(u, 0x1262a)] + list(range(u, u + 6))
        for c in cands:
            b = self.db.obj(c) if c else None
            if b and b[:3] == b'\xff\xd8\xff':
                data = b
                break
        link = None
        for c in range(u - 12, u + 12):
            b = self.db.obj(c) or b''
            i = b.find(b'file:')
            if i >= 0:
                link = b[i:].split(b'\x00')[0].decode('utf-8', 'replace').rstrip('\x01\x02')
                break
        return {'uid': u, 'm': self.to_spread(u), 'local_m': self.transform(u),
                'bounds': struct.unpack('<4d', a[0x1633]) if 0x1633 in a else None,
                'jpeg': data, 'link': link}
