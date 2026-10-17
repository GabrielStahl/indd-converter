"""Render the document model to PDF (one PDF page per InDesign page)."""
import io
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.lib.utils import ImageReader
from model import apply, mmul, A_FILL
from layout import FontBook, paragraphs, layout


def _set_color(c, doc, uid, fill=True):
    col = doc.colors.get(uid)
    if not col or not col['model']:
        return False
    if col['model'] == 'CMYK':
        (c.setFillColorCMYK if fill else c.setStrokeColorCMYK)(*col['values'])
    else:
        (c.setFillColorRGB if fill else c.setStrokeColorRGB)(*col['values'])
    return True


def _path(c, item, F):
    p = c.beginPath()
    m = mmul(F, item['m'])
    for sub in item['path']:
        pts = sub['pts']
        if not pts:
            continue
        p.moveTo(*apply(m, *pts[0][1]))
        segs = list(zip(pts, pts[1:])) + ([(pts[-1], pts[0])] if sub['closed'] else [])
        for a, b in segs:
            if a[2] == a[1] and b[0] == b[1]:
                p.lineTo(*apply(m, *b[1]))
            else:
                p.curveTo(*apply(m, *a[2]), *apply(m, *b[0]), *apply(m, *b[1]))
        if sub['closed']:
            p.close()
    return p


def _bbox(item):
    pts = [apply(item['m'], *pt[1]) for s in item['path'] for pt in s['pts']]
    return min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)


def render(doc, out_path, title=None, log=print):
    fb = FontBook(doc)
    first = doc.body_pages[0]
    c = rl_canvas.Canvas(out_path, pagesize=(first['bounds'][2], first['bounds'][3]),
                         initialFontName=fb.get(doc.style_props(doc.no_para_style) if doc.no_para_style else {}))
    if title:
        c.setTitle(title)
    for page in doc.body_pages:
        W, H = page['bounds'][2], page['bounds'][3]
        ox, oy = page['transform'][4], page['transform'][5]
        c.setPageSize((W, H))
        F = (1.0, 0.0, 0.0, -1.0, -ox, H + oy)       # spread coords (y down) -> PDF (y up)
        for it in doc.items:
            if it['spread'] != page['spread']:
                continue
            x0, y0, x1, y1 = _bbox(it)
            if x1 < ox or x0 > ox + W or y1 < oy or y0 > oy + H:
                continue                                # on the pasteboard / another page
            if it['kind'] == 'image' and it['image']:
                im = it['image']
                if not im['jpeg'] or not im['bounds']:
                    log('  image %d: no embedded JPEG data, skipped' % im['uid'])
                    continue
                c.saveState()
                c.clipPath(_path(c, it, F), stroke=0, fill=0)
                l, t, r, b = im['bounds']
                c.transform(*mmul(mmul(F, im['m']), (1, 0, 0, -1, 0, t + b)))
                c.drawImage(ImageReader(io.BytesIO(im['jpeg'])), l, t, r - l, b - t)
                c.restoreState()
                continue
            f = it['fill'] is not None and _set_color(c, doc, it['fill'], True)
            s = it['stroke'] is not None and it['stroke_w'] > 0 and _set_color(c, doc, it['stroke'], False)
            if s:
                c.setLineWidth(it['stroke_w'])
            if f or s:
                c.drawPath(_path(c, it, F), stroke=int(bool(s)), fill=int(bool(f)))
            if it['kind'] == 'text' and it['story']:
                lines = layout(paragraphs(doc, doc.story(it['story'])), x1 - x0, fb)
                y = y0
                for i, ln in enumerate(lines):
                    y += ln['asc'] if i == 0 else ln['lead']
                    if y > y1 + 2:
                        if any(t.strip() for t, *_ in ln['items']):
                            log('  text overflows frame %d: %r ...' % (it['uid'], ''.join(t for t, *_ in ln['items'])[:40]))
                        break
                    x = x0
                    for t, font, size, p in ln['items']:
                        _set_color(c, doc, p.get(A_FILL, 11), True)
                        c.setFont(font, size)
                        fb.draw(c, t, *apply(F, x, y), font, size)
                        x += fb.width(t, font, size)
        c.showPage()
    c.save()
    return fb.substituted
