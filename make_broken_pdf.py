"""
Build a small PDF that DELIBERATELY has these four PDF/UA-1 violations
(and ideally only these), so the accessibility_fix_demo.py script has
something to chew on:

  7.1-2       Document title missing in DocInfo + DisplayDocTitle not set
  7.2-1       Catalog /Lang missing
  7.18.1-2    Annotation has no /Contents
  7.18.6.2-1  Figure structure element has no /Alt

The PDF is hand-built with pikepdf so that every entry in the file is
visible and the omissions are intentional. The XMP metadata claims
PDF/UA-1 so any conformance checker will apply the UA rules to it.
"""

from __future__ import annotations

import pikepdf
from pikepdf import Array, Dictionary, Name, Pdf, Stream


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------

CONTENT_STREAM = b"""\
/Artifact BMC
q 0.95 0.95 0.95 rg 0 0 612 792 re f Q
EMC
/P <</MCID 0>> BDC
BT /F1 18 Tf 72 720 Td (Accessibility Demo PDF) Tj ET
EMC
/P <</MCID 1>> BDC
BT /F1 11 Tf 72 690 Td (This file deliberately violates four PDF/UA-1 rules.) Tj ET
EMC
/Figure <</MCID 2>> BDC
q 0.20 0.55 0.85 rg 72 520 200 120 re f Q
BT /F1 10 Tf 80 530 Td 1 1 1 rg (figure with no /Alt) Tj ET
EMC
/Link <</MCID 3>> BDC
BT /F1 11 Tf 72 470 Td 0 0 1 rg (A link annotation with no /Contents) Tj ET
EMC
"""


XMP_METADATA = b"""<?xpacket begin="\xef\xbb\xbf" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="demo">
  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
    <rdf:Description rdf:about=""
        xmlns:dc="http://purl.org/dc/elements/1.1/"
        xmlns:pdf="http://ns.adobe.com/pdf/1.3/"
        xmlns:xmp="http://ns.adobe.com/xap/1.0/"
        xmlns:pdfuaid="http://www.aiim.org/pdfua/ns/id/">
      <pdfuaid:part>1</pdfuaid:part>
      <pdf:Producer>accessibility-fix-demo generator</pdf:Producer>
      <!-- NOTE: dc:title is intentionally absent -->
    </rdf:Description>
  </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>
"""


def build_pdf(output_path: str) -> None:
    pdf = Pdf.new()

    # ---- font -----------------------------------------------------------
    font = pdf.make_indirect(Dictionary(
        Type=Name.Font,
        Subtype=Name.Type1,
        BaseFont=Name("/Helvetica"),
        Encoding=Name("/WinAnsiEncoding"),
    ))

    # ---- content stream + page ------------------------------------------
    content = pdf.make_stream(CONTENT_STREAM)

    page = pdf.make_indirect(Dictionary(
        Type=Name.Page,
        MediaBox=Array([0, 0, 612, 792]),
        Resources=Dictionary(Font=Dictionary({"/F1": font})),
        Contents=content,
        StructParents=0,
        # /Parent and /Annots are filled in below
    ))

    pages_root = pdf.make_indirect(Dictionary(
        Type=Name.Pages,
        Kids=Array([page]),
        Count=1,
    ))
    page["/Parent"] = pages_root

    # ---- annotation WITHOUT /Contents (violation 7.18.1-2) --------------
    annot = pdf.make_indirect(Dictionary(
        Type=Name.Annot,
        Subtype=Name("/Link"),
        Rect=Array([72, 465, 320, 485]),
        Border=Array([0, 0, 0]),
        A=Dictionary(
            Type=Name.Action,
            S=Name("/URI"),
            URI=pikepdf.String("https://example.com"),
        ),
        StructParent=1,
        # NO /Contents -- the violation
    ))
    page["/Annots"] = Array([annot])

    # ---- structure tree -------------------------------------------------
    struct_tree_root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    doc_elem = pdf.make_indirect(Dictionary(
        Type=Name.StructElem,
        S=Name("/Document"),
        P=struct_tree_root,
    ))
    p1 = pdf.make_indirect(Dictionary(
        Type=Name.StructElem,
        S=Name("/P"),
        P=doc_elem,
        Pg=page,
        K=0,                                   # MCID 0
    ))
    p2 = pdf.make_indirect(Dictionary(
        Type=Name.StructElem,
        S=Name("/P"),
        P=doc_elem,
        Pg=page,
        K=1,                                   # MCID 1
    ))
    figure = pdf.make_indirect(Dictionary(
        Type=Name.StructElem,
        S=Name("/Figure"),
        P=doc_elem,
        Pg=page,
        K=2,                                   # MCID 2
        # NO /Alt -- the violation (7.18.6.2-1)
    ))
    link_se = pdf.make_indirect(Dictionary(
        Type=Name.StructElem,
        S=Name("/Link"),
        P=doc_elem,
        Pg=page,
        K=Array([
            3,                                                # MCID 3
            Dictionary(Type=Name.OBJR, Obj=annot, Pg=page),   # link to annotation
        ]),
    ))

    doc_elem["/K"] = Array([p1, p2, figure, link_se])

    # parent tree: page MCIDs 0..3 -> their struct elements, plus
    # the annotation's StructParent (1) -> link struct element
    parent_tree = pdf.make_indirect(Dictionary(
        Nums=Array([
            0, Array([p1, p2, figure, link_se]),
            1, link_se,
        ])
    ))
    struct_tree_root["/K"] = Array([doc_elem])
    struct_tree_root["/ParentTree"] = parent_tree
    struct_tree_root["/ParentTreeNextKey"] = 2

    # ---- XMP metadata claiming PDF/UA-1 ---------------------------------
    metadata = pdf.make_stream(XMP_METADATA, Type=Name.Metadata, Subtype=Name.XML)

    # ---- catalog --------------------------------------------------------
    catalog = pdf.Root
    catalog["/Pages"] = pages_root
    catalog["/StructTreeRoot"] = struct_tree_root
    catalog["/MarkInfo"] = Dictionary(Marked=True)
    catalog["/Metadata"] = metadata
    # NO /Lang  -- violation 7.2-1
    # NO /ViewerPreferences /DisplayDocTitle -- part of violation 7.1-2

    # ---- DocInfo: deliberately no /Title --------------------------------
    pdf.docinfo["/Producer"] = pikepdf.String("accessibility-fix-demo generator")
    # NO /Title  -- violation 7.1-2
    if "/Title" in pdf.docinfo:
        del pdf.docinfo["/Title"]

    pdf.save(output_path, linearize=False)
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else "broken.pdf"
    build_pdf(out)
