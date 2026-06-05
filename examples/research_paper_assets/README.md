# Research-paper assets

Reference inputs for research-type destinations that require a
publication-quality PDF (e.g. `destination_research_ai_bubble.example.md`).
Seeded into the target repo at `autosprint/examples/research_paper_assets/`
by `autosprint init`.

## Contents

- **`LaTeXTemplates_journal-article_v2.0/`** — the
  [LaTeXTemplates.com "Journal Article" v2.0](https://www.latextemplates.com/template/journal-article)
  template (CC BY-NC-SA 4.0, by Vel). The `.cls` defines the look the PDF
  build reproduces: A4 two-column journal article, full-width title block and
  abstract, numbered sections, numeric bibliography. The sample figures are
  omitted (research destinations are text + tables only).

- **`build_pdf.py`** — a proven reference build script:
  `results/paper.md` + `results/sources.md` → `results/paper.pdf` via
  **pandoc → LaTeX → tectonic**, typeset in the Libertinus OpenType family.
  The loop typically copies it to `scripts/build_pdf.py` and adapts it to the
  project (it is a starting point, not a contract). See its module docstring
  for the pipeline, the optional `_Subtitle:_` / `_Keywords:_` metadata lines
  in `paper.md`, and the table-caption convention.

## Toolchain

- **pandoc** must be installed by the user
  (`winget install --id JohnMacFarlane.Pandoc` on Windows).
- **tectonic** and the **Libertinus fonts** self-bootstrap: the script
  downloads the pinned official releases into `.tools/` on first run.
  Add `.tools/` to the target repo's `.gitignore` — it holds machine-local
  binaries only.
