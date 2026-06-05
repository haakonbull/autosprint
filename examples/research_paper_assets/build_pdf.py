"""Build ``results/paper.pdf`` from ``results/paper.md`` and ``results/sources.md``.

Reference build script for autosprint research projects (seeded into the
target repo under ``autosprint/examples/research_paper_assets/``). The loop
typically copies it to ``scripts/build_pdf.py`` and adapts it; invoke as
``uv run python scripts/build_pdf.py`` from the repo root.

The PDF reproduces the **LaTeXTemplates.com "Journal Article" v2.0** look kept
under ``autosprint/examples/research_paper_assets/LaTeXTemplates_journal-article_v2.0/``
(a reference input; a copy under ``results/`` is also honoured): a two-column
journal article with a full-width title block + abstract, numbered sections,
and a numeric biblatex bibliography.

Pipeline:
    1. Read ``paper.md``: split off the title, the lead abstract, and the body.
    2. Read ``sources.md`` (six-column ledger) and emit ``refs.bib``
       (one ``@misc`` per tag) for biblatex.
    3. Rewrite every ``[sources.md#<tag>]`` cite to pandoc ``[@<tag>]`` syntax.
    4. Convert the abstract and the body to LaTeX fragments via ``pandoc``
       (``--biblatex`` so cites become ``\\autocite``).
    5. Assemble a ``.tex`` that uses ``LTJournalArticle.cls`` and drops the
       fragments into the journal scaffold.
    6. Compile with ``tectonic`` (bundles every package and runs the
       bibliography pass itself), then copy the result to ``results/paper.pdf``.

Optional metadata lines near the top of ``paper.md`` (each on its own line):
    _Last revised: YYYY-MM-DD_      -> printed as the compile date
    _Subtitle: one-line subtitle_   -> printed under the title
    _Keywords: a; b; c_             -> printed under the abstract
A pandoc-style caption line directly after a pipe table (``Table: ...``)
becomes the caption of the full-width table float.

Toolchain (self-bootstrapping where possible):
    - pandoc          must be installed (``winget install JohnMacFarlane.Pandoc``
                      on Windows, ``apt install pandoc`` / ``brew install pandoc``
                      elsewhere).
    - tectonic        looked up on PATH / in ``.tools/``; downloaded from the
                      official GitHub release into ``.tools/`` when missing.
    - Libertinus OTFs looked up under ``.tools/fonts/``; downloaded from the
                      official GitHub release when missing.
``.tools/`` should be gitignored — it holds machine-local binaries only.

On any failure the script writes a diagnostic to stderr and exits non-zero,
which is what a pytest gate (e.g. ``tests/test_pdf_build.py``) can key off.
"""

from __future__ import annotations

import io
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path


def _repo_root() -> Path:
    """Nearest ancestor of this script that holds a ``.git`` directory.

    Works both from the seeded location (``autosprint/examples/research_paper_assets/``)
    and after the loop copies the script to ``scripts/``. Falls back to the
    current working directory (documented invocation is from the repo root).
    """
    for candidate in Path(__file__).resolve().parents:
        if (candidate / ".git").exists():
            return candidate
    return Path.cwd()


ROOT = _repo_root()
RESULTS = ROOT / "results"
BUILD = RESULTS / ".build"
TOOLS = ROOT / ".tools"
OUT_PDF = RESULTS / "paper.pdf"

# The journal template (reference input). First hit wins.
_TEMPLATE_CANDIDATES = [
    ROOT / "autosprint" / "examples" / "research_paper_assets" / "LaTeXTemplates_journal-article_v2.0",
    RESULTS / "LaTeXTemplates_journal-article_v2.0",
]

CITE_BRACKET_RE = re.compile(r"\[([^\[\]]*sources\.md#[^\[\]]*)\]")
CITE_TAG_RE = re.compile(r"sources\.md#([a-z0-9-]+)")
LAST_REVISED_RE = re.compile(r"^_Last revised:\s*(.+?)_\s*$", re.MULTILINE)
SUBTITLE_RE = re.compile(r"^_Subtitle:\s*(.+?)_\s*$", re.MULTILINE)
KEYWORDS_RE = re.compile(r"^_Keywords:\s*(.+?)_\s*$", re.MULTILINE)
TABLE_PLACEHOLDER = "QQTABLEZEROQQ"

