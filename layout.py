"""Fonts and text layout for the PDF renderer."""
import glob
import logging
import os
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont as RLFont
from fontTools.ttLib import TTFont, TTCollection
from model import A_FILL, A_FONTSTYLE, A_SIZE, A_CAPS, A_FONT, A_AUTOLEAD, A_LEADING

logging.getLogger('fontTools').setLevel(logging.ERROR)

FONT_DIRS = ['/System/Library/Fonts', '/System/Library/Fonts/Supplemental', '/Library/Fonts',
             os.path.expanduser('~/Library/Fonts'), 'C:/Windows/Fonts', '/usr/share/fonts']

# Fonts used by InDesign documents that are not installed, mapped to close installed ones.
# Apple's "Gill Sans Bold" is ~10% wider than Monotype's "Gill Sans MT Bold"; its SemiBold
# cut matches the MT Bold widths almost exactly.
SUBSTITUTES = {
    'gill sans mt': ('Gill Sans', {'bold': 'SemiBold', 'bold italic': 'SemiBold Italic'}),
    'minion pro': ('Times New Roman', {}),
    'myriad pro': ('Helvetica', {}),
}
BUILTIN = {False: {False: 'Helvetica', True: 'Helvetica-Oblique'},
           True: {False: 'Helvetica-Bold', True: 'Helvetica-BoldOblique'}}


def _names(f):
    n = f['name']
    fam = n.getDebugName(16) or n.getDebugName(1)
    sty = n.getDebugName(17) or n.getDebugName(2)
    return (fam or '').lower(), (sty or '').lower()


class FontBook:
    """Resolves (family, style) to an embeddable TrueType font and provides metrics."""
    _index = None

    def __init__(self, doc):
        self.doc = doc
        self.cache, self.metrics = {}, {}
        self.substituted = {}

    @classmethod
    def index(cls):
        if cls._index is None:
            idx = {}
            for d in FONT_DIRS:
                for path in glob.glob(os.path.join(d, '*.tt[fc]')):
                    try:
                        fonts = (TTCollection(path, lazy=True).fonts if path.lower().endswith('.ttc')
                                 else [TTFont(path, lazy=True)])
                        for i, f in enumerate(fonts):
                            if 'glyf' in f:                       # reportlab embeds TrueType only
                                idx.setdefault(_names(f), (path, i))
                    except Exception:
                        pass
            cls._index = idx
        return cls._index

    def lookup(self, fam, sty):
        idx = self.index()
        hit = idx.get((fam.lower(), sty.lower()))
        if hit:
            return hit, None
        sub = SUBSTITUTES.get(fam.lower())
        if sub:
            sfam, smap = sub
            ssty = smap.get(sty.lower(), sty)
            for cand in (ssty, sty, 'regular'):
                hit = idx.get((sfam.lower(), cand.lower()))
                if hit:
                    return hit, '%s %s' % (sfam, cand)
        return None, None

    def get(self, props):
        fam = self.doc.fonts.get(props.get(A_FONT), 'Minion Pro')
        sty = props.get(A_FONTSTYLE, 'Regular')
        key = (fam, sty)
        if key in self.cache:
            return self.cache[key]
        (hit, used) = self.lookup(fam, sty)
        name = None
        if hit:
            name = ('%s-%s' % (fam, sty)).replace(' ', '')
            try:
                pdfmetrics.registerFont(RLFont(name, hit[0], subfontIndex=hit[1]))
                self._load_metrics(name, *hit)
            except Exception:
                name = None
        if name is None:
            name = BUILTIN['Bold' in sty]['Italic' in sty or 'Oblique' in sty]
            used = name
        if used:
            self.substituted['%s %s' % key] = used
        self.cache[key] = name
        return name

    def _load_metrics(self, name, path, index):
        """Kerning pairs + height of 'd' (InDesign's 'Ascent' first-baseline rule)."""
        ft = TTFont(path, fontNumber=index, lazy=True)
        upm = ft['head'].unitsPerEm
        cmap = ft.getBestCmap()
        kern = {}
        if 'kern' in ft:
            g2c = {}
            for cp, g in cmap.items():
                g2c.setdefault(g, []).append(chr(cp))
            for (a, b), v in ft['kern'].kernTables[0].kernTable.items():
                for ca in g2c.get(a, ()):
                    for cb in g2c.get(b, ()):
                        kern[ca + cb] = v / upm
        d_h = ft['glyf'][cmap[ord('d')]].yMax / upm if ord('d') in cmap else None
        self.metrics[name] = {'kern': kern, 'd': d_h}

    def kerns(self, s, font):
        k = self.metrics.get(font, {}).get('kern')
        return [k.get(s[i:i + 2], 0.0) for i in range(len(s) - 1)] if k else [0.0] * max(0, len(s) - 1)

    def width(self, s, font, size):
        return pdfmetrics.stringWidth(s, font, size) + sum(self.kerns(s, font)) * size

    def ascent(self, font, size):
        d = self.metrics.get(font, {}).get('d')
        return d * size if d else pdfmetrics.getFont(font).face.ascent * size / 1000.0

    def draw(self, c, s, x, y, font, size):
        """Draw with pair kerning: split the string wherever a kern adjustment applies."""
        start = 0
        for i, k in enumerate(self.kerns(s, font)):
            if k:
                seg = s[start:i + 1]
                c.drawString(x, y, seg)
                x += pdfmetrics.stringWidth(seg, font, size) + k * size
                start = i + 1
        c.drawString(x, y, s[start:])


