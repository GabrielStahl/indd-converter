"""Write the document model as an IDML package (InDesign Markup Language)."""
import base64
import re
import zipfile
from xml.sax.saxutils import escape as _escape, quoteattr as _quoteattr
from model import A_FILL, A_FONTSTYLE, A_SIZE, A_CAPS, A_FONT, A_LEADING

DOM = '15.0'
NS = 'xmlns:idPkg="http://ns.adobe.com/AdobeInDesign/idml/1.0/packaging"'
HDR = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
LAYER = 'uLayer1'
SPECIAL = {'Black': 'Specialblack', 'Paper': 'Specialpaper', 'Registration': 'Specialregistration'}
CAPS = {0: 'Normal', 1: 'SmallCaps', 2: 'AllCaps', 3: 'CapToSmallCap'}
NO_PARA, NO_CHAR = 'ParagraphStyle/$ID/[No paragraph style]', 'CharacterStyle/$ID/[No character style]'
_BAD = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]')


def escape(s):
    return _escape(_BAD.sub('', s))


def quoteattr(s):
    return _quoteattr(_BAD.sub('', s))


def num(x):
    return ('%.6f' % x).rstrip('0').rstrip('.') if isinstance(x, float) else str(x)


def mat(m):
    return ' '.join(num(float(v)) for v in m)


def frame_pref():
    return ('<TextFramePreference TextColumnCount="1" TextColumnGutter="12" FirstBaselineOffset="AscentOffset" '
            'VerticalJustification="TopAlign"/>')


