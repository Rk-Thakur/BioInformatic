"""Helpers shared by the Stage 1a cleaning modules: TSV I/O, checksums and the
quality report that counts every row each rule keeps, changes or drops."""

import csv
import hashlib
from dataclasses import dataclass, field
from pathlib import Path

CHROM_ORDER = [str(i) for i in range(1, 23)] + ["X", "Y", "MT"]


class CleaningError(Exception):
    """Raised when an input breaks an assumption the pipeline relies on (C8)."""


def chrom_rank(chrom):
    return CHROM_ORDER.index(chrom) if chrom in CHROM_ORDER else len(CHROM_ORDER)


def sha256(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def read_tsv(path):
    """Read a TSV with optional leading '#' comment lines."""
    with open(path, newline="") as fh:
        lines = [line for line in fh if not line.startswith("#")]
    return list(csv.DictReader(lines, delimiter="\t"))


def write_tsv(path, fieldnames, rows, comments=()):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as fh:
        for c in comments:
            fh.write(f"# {c}\n")
        w = csv.DictWriter(fh, fieldnames, delimiter="\t", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


@dataclass
class Section:
    title: str
    rows: list = field(default_factory=list)  # (rule, description, count)
    notes: list = field(default_factory=list)
    tables: list = field(default_factory=list)  # (caption, header, rows)

    def add(self, rule, description, count):
        self.rows.append((rule, description, count))

    def note(self, text):
        self.notes.append(text)

    def table(self, caption, header, rows):
        self.tables.append((caption, header, rows))


class Report:
    def __init__(self, title):
        self.title = title
        self.sections = []
        self.preamble = []

    def section(self, title):
        s = Section(title)
        self.sections.append(s)
        return s

    def to_markdown(self):
        out = [f"# {self.title}", "", *self.preamble, ""]
        for s in self.sections:
            out += [f"## {s.title}", ""]
            if s.rows:
                out += ["| Rule | What | Rows |", "|---|---|---:|"]
                out += [f"| {r} | {d} | {n:,} |" for r, d, n in s.rows]
                out.append("")
            for caption, header, rows in s.tables:
                out += [f"**{caption}**", "", "| " + " | ".join(header) + " |"]
                out.append("|" + "---|" * len(header))
                out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
                out.append("")
            out += [f"- {n}" for n in s.notes]
            if s.notes:
                out.append("")
        return "\n".join(out)

    def write(self, path):
        Path(path).write_text(self.to_markdown())
