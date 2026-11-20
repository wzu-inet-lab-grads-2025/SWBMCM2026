#!/usr/bin/env python3
"""Extract/render authoritative English pages 1-3, without altering the PDF."""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "runs/b/q1_independent"
OUT.mkdir(parents=True, exist_ok=True)
if (OUT / "_deps").exists():
    sys.path.insert(0, str(OUT / "_deps"))

import pypdfium2 as pdfium
from pypdf import PdfReader

SOURCE = ROOT / "problem/B/2026_“ShuWei Cup”B_Problem.pdf"
reader = PdfReader(SOURCE)
assert len(reader.pages) == 5
texts = []
for i in range(3):
    # Some math font mappings in pypdf are isolated UTF-16 surrogates.
    # Preserve those as explicit escapes, not guessed mathematical symbols.
    text = reader.pages[i].extract_text(extraction_mode="layout")
    text = text.encode("utf-8", errors="backslashreplace").decode("utf-8")
    texts.append(f"\n--- ENGLISH ORIGINAL PAGE {i+1} ---\n\n" + text)
(OUT / "english_pages_1_3.txt").write_text("".join(texts))

doc = pdfium.PdfDocument(str(SOURCE))
for i in range(3):
    page = doc[i]
    bitmap = page.render(scale=1.8)
    bitmap.to_pil().save(OUT / f"english_page_{i+1}.png")
    bitmap.close()
    page.close()
doc.close()

manifest = {
    "source": str(SOURCE.relative_to(ROOT)),
    "sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
    "total_pages": len(reader.pages),
    "authoritative_pages_used": [1, 2, 3],
    "notes": "Original rendering is authoritative for math symbols not recoverable from text extraction.",
}
(OUT / "source_audit.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False)+"\n")
print(json.dumps(manifest, indent=2, ensure_ascii=False))