class IdmlWriter:
    def __init__(self, doc):
        self.doc = doc
        self.color_names, used = {}, set()
        for u, c in sorted(doc.colors.items()):
            n = c['name'] or 'u%x' % u
            if n in used:
                n = '%s %d' % (n, u)
            used.add(n)
            self.color_names[u] = n
        # styles referenced from character runs are character styles, the rest paragraph styles
        self.stories = sorted({it['story'] for it in doc.items if it['kind'] == 'text' and it['story']})
        self.story_data = {s: doc.story(s) for s in self.stories}
        self.char_styles = {r['style'] for st in self.story_data.values() for r in st['cruns']}

    # ------------------------------------------------------------ references
    def color_ref(self, uid):
        if uid is None or uid in self.doc.none_swatches or uid not in self.color_names:
            return 'Swatch/None'
        return 'Color/' + self.color_names[uid]

    def style_ref(self, sid):
        st = self.doc.styles.get(sid)
        char = sid in self.char_styles
        if not st or st['name'] in ('[No paragraph style]', '[No character style]'):
            return NO_CHAR if char else NO_PARA
        name = '$ID/NormalParagraphStyle' if st['name'] == 'NormalParagraphStyle' else st['name']
        return ('CharacterStyle/' if char else 'ParagraphStyle/') + name

    def text_attrs(self, props):
        at, pr = {}, {}
        for k, v in props.items():
            if k == A_FILL and isinstance(v, int):
                at['FillColor'] = self.color_ref(v)
            elif k == A_FONTSTYLE and isinstance(v, str):
                at['FontStyle'] = v
            elif k == A_SIZE and isinstance(v, float):
                at['PointSize'] = num(v)
            elif k == A_CAPS and v in CAPS:
                at['Capitalization'] = CAPS[v]
            elif k == A_FONT and v in self.doc.fonts:
                pr['AppliedFont'] = self.doc.fonts[v]
            elif k == A_LEADING and isinstance(v, float):
                pr['Leading'] = 'Auto' if v > 5000 else num(v)
        return at, pr

    @staticmethod
    def props_xml(pr):
        if not pr:
            return ''
        out = '<Properties>'
        for k, v in pr.items():
            if k == 'BasedOn':
                out += '<BasedOn type="object">%s</BasedOn>' % escape(v)
            elif k == 'Leading':
                out += ('<Leading type="enumeration">Auto</Leading>' if v == 'Auto'
                        else '<Leading type="unit">%s</Leading>' % v)
            else:
                out += '<%s type="string">%s</%s>' % (k, escape(v), k)
        return out + '</Properties>'

    @staticmethod
    def attrs_xml(at):
        return ''.join(' %s=%s' % (k, quoteattr(str(v))) for k, v in at.items())

    # ------------------------------------------------------------ resources
    def graphic_xml(self):
        out = [HDR, '<idPkg:Graphic %s DOMVersion="%s">' % (NS, DOM)]
        for u, c in sorted(self.doc.colors.items()):
            name = self.color_names[u]
            space = 'RGB' if c['model'] == 'RGB' else 'CMYK'
            vals = c['values'] or [0, 0, 0, 0]
            vals = [v * (255 if space == 'RGB' else 100) for v in vals]
            if not c['name']:
                extra = ' ColorOverride="Hiddenreserved" ColorEditable="true" ColorRemovable="true"'
            elif name in SPECIAL:
                extra = ' ColorOverride="%s" ColorEditable="false" ColorRemovable="false"' % SPECIAL[name]
            else:
                extra = ' ColorOverride="Normal" ColorEditable="true" ColorRemovable="true"'
            out.append('<Color Self=%s Model="Process" Space="%s" ColorValue="%s"%s BaseColor="n" Name=%s '
                       'Visible="%s" SwatchCreatorID="7937"/>'
                       % (quoteattr('Color/' + name), space, ' '.join(num(float(v)) for v in vals), extra,
                          quoteattr(c['name'] or '$ID/'), 'true' if c['name'] else 'false'))
        out.append('<Swatch Self="Swatch/None" Name="None" ColorEditable="false" ColorRemovable="false" '
                   'Visible="true" SwatchCreatorID="7937"/>')
        out.append('</idPkg:Graphic>')
        return '\n'.join(out)

    def styles_xml(self):
        d = self.doc
        out = [HDR, '<idPkg:Styles %s DOMVersion="%s">' % (NS, DOM), '<RootCharacterStyleGroup Self="u79">',
               '<CharacterStyle Self="%s" Imported="false" Name="$ID/[No character style]"/>' % NO_CHAR]
        para = []
        for sid, st in sorted(d.styles.items()):
            if st['name'] in ('[No paragraph style]', '[No character style]'):
                continue
            if sid not in self.char_styles:
                para.append(sid)
                continue
            ref = self.style_ref(sid)
            at, pr = self.text_attrs(st['props'])
            pr['BasedOn'] = self.style_ref(st['based']) if st['based'] in d.styles else NO_CHAR
            out.append('<CharacterStyle Self=%s Imported="false" Name=%s%s>%s</CharacterStyle>'
                       % (quoteattr(ref), quoteattr(ref.split('/', 1)[1]), self.attrs_xml(at), self.props_xml(pr)))
        out += ['</RootCharacterStyleGroup>', '<RootParagraphStyleGroup Self="u78">']
        base = d.styles[d.no_para_style]['props'] if d.no_para_style else {}
        at, pr = self.text_attrs(base)
        out.append('<ParagraphStyle Self="%s" Name="$ID/[No paragraph style]" Imported="false"%s>%s</ParagraphStyle>'
                   % (NO_PARA, self.attrs_xml(at), self.props_xml(pr)))
        for sid in para:
            st = d.styles[sid]
            ref = self.style_ref(sid)
            at, pr = self.text_attrs(st['props'])
            pr['BasedOn'] = self.style_ref(st['based']) if st['based'] in d.styles else NO_PARA
            out.append('<ParagraphStyle Self=%s Name=%s Imported="false" NextStyle=%s%s>%s</ParagraphStyle>'
                       % (quoteattr(ref), quoteattr(ref.split('/', 1)[1]), quoteattr(ref),
                          self.attrs_xml(at), self.props_xml(pr)))
        out += ['</RootParagraphStyleGroup>', '</idPkg:Styles>']
        return '\n'.join(out)

    def prefs_xml(self):
        pg = self.doc.body_pages[0]
        W, H = pg['bounds'][2], pg['bounds'][3]
        return (HDR + '<idPkg:Preferences %s DOMVersion="%s">\n' % (NS, DOM) +
                '<DocumentPreference PageHeight="%s" PageWidth="%s" PagesPerDocument="%d" FacingPages="false" '
                'DocumentBleedTopOffset="0" DocumentBleedBottomOffset="0" DocumentBleedInsideOrLeftOffset="0" '
                'DocumentBleedOutsideOrRightOffset="0" DocumentBleedUniformSize="true" PageBinding="LeftToRight" '
                'ColumnDirection="Horizontal" Intent="PrintIntent"/>\n</idPkg:Preferences>'
                % (num(H), num(W), len(self.doc.body_pages)))

    # ------------------------------------------------------------ page items
    @staticmethod
    def path_xml(item):
        out = '<Properties><PathGeometry>'
        for sub in item['path']:
            out += '<GeometryPathType PathOpen="%s"><PathPointArray>' % ('false' if sub['closed'] else 'true')
            for lft, anc, rgt in sub['pts']:
                out += '<PathPointType Anchor="%s %s" LeftDirection="%s %s" RightDirection="%s %s"/>' % (
                    num(anc[0]), num(anc[1]), num(lft[0]), num(lft[1]), num(rgt[0]), num(rgt[1]))
            out += '</PathPointArray></GeometryPathType>'
        return out + '</PathGeometry></Properties>'

    def item_xml(self, it):
        m = it['m'] if it['in_group'] else it['local_m']        # groups are flattened
        common = ' ItemLayer="%s" ItemTransform="%s" Visible="true" Locked="false"' % (LAYER, mat(m))
        sid = 'u%x' % it['uid']
        if it['kind'] == 'text' and it['story']:
            return ('<TextFrame Self="%s" ParentStory="u%x" PreviousTextFrame="n" NextTextFrame="n" '
                    'ContentType="TextType" FillColor="Swatch/None" StrokeColor="Swatch/None" StrokeWeight="0"%s>'
                    '%s%s</TextFrame>' % (sid, it['story'], common, self.path_xml(it), frame_pref()))
        stroke = self.color_ref(it['stroke']) if it['stroke_w'] else 'Swatch/None'
        paint = ' FillColor="%s" StrokeColor="%s" StrokeWeight="%s"' % (
            self.color_ref(it['fill']), stroke, num(float(it['stroke_w'])))
        npts = sum(len(s['pts']) for s in it['path'])
        if not all(s['closed'] for s in it['path']) and npts == 2:
            tag = 'GraphicLine'
        else:
            tag = {3: 'Rectangle', 4: 'Oval'}.get(it['shape'], 'Polygon')
        inner, ctype = '', 'Unassigned'
        im = it['image']
        if it['kind'] == 'image' and im and im['jpeg'] and im['bounds']:
            ctype = 'GraphicType'
            l, t, r, b = im['bounds']
            link = ''
            if im['link']:
                link = ('<Link Self="u%x" LinkResourceURI=%s LinkResourceFormat="$ID/JPEG" StoredState="Embedded" '
                        'LinkClassID="35906" LinkClientID="257" LinkResourceModified="false" LinkObjectModified="false" '
                        'ShowInUI="true" CanEmbed="true" CanUnembed="true" CanPackage="true" '
                        'ImportPolicy="NoAutoImport" ExportPolicy="NoAutoExport" LinkResourceSize="0~%x"/>'
                        % (im['uid'] + 1, quoteattr(im['link']), len(im['jpeg'])))
            inner = ('<Image Self="u%x" Space="$ID/RGB" ImageTypeName="$ID/JPEG" ImageRenderingIntent="UseColorSettings" '
                     'LocalDisplaySetting="Default" ItemTransform="%s"><Properties><Profile type="string">$ID/None</Profile>'
                     '<GraphicBounds Left="%s" Top="%s" Right="%s" Bottom="%s"/><Contents><![CDATA[%s]]></Contents>'
                     '</Properties>%s</Image>'
                     % (im['uid'], mat(im['local_m']), num(l), num(t), num(r), num(b),
                        base64.b64encode(im['jpeg']).decode(), link))
        return '<%s Self="%s" ContentType="%s"%s%s>%s%s</%s>' % (
            tag, sid, ctype, paint, common, self.path_xml(it), inner, tag)

    def page_xml(self, pg, name, master):
        W, H = pg['bounds'][2], pg['bounds'][3]
        return ('<Page Self="u%x" Name="%s" AppliedMaster="%s" OverrideList="" MasterPageTransform="1 0 0 1 0 0" '
                'GeometricBounds="0 0 %s %s" ItemTransform="%s"><MarginPreference ColumnCount="1" ColumnGutter="12" '
                'Top="36" Bottom="36" Left="36" Right="36" ColumnDirection="Horizontal" ColumnsPositions="0 %s"/></Page>'
                % (pg['uid'], name, master, num(H), num(W), mat(pg['transform']), num(W - 72)))

    def spread_xml(self, spread, pages, master_self, first_no):
        out = [HDR, '<idPkg:Spread %s DOMVersion="%s">' % (NS, DOM),
               '<Spread Self="u%x" ItemTransform="1 0 0 1 0 0" ShowMasterItems="true" PageCount="%d" '
               'BindingLocation="0" AllowPageShuffle="true">' % (spread, len(pages))]
        out += [self.page_xml(pg, str(first_no + i), master_self or 'n') for i, pg in enumerate(pages)]
        out += [self.item_xml(it) for it in self.doc.items if it['spread'] == spread]
        out += ['</Spread>', '</idPkg:Spread>']
        return '\n'.join(out)

    def master_xml(self, master_self, pages):
        out = [HDR, '<idPkg:MasterSpread %s DOMVersion="%s">' % (NS, DOM),
               '<MasterSpread Self="%s" ItemTransform="1 0 0 1 0 0" NamePrefix="A" BaseName="Master" '
               'Name="A-Master" ShowMasterItems="true" PageCount="%d">' % (master_self, len(pages))]
        out += [self.page_xml(pg, 'A', 'n') for pg in pages]
        out += ['</MasterSpread>', '</idPkg:MasterSpread>']
        return '\n'.join(out)

    # ------------------------------------------------------------ stories
    def story_xml(self, sid):
        st = self.story_data[sid]
        out = [HDR, '<idPkg:Story %s DOMVersion="%s">' % (NS, DOM),
               '<Story Self="u%x" AppliedTOCStyle="n" TrackChanges="false" StoryTitle="$ID/" AppliedNamedGrid="n">' % sid,
               '<StoryPreference OpticalMarginAlignment="false" OpticalMarginSize="12" FrameType="TextFrameType" '
               'StoryOrientation="Horizontal" StoryDirection="LeftToRightDirection"/>']
        text = st['text']
        if text.endswith('\r'):
            text = text[:-1]             # every story ends with an implicit, unwritten return
        cr = []
        for r in st['cruns']:
            cr += [r] * r['n']
        pos = 0
        for r in st['pruns']:
            at, pr = self.text_attrs(r['attrs'])
            out.append('<ParagraphStyleRange AppliedParagraphStyle=%s%s>%s'
                       % (quoteattr(self.style_ref(r['style'])), self.attrs_xml(at), self.props_xml(pr)))
            end = min(pos + r['n'], len(text))
            i = pos
            while i < end:
                run = cr[i] if i < len(cr) else None
                j = i
                while j < end and (cr[j] if j < len(cr) else None) is run:
                    j += 1
                at, pr = self.text_attrs(run['attrs'] if run else {})
                cs = self.style_ref(run['style']) if run else NO_CHAR
                out.append('<CharacterStyleRange AppliedCharacterStyle=%s%s>%s'
                           % (quoteattr(cs), self.attrs_xml(at), self.props_xml(pr)))
                parts = text[i:j].split('\r')
                for k, part in enumerate(parts):
                    part = part.replace('﻿', '').replace('\n', ' ')
                    if part:
                        out.append('<Content>%s</Content>' % escape(part))
                    if k < len(parts) - 1:
                        out.append('<Br/>')
                out.append('</CharacterStyleRange>')
                i = j
            out.append('</ParagraphStyleRange>')
            pos += r['n']
        out += ['</Story>', '</idPkg:Story>']
        return '\n'.join(out)

    # ------------------------------------------------------------ package
    def write(self, path, name='document.indd'):
        d = self.doc
        files = {'Resources/Graphic.xml': self.graphic_xml(),
                 'Resources/Styles.xml': self.styles_xml(),
                 'Resources/Preferences.xml': self.prefs_xml()}
        master_self = None
        if d.master_pages:
            master_self = 'u%x' % d.master_pages[0]['spread']
            mpages = [p for p in d.master_pages if p['spread'] == d.master_pages[0]['spread']]
            files['MasterSpreads/MasterSpread_%s.xml' % master_self] = self.master_xml(master_self, mpages)
        spreads, n = [], 1
        for sp in sorted({p['spread'] for p in d.body_pages}):
            pages = [p for p in d.body_pages if p['spread'] == sp]
            files['Spreads/Spread_u%x.xml' % sp] = self.spread_xml(sp, pages, master_self, n)
            spreads.append(sp)
            n += len(pages)
        for s in self.stories:
            files['Stories/Story_u%x.xml' % s] = self.story_xml(s)
        dm = [HDR + '<?aid style="50" type="document" readerVersion="6.0" featureSet="257" product="15.0(0)" ?>',
              '<Document %s DOMVersion="%s" Self="d" StoryList="%s" Name=%s ZeroPoint="0 0" ActiveLayer="%s" '
              'CMYKProfile="Coated FOGRA39 (ISO 12647-2:2004)" RGBProfile="sRGB IEC61966-2.1">'
              % (NS, DOM, ' '.join('u%x' % s for s in self.stories), quoteattr(name), LAYER),
              '<idPkg:Graphic src="Resources/Graphic.xml"/>',
              '<idPkg:Styles src="Resources/Styles.xml"/>',
              '<idPkg:Preferences src="Resources/Preferences.xml"/>',
              '<Layer Self="%s" Name="Ebene 1" Visible="true" Locked="false" IgnoreWrap="false" ShowGuides="true" '
              'LockGuides="false" UI="true" Expendable="true" Printable="true"/>' % LAYER]
        if master_self:
            dm.append('<idPkg:MasterSpread src="MasterSpreads/MasterSpread_%s.xml"/>' % master_self)
        dm += ['<idPkg:Spread src="Spreads/Spread_u%x.xml"/>' % sp for sp in spreads]
        dm.append('<Section Self="uSection1" Length="%d" Name="" ContinueNumbering="true" IncludeSectionPrefix="false" '
                  'PageNumberStyle="Arabic" PageStart="u%x" SectionPrefix="" Marker=""/>'
                  % (len(d.body_pages), d.body_pages[0]['uid']))
        dm += ['<idPkg:Story src="Stories/Story_u%x.xml"/>' % s for s in self.stories]
        dm.append('</Document>')
        container = (HDR + '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                     '<rootfiles><rootfile full-path="designmap.xml" media-type="text/xml"/></rootfiles></container>')
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr(zipfile.ZipInfo('mimetype'), 'application/vnd.adobe.indesign-idml-package',
                       compress_type=zipfile.ZIP_STORED)
            z.writestr('designmap.xml', '\n'.join(dm), compress_type=zipfile.ZIP_DEFLATED)
            z.writestr('META-INF/container.xml', container, compress_type=zipfile.ZIP_DEFLATED)
            for k, v in files.items():
                z.writestr(k, v, compress_type=zipfile.ZIP_DEFLATED)