# Pinned toolchain downloads (official upstream releases). Bump deliberately.
_LIBERTINUS_VERSION = "7.051"
_LIBERTINUS_URL = "https://github.com/alerque/libertinus/releases/download/" f"v{_LIBERTINUS_VERSION}/Libertinus-{_LIBERTINUS_VERSION}.zip"
_TECTONIC_VERSION = "0.15.0"

_FONT_FACES = [
    "LibertinusSerif-Regular.otf",
    "LibertinusSerif-Italic.otf",
    "LibertinusSerif-Bold.otf",
    "LibertinusSerif-BoldItalic.otf",
    "LibertinusSans-Regular.otf",
    "LibertinusSans-Italic.otf",
    "LibertinusSans-Bold.otf",
    "LibertinusMono-Regular.otf",
    "LibertinusMath-Regular.otf",
]


def _download(url: str, label: str) -> bytes:
    print(f"build_pdf: downloading {label} from {url} ...")
    try:
        with urllib.request.urlopen(url, timeout=120) as resp:
            return resp.read()
    except OSError as exc:
        raise SystemExit(f"build_pdf: download of {label} failed: {exc}") from exc


def _ensure_fonts() -> Path:
    """Return the directory holding the Libertinus OTFs, downloading the
    pinned release into ``.tools/fonts/`` on first use.

    Vendored files (not installed font names) keep the build reproducible —
    it depends on the OTFs under ``.tools/fonts/`` rather than on whatever
    happens to be installed on the host.
    """
    fonts_root = TOOLS / "fonts"
    matches = list(fonts_root.glob("**/LibertinusSerif-Regular.otf"))
    if matches:
        return matches[0].parent
    fonts_root.mkdir(parents=True, exist_ok=True)
    blob = _download(_LIBERTINUS_URL, f"Libertinus {_LIBERTINUS_VERSION} fonts")
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        for info in zf.infolist():
            if info.filename.lower().endswith(".otf"):
                target = fonts_root / Path(info.filename).name
                target.write_bytes(zf.read(info))
    matches = list(fonts_root.glob("**/LibertinusSerif-Regular.otf"))
    if not matches:
        raise SystemExit("build_pdf: Libertinus archive held no OTF files — layout changed upstream?")
    return matches[0].parent


def font_setup() -> str:
    """Copy the vendored Libertinus OTFs into the build dir and return the
    fontspec/unicode-math setup that loads them by bare filename.

    Faces are copied alongside ``paper.tex`` so XeTeX resolves them from its
    working directory (absolute Windows paths in fontspec's ``Path=`` trip
    XeTeX's bracket loader). Libertinus is a complete, OFL-licensed,
    publication-grade family (serif + sans + mono + matching maths).
    ``Ligatures=TeX`` keeps pandoc's --/--- dashes and ``/'' quotes.
    """
    otf_dir = _ensure_fonts()
    for face in _FONT_FACES:
        shutil.copyfile(otf_dir / face, BUILD / face)
    return "\\setmainfont{LibertinusSerif-Regular.otf}[" "ItalicFont=LibertinusSerif-Italic.otf, " "BoldFont=LibertinusSerif-Bold.otf, " "BoldItalicFont=LibertinusSerif-BoldItalic.otf, " "Ligatures=TeX, Numbers=OldStyle]\n" "\\setsansfont{LibertinusSans-Regular.otf}[" "ItalicFont=LibertinusSans-Italic.otf, " "BoldFont=LibertinusSans-Bold.otf, Ligatures=TeX]\n" "\\setmonofont{LibertinusMono-Regular.otf}[Scale=MatchLowercase]\n" "\\setmathfont{LibertinusMath-Regular.otf}"


# Remaining non-ASCII glyphs pandoc leaves verbatim that need explicit LaTeX
# spellings. Dashes are already handled by pandoc's latex writer.
_UNICODE_TEX = {
    "—": "---",
    "–": "--",
    "−": r"\(-\)",
    "§": r"\S{}",
    "¶": r"\P{}",
    "×": r"\(\times\)",
    "↔": r"\(\leftrightarrow\)",
    "→": r"\(\rightarrow\)",
    "≥": r"\(\geq\)",
    "≤": r"\(\leq\)",
    "±": r"\(\pm\)",
    "·": r"\textperiodcentered{}",
}


