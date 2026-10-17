# indd-converter

Convert Adobe InDesign `.indd` files to **IDML** and **PDF**, without InDesign.

The `.indd` format is undocumented. This tool reads it from a reverse-engineered understanding of its internal object database, gained by analysing files only.

```
pip install -r requirements.txt
python3 convert.py "MyDocument.indd"     # writes MyDocument.idml + MyDocument.pdf
```

**Supports:** text frames with paragraph/character styles, swatches (CMYK/RGB), rectangles, ovals, lines, and embedded JPEG images.
**Not yet supported:** tables, tabs, threaded frames, rotation, effects, or big-endian (old Mac) files.
Missing fonts are substituted with installed ones in the PDF; the IDML keeps the original font names.

Experimental, tested on one document so far. MIT licensed.
