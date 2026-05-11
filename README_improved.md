# PDF accessibility fixer demo

This starter shows one small loop:

**veraPDF finds PDF/UA violations → Python resolves the failing object → pikepdf patches the PDF.**

The demo PDF is intentionally broken. It contains accessibility issues such as:

- missing document language
- missing document title metadata
- a figure with no `/Alt`
- a link annotation with no `/Contents`

The goal is not to fully repair every PDF/UA issue yet. The goal is to show how detection, object resolution, and simple correction fit together.

## Files

| File | Purpose |
|---|---|
| `make_broken_pdf.py` | Builds a tiny intentionally broken PDF |
| `broken.pdf` | Example input PDF |
| `accessibility_fix_demo.py` | Runs veraPDF, resolves failed checks, applies simple fixes |
| `output-fixed.pdf` | Example patched output |
| `requirements.txt` | Python dependency list |

## Install

You need:

- Python 3.9+
- Java 11+
- `verapdf` CLI on your `PATH`
- Python dependencies:

```bash
pip install -r requirements.txt
```

Check that veraPDF is available:

```bash
verapdf --version
```

## Run the demo

Generate the broken PDF:

```bash
python make_broken_pdf.py broken.pdf
```

Run the fixer:

```bash
python accessibility_fix_demo.py
```

The script will:

1. run veraPDF on `broken.pdf`
2. parse the XML report
3. print each failed rule
4. resolve the veraPDF context path to a pikepdf object
5. apply any matching fixer
6. write `output-fixed.pdf`

Example output shape:

```text
Running veraPDF on broken.pdf ...

Rule    : 7.2-1
Message : Natural language is not defined
Context : root/document[0]/catalog[0]

    Element : Pdf (1 pages)
    Fix : set catalog /Lang = 'en' (placeholder)

Rule    : 7.18.1-2
Message : Annotation does not have Contents
Context : root/document[0]/pages[0]/annots[0]

    Element : /Annot keys=['/Type', '/Subtype', '/Rect', ...]
    Fix : added placeholder /Contents on annotation

Patched copy written to output-fixed.pdf
```

## What changed?

The fixer adds placeholder values such as:

```python
pdf.Root["/Lang"] = "en"
pdf.docinfo["/Title"] = "TODO: real document title"
prefs["/DisplayDocTitle"] = True
element["/Contents"] = "TODO: describe this annotation"
element["/Alt"] = "TODO: describe this figure"
```

These are not final accessibility repairs. They are placeholders showing where a real tool would insert human-authored or LLM-assisted text.

## The key idea

veraPDF reports violations using paths like this:

```text
root/document[0]/pages[0]/annots[0](9 0 obj PDLinkAnnot)
root/document[0]/StructTreeRoot[0]/K[0](7 0 obj SEDocument)/K[2](14 0 obj SEFigure)
```

pikepdf does not understand these paths automatically.

So the demo has a small resolver:

```python
element = resolve_context(v.context, pdf)
```

The resolver walks the veraPDF path one segment at a time. The translation logic lives in `step()`.

When veraPDF says:

```text
pages[0]/annots[0]
```

the resolver returns the actual annotation dictionary in pikepdf.

Then the fixer can mutate it:

```python
element["/Contents"] = "TODO: describe this annotation"
```

## Add your own fixer

A fixer is just a function registered by rule id:

```python
@fixer("7.18.1-2")
def fix_annotation_contents(element, pdf):
    if isinstance(element, Dictionary) and not element.get("/Contents"):
        element["/Contents"] = "TODO: describe this annotation"
```

To support a new rule:

1. run veraPDF
2. copy the failed rule id and context path
3. make sure `resolve_context()` reaches the object
4. add a new `@fixer(...)`
5. save and re-run veraPDF

## When resolution fails

You may see:

```text
Element : <unresolved - extend `step` for this path>
```

That means veraPDF used a path segment that this starter does not know how to translate yet.

Add a case to `step()`.

For example, if a future rule points to an image XObject, you might add support for segments like:

```text
xobject
image
form
```

## Important note on rule IDs

veraPDF rule ids can vary between validation-profile versions.

The rule message and context path are often more stable than the exact test number. If a fixer stops firing after a veraPDF upgrade, check the new XML report and update the `@fixer("...")` rule id.

## Suggested script improvement

The current demo script uses hardcoded paths. To make this README command-line friendly, replace the hardcoded configuration with `argparse`:

```python
parser = argparse.ArgumentParser()
parser.add_argument("pdf_path", nargs="?", default="broken.pdf")
parser.add_argument("-o", "--output", default="output-fixed.pdf")
parser.add_argument("--flavour", default="ua1")
args = parser.parse_args()

PDF_PATH = args.pdf_path
OUTPUT_PATH = args.output
FLAVOUR = args.flavour
```

Then students can run:

```bash
python accessibility_fix_demo.py broken.pdf -o output-fixed.pdf
```