def latexify_symbols(s: str) -> str:
    for uni, tex in _UNICODE_TEX.items():
        s = s.replace(uni, tex)
    return s


# --------------------------------------------------------------------------- #
# Tool discovery
# --------------------------------------------------------------------------- #
def _find_pandoc() -> str:
    found = shutil.which("pandoc")
    if found:
        return found
    fallback = Path.home() / "AppData" / "Local" / "Pandoc" / "pandoc.exe"
    if fallback.exists():
        return str(fallback)
    raise SystemExit("build_pdf: pandoc not found. Install it with " "`winget install --id JohnMacFarlane.Pandoc` (Windows), " "`apt install pandoc` (Linux), or `brew install pandoc` (macOS).")


def _tectonic_release_asset() -> tuple[str, str]:
    """(archive filename, member name) of the pinned tectonic release for this platform."""
    if sys.platform == "win32":
        return f"tectonic-{_TECTONIC_VERSION}-x86_64-pc-windows-msvc.zip", "tectonic.exe"
    if sys.platform == "darwin":
        import platform

        arch = "aarch64" if platform.machine() == "arm64" else "x86_64"
        return f"tectonic-{_TECTONIC_VERSION}-{arch}-apple-darwin.tar.gz", "tectonic"
    return f"tectonic-{_TECTONIC_VERSION}-x86_64-unknown-linux-musl.tar.gz", "tectonic"


def _find_tectonic() -> str:
    exe_name = "tectonic.exe" if sys.platform == "win32" else "tectonic"
    local = TOOLS / exe_name
    if local.exists():
        return str(local)
    found = shutil.which("tectonic")
    if found:
        return found
    # Self-bootstrap: fetch the pinned release binary into .tools/.
    asset, member = _tectonic_release_asset()
    url = "https://github.com/tectonic-typesetting/tectonic/releases/download/" f"tectonic%40{_TECTONIC_VERSION}/{asset}"
    blob = _download(url, f"tectonic {_TECTONIC_VERSION}")
    TOOLS.mkdir(parents=True, exist_ok=True)
    if asset.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            local.write_bytes(zf.read(member))
    else:
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
            extracted = tf.extractfile(member)
            if extracted is None:
                raise SystemExit(f"build_pdf: `{member}` not found in {asset}")
            local.write_bytes(extracted.read())
        local.chmod(0o755)
    return str(local)


def _find_template_cls() -> Path:
    for candidate in _TEMPLATE_CANDIDATES:
        cls = candidate / "LTJournalArticle.cls"
        if cls.exists():
            return cls
    locations = "\n  ".join(str(c) for c in _TEMPLATE_CANDIDATES)
    raise SystemExit(f"build_pdf: LTJournalArticle.cls not found. Looked in:\n  {locations}")


def _source(name: str) -> Path:
    candidate = RESULTS / name
    if candidate.exists():
        return candidate
    raise SystemExit(f"build_pdf: {name} not found under results/")


# --------------------------------------------------------------------------- #
# paper.md parsing
# --------------------------------------------------------------------------- #
def split_paper(md: str) -> tuple[str, str, str, str, str, str]:
    """Return (title, abstract_md, body_md, revised, subtitle, keywords).

    Title is the single ``# `` heading. The abstract is a dedicated
    ``## Abstract`` section if present (lifted out of the body so it renders in
    the full-width title block), else the lead text between title and the first
    ``## ``. Every other ``## `` section forms the body. ``revised`` /
    ``subtitle`` / ``keywords`` come from the optional italic metadata lines
    (empty string when absent).
    """
    revised = subtitle = keywords = ""
    for regex in (LAST_REVISED_RE, SUBTITLE_RE, KEYWORDS_RE):
        m = regex.search(md)
        if m:
            value = m.group(1).strip()
            if regex is LAST_REVISED_RE:
                revised = value
            elif regex is SUBTITLE_RE:
                subtitle = value
            else:
                keywords = value
            md = regex.sub("", md, count=1)
    lines = md.splitlines()

    title = ""
    title_idx = None
    for i, line in enumerate(lines):
        if line.startswith("# ") and not line.startswith("## "):
            title = line[2:].strip()
            title_idx = i
            break
    if title_idx is None:
        raise SystemExit("build_pdf: paper.md has no '# ' title heading")

    rest = lines[title_idx + 1 :]
    heads = [i for i, ln in enumerate(rest) if ln.startswith("## ")]
    if not heads:
        raise SystemExit("build_pdf: paper.md has no '## ' section headings")

    lead = "\n".join(rest[: heads[0]]).strip()
    abstract_md = ""
    body_parts: list[str] = []
    for n, h in enumerate(heads):
        seg_end = heads[n + 1] if n + 1 < len(heads) else len(rest)
        heading = rest[h][3:].strip()
        block = "\n".join(rest[h:seg_end]).strip()
        if heading.lower() == "abstract":
            abstract_md = "\n".join(rest[h + 1 : seg_end]).strip()
        else:
            body_parts.append(block)

    if not abstract_md:
        abstract_md = lead
    body_md = "\n\n".join(body_parts).strip()
    if not body_md:
        raise SystemExit("build_pdf: paper.md has no body sections beyond the abstract")
    return title, abstract_md, body_md, revised, subtitle, keywords


