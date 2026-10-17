#!/usr/bin/env python3
"""Convert an InDesign .indd file to IDML and PDF without InDesign.

    python3 convert.py "CV.indd"              # writes CV.idml and CV.pdf next to it
    python3 convert.py "CV.indd" -o out/ --no-pdf
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from model import Document          # noqa: E402
from idml_writer import IdmlWriter  # noqa: E402
from pdf_writer import render       # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('indd')
    ap.add_argument('-o', '--outdir', help='output folder (default: next to the input file)')
    ap.add_argument('--no-pdf', action='store_true')
    ap.add_argument('--no-idml', action='store_true')
    a = ap.parse_args()

    base = os.path.splitext(os.path.basename(a.indd))[0]
    outdir = a.outdir or os.path.dirname(os.path.abspath(a.indd))
    os.makedirs(outdir, exist_ok=True)

    doc = Document(a.indd)
    print('%s: %d page(s), %d page items, %d stories, %d swatches, %d styles' % (
        os.path.basename(a.indd), len(doc.body_pages), len(doc.items),
        len({i['story'] for i in doc.items if i['story']}), len(doc.colors), len(doc.styles)))

    if not a.no_idml:
        p = os.path.join(outdir, base + '.idml')
        IdmlWriter(doc).write(p, os.path.basename(a.indd))
        print('wrote', p)
    if not a.no_pdf:
        p = os.path.join(outdir, base + '.pdf')
        subs = render(doc, p, title=base)
        print('wrote', p)
        for k, v in sorted(subs.items()):
            print('  font substitution: %s -> %s' % (k, v))


if __name__ == '__main__':
    main()
