"""
Demo: detect PDF/UA accessibility violations with veraPDF,
then resolve each violation back to a concrete pikepdf object
so it can be inspected and (in a real tool) fixed.

Pipeline:
  1. Run `verapdf --flavour ua1 --format xml input.pdf`  -> XML report
  2. Parse every <rule status="failed">/<check>          -> (rule_id, context, msg)
  3. Walk the veraPDF "context" path through pikepdf     -> the offending object
  4. Dispatch on the rule id                             -> a per-rule fixer
  5. Save the patched PDF

Real veraPDF context paths look like:

    root/document[0]/pages[0](6 0 obj PDPage)/annots[0](9 0 obj PDLinkAnnot)
    root/document[0]/StructTreeRoot[0]/K[0](7 0 obj SEDocument)/K[2](14 0 obj SEFigure)
    root/document[0]/pages[0]/contentStream[0]/content[1]{mcid:0}/contentItem[0]{mcid:0}

The `(N obj M ClassName)` and `{mcid:N}` parts are debug annotations from
veraPDF's model -- we strip them and walk just the `name[index]` skeleton.

veraPDF and pikepdf use independent object models, so resolving back to
pikepdf is a hand-written translation table. The `step()` function below
covers the common cases; extend it as new segments appear in reports.

Heads-up on rule numbering: veraPDF's "rule id" is `<clause>-<testNumber>`.
The clause numbers (7.1, 7.2, 7.3, 7.18 ...) are PDF/UA-1 clauses and stable,
but the test numbers come from the validation-profile XML and can shift
between veraPDF releases. The fixer registry below targets veraPDF 1.31.
If your version emits different test numbers, just re-key the @fixer
decorators -- the rule message text usually tells you what to map to what.

Prerequisites:
  * Java 11+ on PATH (verapdf is a Java tool)
  * `verapdf` CLI on PATH  (https://docs.verapdf.org/install/)
  * pip install pikepdf
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Callable, Optional

import pikepdf
from pikepdf import Array, Dictionary, Name, Pdf


# ---------------------------------------------------------------------------
# Data model for a single failed check
# ---------------------------------------------------------------------------

@dataclass
class Violation:
    rule_id: str           # e.g. "7.3-1"
    clause: str            # e.g. "7.3"
    test_number: str       # e.g. "1"
    description: str       # human-readable rule description
    context: str           # veraPDF location path
    message: str           # error message for this specific check

    def __str__(self) -> str:
        return f"[{self.rule_id}] {self.context} - {self.description}"


# ---------------------------------------------------------------------------
# 1. Run verapdf and parse the XML report
# ---------------------------------------------------------------------------

def run_verapdf(pdf_path: str, flavour: str = "ua1") -> str:
    """Run the verapdf CLI and return the raw XML report as a string."""
    if shutil.which("verapdf") is None:
        sys.exit("ERROR: `verapdf` CLI not found on PATH. "
                 "Install it from https://verapdf.org/software/")
    result = subprocess.run(
        ["verapdf", "--flavour", flavour, "--format", "xml", pdf_path],
        capture_output=True, text=True, check=False,
    )
    if not result.stdout.strip():
        sys.exit(f"verapdf produced no output. stderr was:\n{result.stderr}")
    return result.stdout


def parse_report(xml_text: str) -> list[Violation]:
    """Extract every failed check from a verapdf XML report."""
    root = ET.fromstring(xml_text)
    violations: list[Violation] = []

    # Structure: report/jobs/job/validationReport/details/rule/check
    for rule in root.iter("rule"):
        if rule.attrib.get("status") != "failed":
            continue
        clause = rule.attrib.get("clause", "")
        test_no = rule.attrib.get("testNumber", "")
        description = (rule.findtext("description") or "").strip()

        for check in rule.findall("check"):
            if check.attrib.get("status") != "failed":
                continue
            context = (check.findtext("context") or "").strip()
            message = (check.findtext("errorMessage")
                       or check.findtext("message")
                       or "").strip()
            violations.append(Violation(
                rule_id=f"{clause}-{test_no}",
                clause=clause,
                test_number=test_no,
                description=description,
                context=context,
                message=message,
            ))
    return violations


# ---------------------------------------------------------------------------
# 2. Context resolver: turn the path string into a pikepdf object
# ---------------------------------------------------------------------------

# Strip "(... )" or "{...}" decorations from a single path segment.
_DECORATION_RE = re.compile(r"\s*[({].*$")

# A clean segment looks like "pages" or "pages[0]".
_SEGMENT_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_]*)(?:\[(\d+)\])?$")


def parse_segments(context: str):
    """Yield clean (name, index) tuples from a veraPDF context path."""
    for raw in context.split("/"):
        cleaned = _DECORATION_RE.sub("", raw).strip()
        if not cleaned:
            continue
        m = _SEGMENT_RE.match(cleaned)
        if not m:
            yield cleaned, 0
            continue
        yield m.group(1), int(m.group(2)) if m.group(2) else 0


def resolve_context(context: str, pdf: Pdf) -> Optional[Any]:
    """Walk a veraPDF context path through pikepdf, return the target object."""
    if not context:
        return None
    current: Any = pdf
    for name, idx in parse_segments(context):
        current = step(current, name, idx)
        if current is None:
            return None        # unmapped segment -- extend `step`
    return current


def step(current: Any, name: str, index: int) -> Optional[Any]:
    """
    One step of the walk. Given the current pikepdf object and the next
    path segment, return the next object - or None if we don't know how
    to follow that segment yet. Extend this as new segments come up.
    """
    try:
        if name in ("root", "document", "PDDocument"):
            return current                              # stay on the Pdf

        if name in ("catalog", "PDCatalog"):
            return current.Root if isinstance(current, Pdf) else current

        if name in ("pages", "PDPage"):
            # Return the raw page Dictionary (not the pikepdf.Page wrapper)
            # so downstream isinstance(element, Dictionary) checks work.
            if isinstance(current, Pdf):
                return current.pages[index].obj
            return current.Root.pages[index].obj        # /Catalog -> /Pages

        if name in ("annots", "PDAnnot"):
            annots = current.get("/Annots")
            return annots[index] if annots is not None else None

        if name in ("metadata", "PDMetadata"):
            holder = current.Root if isinstance(current, Pdf) else current
            return holder.get("/Metadata")

        # The XMPPackage segment in "metadata[0]/XMPPackage[0]" is veraPDF's
        # parsed view of the XMP stream. pikepdf doesn't have a separate
        # object for it -- the metadata stream IS the package -- so stay put.
        if name == "XMPPackage":
            return current

        if name in ("StructTreeRoot", "structTreeRoot"):
            holder = current.Root if isinstance(current, Pdf) else current
            return holder.get("/StructTreeRoot")

        if name in ("K", "children"):
            # /K can be a single kid OR an array of kids. Index into either.
            kids = current.get("/K")
            if kids is None:
                return None
            if isinstance(kids, Array):
                return kids[index]
            return kids if index == 0 else None

        # Content-stream-level segments. veraPDF parses the page's
        # content stream into a tree of operators / marked-content items
        # that pikepdf does not expose directly. For our fixers these
        # don't need a resolved element -- the catalog-level fix handles
        # the violation -- so we route them back to the page (the current
        # object) so the walk doesn't dead-end.
        if name in ("contentStream", "operators", "content", "contentItem"):
            return current

        if name in ("font", "PDFont"):
            # Font dicts live under the page's resources.
            page = current
            if isinstance(page, Dictionary) and "/Resources" in page:
                fonts = page["/Resources"].get("/Font")
                if fonts:
                    keys = list(fonts.keys())
                    if 0 <= index < len(keys):
                        return fonts[keys[index]]
            return None

        # TODO extend with:
        #   xobject, image, form, outlines, action, fontDescriptor, MarkInfo
        return None
    except (KeyError, IndexError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# 3. Per-rule fixers
#
# Rule ids below are from veraPDF 1.31 PDF/UA-1 profile. If your version
# emits different test numbers, retarget the decorators -- the fixer
# bodies stay the same.
# ---------------------------------------------------------------------------

FixFn = Callable[[Any, Pdf], None]
_FIXERS: dict[str, FixFn] = {}


def fixer(*rule_ids: str) -> Callable[[FixFn], FixFn]:
    """Register one function under one or more veraPDF rule ids."""
    def decorator(fn: FixFn) -> FixFn:
        for rid in rule_ids:
            _FIXERS[rid] = fn
        return fn
    return decorator


@fixer("7.1-9")   # XMP metadata stream does not contain dc:title
def fix_xmp_title(_element: Any, pdf: Pdf) -> None:
    with pdf.open_metadata() as meta:
        if not meta.get("dc:title"):
            meta["dc:title"] = "TODO: real document title"
            print("    Fix : added dc:title to XMP metadata")


@fixer("7.1-10")  # ViewerPreferences/DisplayDocTitle missing or false
def fix_display_doc_title(_element: Any, pdf: Pdf) -> None:
    if not pdf.docinfo.get("/Title"):
        pdf.docinfo["/Title"] = "TODO: real document title"
        print("    Fix : set placeholder /Title in DocInfo")
    prefs = pdf.Root.get("/ViewerPreferences")
    if prefs is None:
        prefs = pdf.make_indirect(Dictionary())
        pdf.Root["/ViewerPreferences"] = prefs
    prefs["/DisplayDocTitle"] = True
    print("    Fix : set /ViewerPreferences /DisplayDocTitle = true")


@fixer("7.2-1", "7.2-34")
# 7.2-1   : Catalog /Lang missing
# 7.2-34  : Natural language for text in page content cannot be determined
# Both are resolved by setting /Lang on the catalog. 7.2-34 may fire once
# per text run; making the fixer idempotent keeps repeats harmless.
def fix_lang(_element: Any, pdf: Pdf) -> None:
    if not pdf.Root.get("/Lang"):
        pdf.Root["/Lang"] = "en"          # set the REAL BCP-47 tag here
        print("    Fix : set catalog /Lang = 'en' (placeholder)")
    else:
        print("    Fix : catalog /Lang already set, nothing to do")


@fixer("7.18.1-2", "7.18.5-2")
# Annotation has no /Contents (alt text). Two related rules describe the
# same defect for link annotations -- one fixer covers both.
def fix_annotation_contents(element: Any, _pdf: Pdf) -> None:
    if isinstance(element, Dictionary) and not element.get("/Contents"):
        element["/Contents"] = "TODO: describe this annotation"
        print("    Fix : added placeholder /Contents on annotation")


@fixer("7.3-1")   # Figure struct element has neither /Alt nor /ActualText
def fix_figure_alt(element: Any, _pdf: Pdf) -> None:
    if isinstance(element, Dictionary):
        element["/Alt"] = "TODO: describe this figure"
        print("    Fix : added placeholder /Alt on figure struct element")


@fixer("7.18.3-1")  # Page with annotations missing /Tabs = /S
def fix_page_tabs(element: Any, _pdf: Pdf) -> None:
    if isinstance(element, Dictionary):
        element["/Tabs"] = Name("/S")
        print("    Fix : set page /Tabs = /S")


def apply_fix(v: Violation, element: Any, pdf: Pdf) -> None:
    fn = _FIXERS.get(v.rule_id)
    if fn is None:
        print(f"    Fix : no automatic handler for rule {v.rule_id} "
              f"- flag for manual review")
        return
    fn(element, pdf)


# ---------------------------------------------------------------------------
# 4. Orchestration
# ---------------------------------------------------------------------------

def describe(element: Any) -> str:
    """Cheap human-readable summary of a pikepdf object."""
    if element is None:
        return "<none>"
    if isinstance(element, Pdf):
        return f"Pdf ({len(element.pages)} pages)"
    if isinstance(element, Dictionary):
        keys = list(element.keys())
        type_ = element.get("/Type") or element.get("/S") or "Dictionary"
        return f"{type_} keys={keys[:6]}"
    return type(element).__name__


def main() -> None:
    # -----------------------------------------------------------------------
    # Hardcoded configuration
    # -----------------------------------------------------------------------
    PDF_PATH = "/Users/nadeen/PDFAccessGuru/brokem.pdf"              # path to the input PDF
    OUTPUT_PATH = "/Users/nadeen/PDFAccessGuru/output-fixed.pdf"    # path for the patched copy
    FLAVOUR = "ua1"                     # veraPDF flavour

    print(f"Running veraPDF on {PDF_PATH} (flavour={FLAVOUR}) ...")

    report_xml = run_verapdf(PDF_PATH, FLAVOUR)
    violations = parse_report(report_xml)

    print(f"veraPDF reported {len(violations)} failed checks.\n")

    with pikepdf.open(PDF_PATH, allow_overwriting_input=False) as pdf:
        for v in violations:
            print(f"Rule    : {v.rule_id}")
            print(f"Message : {v.message or v.description}")
            print(f"Context : {v.context}")

            element = resolve_context(v.context, pdf)

            if element is None:
                print("    Element : <unresolved - extend `step` for this path>")
                print()
                continue

            print(f"    Element : {describe(element)}")
            apply_fix(v, element, pdf)
            print()

        pdf.save(OUTPUT_PATH)
        print(f"Patched copy written to {OUTPUT_PATH}")



if __name__ == "__main__":
    main()