def paragraphs(doc, story):
    """Split a story into paragraphs of (text, resolved props) spans."""
    text = story['text']
    cprops = []
    for r in story['cruns']:
        p = dict(doc.style_props(r['style']))
        p.update(r['attrs'])
        cprops += [p] * r['n']
    out, pos = [], 0
    for r in story['pruns']:
        base = dict(doc.style_props(r['style']))
        base.update(r['attrs'])
        for ln in r['paras']:
            spans, cur, curkey, curp = [], '', None, None
            for i in range(pos, min(pos + ln, len(text))):
                cp = dict(base)
                if i < len(cprops):
                    cp.update(cprops[i])
                key = repr(sorted(cp.items()))
                if curkey is not None and key != curkey:
                    spans.append((cur, curp))
                    cur = ''
                curkey, curp = key, cp
                cur += text[i]
            if cur:
                spans.append((cur, curp))
            out.append({'props': base, 'spans': spans})
            pos += ln
    return out


def layout(paras, width, fb):
    """Greedy line breaking. Returns lines {'items': [(text, font, size, props)], 'asc', 'lead'}."""
    lines = []
    for para in paras:
        toks = []
        for txt, p in para['spans']:
            txt = txt.replace('\ufeff', '').replace('\r', '')
            if p.get(A_CAPS) == 2:
                txt = txt.upper()
            buf = ''
            for ch in txt:
                if ch in '\n\u2028':
                    if buf:
                        toks.append((buf, p))
                        buf = ''
                    toks.append(('\n', p))
                    continue
                if (ch == ' ' and buf and buf[-1] != ' ') or (ch != ' ' and buf.endswith(' ')):
                    toks.append((buf, p))
                    buf = ''
                buf += ch
            if buf:
                toks.append((buf, p))

        def flush(cur):
            pp = cur[0][3] if cur else para['props']
            fonts = cur or [('', fb.get(pp), pp.get(A_SIZE, 12.0), pp)]
            lead = pp.get(A_LEADING)
            if not isinstance(lead, float) or lead > 5000:          # 'Auto' leading
                lead = max(it[2] for it in fonts) * pp.get(A_AUTOLEAD, 1.2)
            lines.append({'items': cur, 'asc': max(fb.ascent(it[1], it[2]) for it in fonts), 'lead': lead})

        cur, curw = [], 0.0
        for t, p in toks:
            if t == '\n':
                flush(cur)
                cur, curw = [], 0.0
                continue
            font, size = fb.get(p), p.get(A_SIZE, 12.0)
            if cur and t.strip() and curw + fb.width(t.rstrip(' '), font, size) > width + 0.01:
                flush(cur)
                cur, curw = [], 0.0
            cur.append((t, font, size, p))
            curw += fb.width(t, font, size)
        flush(cur)
    return lines