def rewrite_cites(md: str) -> str:
    """Turn any bracket holding sources.md#tag refs into pandoc [@a; @b] form.

    Handles single cites, comma/space-separated lists inside one bracket, and
    adjacent bracket runs.
    """

    def repl(m: re.Match[str]) -> str:
        tags = CITE_TAG_RE.findall(m.group(1))
        if not tags:
            return m.group(0)
        return "[" + "; ".join(f"@{t}" for t in tags) + "]"

    out = CITE_BRACKET_RE.sub(repl, md)
    # Merge directly-adjacent citation brackets: [@a][@b] -> [@a; @b]
    return re.sub(r"\]\[@", "; @", out)


def extract_first_table(md: str) -> tuple[str, list[list[str]] | None, str]:
    """Pull the first pipe-table out, leaving a placeholder paragraph behind.

    Returns (md_with_placeholder, rows, caption) where rows is header + data
    rows (the `---` separator row dropped), or (md, None, "") if there is no
    table. A two-column journal layout can't hold a wide table in one column,
    so the table is rendered separately as a full-width float. A pandoc-style
    ``Table: ...`` line directly after the table becomes the float's caption.
    """
    lines = md.splitlines()
    start = end = None
    for i, line in enumerate(lines):
        if line.lstrip().startswith("|") and line.rstrip().endswith("|"):
            if start is None:
                start = i
            end = i
        elif start is not None:
            break
    if start is None or end is None or end - start < 2:
        return md, None, ""

    rows: list[list[str]] = []
    sep_pat = re.compile(r":?-{3,}:?")
    for line in lines[start : end + 1]:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells and all(sep_pat.fullmatch(c.replace(" ", "")) or c == "" for c in cells):
            continue  # separator row
        rows.append(cells)

    caption = ""
    after = end + 1
    while after < len(lines) and not lines[after].strip():
        after += 1
    if after < len(lines):
        m = re.match(r"^(?:Table|:)[:]?\s+(.+)$", lines[after].strip())
        if m:
            caption = m.group(1).strip()
            lines = lines[:after] + lines[after + 1 :]
    new_lines = lines[:start] + ["", TABLE_PLACEHOLDER, ""] + lines[end + 1 :]
    return "\n".join(new_lines), rows, caption


def build_table_tex(rows: list[list[str]], caption: str) -> str:
    """Render parsed rows as a full-width tabularx float matching the template."""
    ncol = max(len(r) for r in rows)
    colspec = "@{}" + " ".join([r">{\raggedright\arraybackslash}X"] * ncol) + "@{}"

    def cell(text: str) -> str:
        return latexify_symbols(_tex_escape(text))

    header = rows[0] + [""] * (ncol - len(rows[0]))
    header_tex = " & ".join(rf"\textbf{{{cell(c)}}}" for c in header) + r" \\"
    body_rows = []
    for r in rows[1:]:
        r = r + [""] * (ncol - len(r))
        body_rows.append(" & ".join(cell(c) for c in r) + r" \\")

    caption_line = rf"\caption{{{cell(caption)}}}" if caption else ""
    return "\n".join(
        line
        for line in [
            r"\begin{table*}[t]",
            r"\centering\footnotesize",
            caption_line,
            rf"\begin{{tabularx}}{{\textwidth}}{{{colspec}}}",
            r"\toprule",
            header_tex,
            r"\midrule",
            *body_rows,
            r"\bottomrule",
            r"\end{tabularx}",
            r"\end{table*}",
        ]
        if line
    )


