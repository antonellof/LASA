#!/usr/bin/env python3
"""Local technical preflight, not a certificate of arXiv or journal acceptance."""
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess
import zipfile

HERE = Path(__file__).resolve().parent
REPO = "https://github.com/antonellof/lineage-attack-surface-analyzer"


def run(command):
    if not shutil.which(command[0]):
        raise RuntimeError(f"missing validation dependency: {command[0]}")
    return subprocess.check_output(command, text=True)


def validate():
    bundle = HERE / "build/arxiv"
    pdf, tex = HERE / "PAPER.pdf", bundle / "paper.tex"
    source = tex.read_text()
    standalone = HERE / "build/standalone-check"
    standalone.mkdir(parents=True, exist_ok=True)
    if not shutil.which("tectonic"):
        raise RuntimeError("Tectonic is required for the independent source-bundle build")
    subprocess.run(["tectonic", "--outdir", str(standalone), str(tex)],
                   cwd=bundle, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    built_text = run(["pdftotext", str(standalone / "paper.pdf"), "-"])
    reading_text = run(["pdftotext", str(pdf), "-"])
    ordered_text = run(["pdftotext", "-layout", str(pdf), "-"])
    text = run(["pdftotext", "-f", "1", "-l", "1", "-layout", str(pdf), "-"])
    header = " ".join(text.split())
    date = re.search(r"^Date:\s*(.+)$", (HERE / "PAPER.md").read_text(), re.MULTILINE).group(1).strip()
    values = ["Antonello Fratepietro", "Code and data: " + REPO, "antonello.f@gmail.com", date]
    positions = [header.find(v) for v in values]
    checks = {"source_bundle_compiles_independently": True,
              "bundle_pdf_matches_reading_copy_text": built_text.split() == reading_text.split(),
              "author_url_email_date_visible_in_order": all(p >= 0 for p in positions) and positions == sorted(positions),
              "repository_printed_without_hyperlink": r"\nolinkurl{" + REPO + "}" in source,
              "no_unresolved_citations": "[@" not in source and "citation not found" not in source.lower(),
              "no_conference_acceptance_claim": not any(s in source.lower() for s in
                  ("published as a conference paper", "under review as a conference paper"))}
    fonts = run(["pdffonts", str(pdf)]).splitlines()
    # Rightmost columns are emb, sub, uni, object number, generation number.
    checks["all_fonts_embedded"] = len(fonts) > 2 and all(
        len(line.split()) >= 6 and line.split()[-5] == "yes"
        for line in fonts[2:] if line.strip()
    )
    checks["no_type3_fonts"] = not any("Type 3" in line for line in fonts[2:])
    info = run(["pdfinfo", str(pdf)])
    checks["no_javascript"] = bool(re.search(r"JavaScript:\s+no", info))
    figures = re.findall(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}", source)
    checks["all_figures_bundled"] = bool(figures) and all((bundle / f).is_file() for f in figures)
    checks["no_absolute_figure_paths"] = all(not Path(f).is_absolute() for f in figures)
    inputs = re.findall(r"\\(?:input|include)\{([^}]+)\}", source)
    checks["no_missing_local_inputs"] = all((bundle / f).exists() or (bundle / (f + ".tex")).exists() for f in inputs)
    checks["inline_bibliography_present"] = r"\begin{CSLReferences}" in source
    checks["figures_before_bibliography"] = (
        "Figure 3:" in ordered_text and "References" in ordered_text
        and ordered_text.index("Figure 3:") < ordered_text.index("References")
    )
    payload = {"checks": checks, "passed": all(checks.values()),
               "compile_test": "Tectonic/XeTeX reading copy and independent source-bundle compilation",
               "not_certified": ["arXiv server compilation", "venue formatting compliance", "scientific acceptance"],
               "guidance": ["https://info.arxiv.org/help/submit_tex.html", "https://info.arxiv.org/help/submit_pdf.html"]}
    (HERE / "build/validation.json").write_text(json.dumps(payload, indent=2) + "\n")
    if not payload["passed"]:
        raise RuntimeError("preflight failed: " + ", ".join(k for k, v in checks.items() if not v))
    # Only compilation inputs enter the upload archive. CSL/XML and reading PDFs stay outside it.
    with zipfile.ZipFile(HERE / "build/submission.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(tex, "paper.tex")
        for f in figures:
            archive.write(bundle / f, f)
    print(json.dumps(payload, indent=2))
    print("Source archive: paper/build/submission.zip")
    return payload


if __name__ == "__main__":
    validate()
