#!/usr/bin/env python3
"""Static schema extractor for the 28 Cowork skills.

Reads every .py under work/SAG-1459/unpacked/*/*/scripts/, captures:
- workbook filename literals (anything ending .xlsx / .xlsm / .csv)
- sheet/tab string literals (sheet_name=, wb[...], book[...], etc.)
- column string literals (df['...'], df["..."], row[...], ws[...])
- read-vs-write classification by surrounding API call (to_excel, read_excel,
  load_workbook, ws[...].value =, append, save)

Output: work/SAG-1467/scan/extract.json

NO live data extraction. NO PII. Reads the source code only.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UNPACKED = ROOT / "SAG-1459" / "unpacked"
OUT_DIR = ROOT / "SAG-1467" / "scan"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# A short skill key for the package directory.
def skill_key(pkg_dir: Path) -> str:
    return pkg_dir.name  # e.g. "6-lowesime-warranty-audit_v4.50.0"


def short_skill_id(pkg_dir: Path) -> str:
    # leading number (e.g. "6", "6-5", "1-1")
    name = pkg_dir.name
    m = re.match(r"^(\d+(?:-\d+)?)", name)
    return m.group(1) if m else name


XLSX_LITERAL_RE = re.compile(r"""['"]([^'"\n{}]{1,200}?\.(?:xlsx|xlsm|csv))['"]""", re.IGNORECASE)

# Sheet/tab signatures: any `sheet_name=` kwarg, `wb[...]`, `book[...]`, `workbook[...]`,
# `ws.title = ...`, `worksheet =`, `pd.ExcelFile(...).parse(...)`, openpyxl create_sheet, etc.
SHEET_KW_RE = re.compile(r"""sheet_name\s*=\s*['"]([^'"\n]{1,120})['"]""")
SHEET_INDEX_RE = re.compile(r"""\b(?:wb|book|workbook|self\.\s*wb|self\.\s*book)\s*\[\s*['"]([^'"\n]{1,120})['"]\s*\]""")
WS_TITLE_RE = re.compile(r"""(?:\.\s*title\s*=\s*['"]([^'"\n]{1,120})['"]|create_sheet\s*\(\s*(?:title\s*=\s*)?['"]([^'"\n]{1,120})['"])""")
PARSE_SHEET_RE = re.compile(r"""\.parse\s*\(\s*['"]([^'"\n]{1,120})['"]""")

# Column references: df['X'], df["X"], df.loc[:, 'X'], ws['A1'] etc.
# We try to filter out cell coordinates like "A1", "B12", "AA3".
COL_BRACKET_RE = re.compile(r"""\b(?:df|frame|table|out|sheet|ws|sheetlet|rec|row|series|s|df_[a-zA-Z_]+|[a-z_]*df[a-z_]*)\s*\[\s*['"]([^'"\n]{1,80})['"]\s*\]""")
LOC_COL_RE = re.compile(r"""\.loc\s*\[\s*[^\]]*?,\s*['"]([^'"\n]{1,80})['"]\s*\]""")
LOC_LIST_RE = re.compile(r"""\[\s*['"]([^'"\n]{1,80})['"](?:\s*,\s*['"][^'"\n]{1,80}['"])+\s*\]""")  # bracket-list (potential header lists)
COLS_ASSIGN_RE = re.compile(r"""\.columns\s*=\s*\[([^\]]+)\]""")

# Header-write pattern (the most reliable column-name signal in openpyxl code):
# ws.cell(row=1, column=N, value="...")
HEADER_WRITE_RE = re.compile(
    r"""\.cell\s*\(\s*row\s*=\s*1\s*,\s*column\s*=\s*[^,)]+?,\s*value\s*=\s*['"]([^'"\n]{1,120})['"]"""
)
# Equality / contains comparison against a header string:
# if cell.value == "Service Provider"
# if header == "..."
# if "Service Provider" in ...
HEADER_EQ_RE = re.compile(
    r"""(?:cell\.value|header|hdr|col_name|column_name|h)\s*(?:==|in)\s*['"]([^'"\n]{1,120})['"]"""
)
# header-list constants (uppercase identifier = [...])
HEADER_LIST_RE = re.compile(
    r"""\b(?:HEADERS?|COLUMNS?|FIELDS?|COL_NAMES?|EXPECTED_HEADERS?|TAB\d?_HEADERS?|[A-Z_]*_(?:HEADERS?|COLS?|COLUMNS?))\s*=\s*\[([^\]]+)\]""",
    re.MULTILINE,
)

# Cell-coordinate filter — purely letters followed by digits like "A1", "AA12".
CELL_RE = re.compile(r"^[A-Z]{1,3}\d{1,4}$")

# Read/write API signatures we look for in context.
READ_HINTS = ("read_excel", "load_workbook", "ExcelFile", "read_csv", ".parse(")
WRITE_HINTS = ("to_excel", "DataFrame.to_excel", "ws.append", "ws[", "wb.save", "writer.save", ".save(", "ExcelWriter")


def categorize(filetext: str) -> tuple[set[str], set[str]]:
    reads: set[str] = set()
    writes: set[str] = set()
    for hint in READ_HINTS:
        if hint in filetext:
            reads.add(hint)
    for hint in WRITE_HINTS:
        if hint in filetext:
            writes.add(hint)
    return reads, writes


def scan_file(path: Path) -> dict:
    text = path.read_text(errors="replace")

    raw_workbooks = set(m.group(1) for m in XLSX_LITERAL_RE.finditer(text))
    workbooks: set[str] = set()
    for wb in raw_workbooks:
        wb = wb.strip()
        # Reject f-string fragments and prose
        if not wb or " " in wb.split("/")[-1]:
            continue
        if any(c in wb for c in ("{", "}", "\\")):
            continue
        # Keep only the basename for cross-skill matching, but also keep the
        # full path so we can show provenance.
        workbooks.add(wb)

    sheets: set[str] = set()
    for m in SHEET_KW_RE.finditer(text):
        sheets.add(m.group(1))
    for m in SHEET_INDEX_RE.finditer(text):
        sheets.add(m.group(1))
    for m in WS_TITLE_RE.finditer(text):
        sheets.add(m.group(1) or m.group(2))
    for m in PARSE_SHEET_RE.finditer(text):
        sheets.add(m.group(1))
    sheets = {s for s in sheets if s and len(s.strip()) > 0}

    raw_cols: set[str] = set()
    # High-confidence: header writes and header-string equality / list constants.
    high_conf_cols: set[str] = set()
    for m in HEADER_WRITE_RE.finditer(text):
        high_conf_cols.add(m.group(1))
    for m in HEADER_EQ_RE.finditer(text):
        high_conf_cols.add(m.group(1))
    for m in HEADER_LIST_RE.finditer(text):
        inside = m.group(1)
        for s in re.findall(r"""['"]([^'"\n]{1,120})['"]""", inside):
            high_conf_cols.add(s)
    # Pandas df[...] and df.loc[..., ...] (medium-confidence).
    for m in COL_BRACKET_RE.finditer(text):
        c = m.group(1)
        if CELL_RE.match(c):
            continue
        if c.strip() and len(c) <= 80:
            raw_cols.add(c)
    for m in LOC_COL_RE.finditer(text):
        raw_cols.add(m.group(1))
    for m in COLS_ASSIGN_RE.finditer(text):
        inside = m.group(1)
        for s in re.findall(r"""['"]([^'"\n]{1,80})['"]""", inside):
            raw_cols.add(s)

    # The high_conf set is always retained (these are header writes / direct
    # comparisons that we trust). For the medium-confidence df[...] set, we
    # still apply the snake_case filter.
    raw_cols = raw_cols - high_conf_cols  # avoid double-filtering on items already trusted

    # Filter the medium-confidence set: reject snake_case identifiers, but keep
    # mixed-case, Title Case, names with spaces, names with #, etc.
    cols: set[str] = set(high_conf_cols)
    snake = re.compile(r"^[a-z][a-z0-9_]+$")
    for c in raw_cols:
        c2 = c.strip()
        if not c2:
            continue
        if snake.match(c2):
            continue
        if c2.isdigit():
            continue
        cols.add(c2)

    reads, writes = categorize(text)

    # Per-sheet read/write classification: scan lines near each sheet literal.
    lines = text.splitlines()
    sheet_rw: dict[str, dict[str, bool]] = {}
    for sheet in sheets:
        rw = {"read": False, "write": False}
        for i, ln in enumerate(lines):
            if f'"{sheet}"' not in ln and f"'{sheet}'" not in ln:
                continue
            # Look at this line + 2 lines before/after
            window = "\n".join(lines[max(0, i - 2): i + 3])
            if any(h in window for h in ("read_excel", "load_workbook", "ExcelFile", ".parse(", "= wb[", "= book[")):
                rw["read"] = True
            if any(h in window for h in ("to_excel", "wb.save", "writer.save", ".save(",
                                          "create_sheet", "= ws[", "ws.cell", "ws.append",
                                          ".value =", " = ws[")):
                rw["write"] = True
        sheet_rw[sheet] = rw

    return {
        "path": str(path.relative_to(ROOT)),
        "workbooks": sorted(workbooks),
        "sheets": sorted(sheets),
        "sheet_rw": sheet_rw,
        "columns": sorted(cols),
        "read_hints": sorted(reads),
        "write_hints": sorted(writes),
        "loc": sum(1 for _ in text.splitlines()),
    }


def main():
    pkgs = sorted([p for p in UNPACKED.iterdir() if p.is_dir()])
    out = {"skills": {}}

    for pkg in pkgs:
        sid = short_skill_id(pkg)
        skill_key_name = pkg.name
        scripts_dir = next((p for p in pkg.iterdir() if p.is_dir()), None)
        per_skill = {
            "package": skill_key_name,
            "skill_id": sid,
            "files": [],
            "workbooks": set(),
            "sheets": set(),
            "columns": set(),
        }
        if scripts_dir is None:
            out["skills"][sid] = {
                **per_skill,
                "workbooks": [],
                "sheets": [],
                "columns": [],
            }
            continue
        for py in sorted(scripts_dir.rglob("*.py")):
            scan = scan_file(py)
            per_skill["files"].append(scan)
            per_skill["workbooks"].update(scan["workbooks"])
            per_skill["sheets"].update(scan["sheets"])
            per_skill["columns"].update(scan["columns"])
        per_skill["workbooks"] = sorted(per_skill["workbooks"])
        per_skill["sheets"] = sorted(per_skill["sheets"])
        per_skill["columns"] = sorted(per_skill["columns"])
        out["skills"][sid] = per_skill

    out_path = OUT_DIR / "extract.json"
    out_path.write_text(json.dumps(out, indent=2))
    print(f"Wrote {out_path}")
    print(f"Skills scanned: {len(out['skills'])}")

    # Build cross-skill pivots.
    workbook_to_skills: dict[str, set[str]] = defaultdict(set)
    sheet_to_skills_read: dict[str, set[str]] = defaultdict(set)
    sheet_to_skills_write: dict[str, set[str]] = defaultdict(set)
    sheet_to_skills_any: dict[str, set[str]] = defaultdict(set)
    column_to_skills: dict[str, set[str]] = defaultdict(set)

    for sid, sk in out["skills"].items():
        for wb in sk["workbooks"]:
            # Strip path prefixes — only the basename matters for cross-skill matching.
            base = wb.rsplit("/", 1)[-1]
            workbook_to_skills[base].add(sid)
        for sheet in sk["sheets"]:
            sheet_to_skills_any[sheet].add(sid)
        # Walk the per-file sheet_rw to populate read/write pivots.
        for f in sk["files"]:
            for sheet, rw in f.get("sheet_rw", {}).items():
                if rw.get("read"):
                    sheet_to_skills_read[sheet].add(sid)
                if rw.get("write"):
                    sheet_to_skills_write[sheet].add(sid)
        for col in sk["columns"]:
            column_to_skills[col].add(sid)

    pivots = {
        "workbook_to_skills": {k: sorted(v) for k, v in workbook_to_skills.items()},
        "sheet_read_by_skills": {k: sorted(v) for k, v in sheet_to_skills_read.items()},
        "sheet_write_by_skills": {k: sorted(v) for k, v in sheet_to_skills_write.items()},
        "sheet_referenced_by_skills": {k: sorted(v) for k, v in sheet_to_skills_any.items()},
        "column_to_skills": {k: sorted(v) for k, v in column_to_skills.items()},
    }
    (OUT_DIR / "pivots.json").write_text(json.dumps(pivots, indent=2))
    print("Wrote pivots.json")

    # Summary line
    print(f"Workbooks (unique basenames): {len(workbook_to_skills)}")
    print(f"Sheets (unique): {len(sheet_to_skills_any)}")
    print(f"Columns (unique, post-filter): {len(column_to_skills)}")


if __name__ == "__main__":
    main()