# --------------------------------------------------------------------------- #
# sources.md -> refs.bib
# --------------------------------------------------------------------------- #
_TEX_SPECIALS = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def _tex_escape(s: str) -> str:
    out = []
    for ch in s:
        out.append(_TEX_SPECIALS.get(ch, ch))
    return "".join(out)


def parse_sources(text: str) -> list[dict[str, str]]:
    rows: list[list[str]] = []
    in_table = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            rows.append([c.strip() for c in stripped.strip("|").split("|")])
            in_table = True
        elif in_table:
            break
    if not rows:
        return []
    header = [c.lower() for c in rows[0]]
    needed = ("identifier", "author", "date", "tag", "url")
    if not all(col in header for col in needed):
        return []
    idx = {col: header.index(col) for col in header}
    quality_key = "quality-rating" if "quality-rating" in header else None
    sep_pat = re.compile(r":?-{3,}:?")
    out: list[dict[str, str]] = []
    for row in rows[1:]:
        if row and all(sep_pat.fullmatch(c) for c in row):
            continue
        if len(row) < len(header):
            continue
        out.append(
            {
                "identifier": row[idx["identifier"]],
                "author": row[idx["author"]],
                "date": row[idx["date"]],
                "tag": row[idx["tag"]],
                "url": row[idx["url"]],
                "quality": row[idx[quality_key]] if quality_key else "",
            }
        )
    return out


def build_bib(sources: list[dict[str, str]]) -> str:
    entries = []
    for s in sources:
        if not s["tag"]:
            continue
        fields = [
            f"  author = {{{_tex_escape(s['author'])}}}",
            f"  title  = {{{_tex_escape(s['identifier'])}}}",
            f"  date   = {{{s['date']}}}",
        ]
        if s["url"]:
            fields.append(f"  url    = {{{s['url']}}}")
        if s["quality"]:
            fields.append(f"  note   = {{{_tex_escape(s['quality'])}}}")
        entries.append("@misc{" + s["tag"] + ",\n" + ",\n".join(fields) + ",\n}")
    return "\n\n".join(entries) + "\n"


# --------------------------------------------------------------------------- #
# pandoc + assembly
# --------------------------------------------------------------------------- #
def pandoc_fragment(pandoc: str, md_text: str, shift_headings: bool = False) -> str:
    """Convert a markdown fragment to a LaTeX fragment (cites -> \\autocite).

    ``shift_headings`` promotes ``##`` to ``\\section`` (and ``###`` to
    ``\\subsection``) so body sections get top-level journal numbering.
    """
    args = [pandoc, "--from=markdown", "--to=latex", "--biblatex", "--wrap=preserve"]
    if shift_headings:
        args.append("--shift-heading-level-by=-1")
    proc = subprocess.run(
        args,
        input=md_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"pandoc failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def assemble_tex(
    title: str,
    abstract_tex: str,
    body_tex: str,
    footer: str,
    revised: str,
    subtitle: str,
    keywords: str,
) -> str:
    running = title if len(title) <= 60 else title[:57].rstrip() + "..."
    title_tex = latexify_symbols(_tex_escape(title))
    running_tex = latexify_symbols(_tex_escape(running))
    footer_tex = latexify_symbols(_tex_escape(footer))
    subtitle_tex = latexify_symbols(_tex_escape(subtitle)) if subtitle else ""
    compiled = _tex_escape(revised) if revised else ""
    compiled_line = rf" \textbf{{Compiled:}} {compiled}." if compiled else ""
    keywords_tex = r"\par\smallskip\noindent{\normalfont\small\textbf{Keywords:} " + latexify_symbols(_tex_escape(keywords)) + "}" if keywords else ""
    return rf"""\documentclass[a4paper,10pt]{{LTJournalArticle}}

\usepackage{{tabularx}}

\addbibresource{{refs.bib}}

\runninghead{{{running_tex}}}
\footertext{{{footer_tex}}}
\setcounter{{page}}{{1}}

\title{{{title_tex}}}
\author{{%
  The \texttt{{autosprint}} research loop\thanks{{Generated and maintained
  autonomously by the \texttt{{autosprint}} PIT loop from version-controlled
  Markdown sources; this synthesis carries no individual human author.{compiled_line}}}
}}
\date{{\footnotesize {subtitle_tex}}}

\renewcommand{{\maketitlehookd}}{{%
\begin{{abstract}}
\noindent {abstract_tex}
{keywords_tex}
\end{{abstract}}
}}

% pandoc helper macros (kept here since we use a hand-written scaffold)
\providecommand{{\tightlist}}{{\setlength{{\itemsep}}{{0pt}}\setlength{{\parskip}}{{0pt}}}}
\providecommand{{\passthrough}}[1]{{#1}}

% Inline literals (file paths, identifiers) read as prose, not code: the source
% backticks them heavily and a monospace face looks out of place in a journal
% article. Render them in the body serif instead, and match link style to text.
\renewcommand{{\texttt}}[1]{{\textrm{{#1}}}}
\urlstyle{{same}}

\begin{{document}}

\maketitle

{body_tex}

\printbibliography

\end{{document}}
"""


def run_tectonic(tectonic: str, tex_path: Path) -> None:
    proc = subprocess.run(
        [tectonic, "--keep-logs", "--chatter", "minimal", tex_path.name],
        cwd=str(tex_path.parent),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout)[-2000:]
        raise RuntimeError(f"tectonic failed (exit {proc.returncode}):\n{tail}")


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main() -> int:
    pandoc = _find_pandoc()
    tectonic = _find_tectonic()
    cls = _find_template_cls()

    paper_md = _source("paper.md")
    sources_md = _source("sources.md")

    title, abstract_md, body_md, revised, subtitle, keywords = split_paper(paper_md.read_text(encoding="utf-8"))
    body_md, table_rows, table_caption = extract_first_table(body_md)
    abstract_md = rewrite_cites(abstract_md)
    body_md = rewrite_cites(body_md)

    sources = parse_sources(sources_md.read_text(encoding="utf-8"))

    BUILD.mkdir(parents=True, exist_ok=True)
    (BUILD / "refs.bib").write_text(build_bib(sources), encoding="utf-8")
    # Copy the template class with two build-time patches (upstream file left
    # untouched): (1) biblatex backend biber -> bibtex, since tectonic bundles
    # bibtex but not biber and the numeric output is identical; (2) swap the
    # Type1 Palatino (mathpazo) for a full OpenType font system via fontspec +
    # unicode-math, for publication-grade typography (true small caps, proper
    # kerning/ligatures, matching maths).
    cls_text = cls.read_text(encoding="utf-8").replace("backend=biber", "backend=bibtex")
    cls_text = cls_text.replace("\\usepackage[utf8]{inputenc}", "\\usepackage{fontspec}")
    cls_text = cls_text.replace("\\usepackage[T1]{fontenc}", "\\usepackage{unicode-math}")
    cls_text = cls_text.replace("\\usepackage[sc]{mathpazo}", font_setup())
    (BUILD / cls.name).write_text(cls_text, encoding="utf-8")

    abstract_tex = latexify_symbols(pandoc_fragment(pandoc, abstract_md)) if abstract_md else ""
    body_tex = latexify_symbols(pandoc_fragment(pandoc, body_md, shift_headings=True))
    if table_rows:
        body_tex = body_tex.replace(TABLE_PLACEHOLDER, build_table_tex(table_rows, table_caption))

    year = revised[:4] if revised[:4].isdigit() else ""
    running = title if len(title) <= 60 else title[:57].rstrip() + "..."
    footer = f"{running} · Working Paper{(' ' + year) if year else ''}"
    tex = assemble_tex(title, abstract_tex, body_tex, footer, revised, subtitle, keywords)
    tex_path = BUILD / "paper.tex"
    tex_path.write_text(tex, encoding="utf-8")

    try:
        run_tectonic(tectonic, tex_path)
    except RuntimeError as exc:
        print(f"build_pdf: {exc}", file=sys.stderr)
        return 1

    built = BUILD / "paper.pdf"
    if not built.exists():
        print("build_pdf: tectonic reported success but produced no PDF", file=sys.stderr)
        return 1
    shutil.copyfile(built, OUT_PDF)

    size_kb = OUT_PDF.stat().st_size / 1024
    print(f"Built {OUT_PDF.relative_to(ROOT)} ({size_kb:.0f} KB, " f"{len(sources)} sources) from {paper_md.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
