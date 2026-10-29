"""Stage 1a cleaning rules C1-C11, on small hand-made inputs.

The slow test at the bottom runs the full pipeline on the real files in data/raw/.
"""

import gzip
from pathlib import Path

import pytest

from scripts.clean import clinvar, erepo, genes, run_all
from scripts.clean.common import CleaningError, Report, read_tsv

ROOT = Path(__file__).resolve().parent.parent
CURATED = ROOT / "data" / "curated"
RAW = ROOT / "data" / "raw"

HEADER = "##fileformat=VCFv4.1\n##fileDate={date}\n##reference={ref}\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"


def vcf_line(chrom, pos, vid, ref, alt, info):
    return f"{chrom}\t{pos}\t{vid}\t{ref}\t{alt}\t.\t.\t{info}\n"


def write_vcf(path, lines, date="2023-01-07", ref="GRCh38"):
    with gzip.open(path, "wt") as fh:
        fh.write(HEADER.format(date=date, ref=ref))
        fh.writelines(lines)
    return path


@pytest.fixture(scope="module")
def panel():
    return genes.load_genes(CURATED / "panel_genes.tsv")


def clean(tmp_path, lines, panel, **kw):
    section = Report("t").section("s")
    date, rows = clinvar.clean_release(
        write_vcf(tmp_path / "c.vcf.gz", lines, **kw), panel, section
    )
    counts = {desc: n for _, desc, n in section.rows}
    return date, rows, counts


# ---------------------------------------------------------------- C11 genes


def test_panel_genes_complete_and_non_overlapping(panel):
    assert set(panel) == {"GCK", "HNF1A", "HNF4A", "HNF1B", "KCNJ11", "ABCC8", "INS"}
    assert panel["ABCC8"].start > panel["KCNJ11"].end  # neighbours on chr11, no overlap


def test_overlapping_genes_rejected(tmp_path):
    p = tmp_path / "g.tsv"
    rows = ["gene\tncbi_gene_id\tchrom\tstart\tend\tstrand"]
    for g in ("GCK", "HNF1A", "HNF4A", "HNF1B", "INS"):
        rows.append(f"{g}\t1\t{len(rows)}\t1\t10\t+")
    rows += ["KCNJ11\t1\t11\t100\t200\t-", "ABCC8\t1\t11\t150\t300\t-"]
    p.write_text("\n".join(rows) + "\n")
    with pytest.raises(CleaningError, match="overlap"):
        genes.load_genes(p)


def test_bed_is_zero_based(panel, tmp_path):
    genes.write_bed(panel, tmp_path / "g.bed")
    first = (tmp_path / "g.bed").read_text().splitlines()[0].split("\t")
    gck = panel["GCK"]
    assert first == ["7", str(gck.start - 1), str(gck.end), "GCK", "0", "-"]


# ---------------------------------------------------------------- C6 gene assignment


def test_assign_gene(panel):
    in_abcc8 = panel["ABCC8"].start + 10
    in_kcnj11 = panel["KCNJ11"].start + 10
    assert genes.assign_gene("11", in_abcc8, ["ABCC8"], panel) == ("ABCC8", "single")
    # non-panel neighbours are ignored
    assert genes.assign_gene("11", 2160000, ["INS-IGF2", "INS"], panel) == ("INS", "single")
    # two panel genes listed: position decides
    assert genes.assign_gene("11", in_kcnj11, ["ABCC8", "KCNJ11"], panel) == (
        "KCNJ11",
        "by_position",
    )
    assert genes.assign_gene("11", in_abcc8, ["KCNJ11", "ABCC8"], panel) == ("ABCC8", "by_position")
    # between the two genes: nearest wins, flagged
    gap = panel["KCNJ11"].end + 100
    assert genes.assign_gene("11", gap, ["ABCC8", "KCNJ11"], panel) == ("KCNJ11", "nearest")
    assert genes.assign_gene("11", gap, ["TH"], panel) == (None, "no_panel_gene")


# ---------------------------------------------------------------- C2-C8 ClinVar


def test_clinvar_rules(tmp_path, panel):
    kc = panel["KCNJ11"].start + 50
    lines = [
        vcf_line(
            "12",
            120978600,
            "1",
            "C",
            "T",
            "CLNSIG=Pathogenic/Likely_risk_allele;GENEINFO=HNF1A:6927;CLNREVSTAT=reviewed_by_expert_panel",
        ),
        vcf_line(
            "12",
            120978601,
            "2",
            "C",
            "T",
            "CLNSIG=Conflicting_interpretations_of_pathogenicity;GENEINFO=HNF1A:6927",
        ),
        vcf_line("12", 120978602, "3", "C", ".", "CLNSIG=Benign;GENEINFO=HNF1A:6927"),  # C5
        vcf_line("12", 120978603, "4", "C", "T", "ONC=Oncogenic;GENEINFO=HNF1A:6927"),  # C4
        vcf_line(
            "11", kc, "5", "G", "A", "CLNSIG=Pathogenic;GENEINFO=ABCC8:6833|KCNJ11:3767"
        ),  # C6
        vcf_line(
            "7", 44150000, "6", "A" * 60, "A", "CLNSIG=Uncertain_significance;GENEINFO=GCK:2645"
        ),  # C7
        vcf_line("1", 1000, "7", "A", "G", "CLNSIG=Pathogenic;GENEINFO=BRCA1:672"),  # not panel
        vcf_line("7", 44150001, "8", "A", "G", "CLNSIG=risk_factor;GENEINFO=GCK:2645"),
    ]
    date, rows, counts = clean(tmp_path, lines, panel)
    assert date == "2023-01-07"
    by_id = {r["clinvar_id"]: r for r in rows}
    assert set(by_id) == {"1", "2", "5", "6", "8"}
    assert by_id["1"]["classification"] == "P" and by_id["1"]["stars"] == 3
    assert by_id["1"]["expert_reviewed"] == 1
    assert by_id["2"]["classification"] == "CONFLICT"
    assert by_id["5"]["gene"] == "KCNJ11" and by_id["5"]["gene_method"] == "by_position"
    assert by_id["6"]["long_indel"] == 1
    assert by_id["8"]["classification"] == "OTHER"
    assert counts["dropped: ALT missing or multi-allelic"] == 1
    assert counts["dropped: no germline CLNSIG"] == 1
    assert counts["  of which oncogenicity/somatic only"] == 1
    assert counts["kept: risk-allele combination mapped to its tier"] == 1
    assert counts["clean records"] == 5


def test_clinvar_rejects_wrong_reference(tmp_path, panel):
    with pytest.raises(CleaningError, match="GRCh38"):
        clean(tmp_path, [], panel, ref="GRCh37")


def test_clinvar_rejects_chr_prefix(tmp_path, panel):
    line = vcf_line("chr7", 44150000, "1", "A", "G", "CLNSIG=Benign;GENEINFO=GCK:2645")
    with pytest.raises(CleaningError, match="chr"):
        clean(tmp_path, [line], panel)


def test_clinvar_rejects_duplicate_keys(tmp_path, panel):
    line = vcf_line("7", 44150000, "1", "A", "G", "CLNSIG=Benign;GENEINFO=GCK:2645")
    with pytest.raises(CleaningError, match="duplicate"):
        clean(tmp_path, [line, line], panel)


def test_clinvar_output_sorted(tmp_path, panel):
    lines = [
        vcf_line("12", 120978700, "1", "C", "T", "CLNSIG=Benign;GENEINFO=HNF1A:6927"),
        vcf_line("7", 44150000, "2", "A", "G", "CLNSIG=Benign;GENEINFO=GCK:2645"),
    ]
    _, rows, _ = clean(tmp_path, lines, panel)
    assert [r["chrom"] for r in rows] == ["7", "12"]


# ---------------------------------------------------------------- C9-C10 eRepo

EREPO_COLS = [
    "#Variation",
    "ClinVar Variation Id",
    "Allele Registry Id",
    "HGVS Expressions",
    "HGNC Gene Symbol",
    "Assertion",
    "Expert Panel",
    "Approval Date",
    "Published Date",
    "Retracted",
    "Uuid",
    "Applied Evidence Codes (Met)",
]


def erepo_row(**kw):
    base = dict.fromkeys(EREPO_COLS, "")
    base.update(
        {
            "HGNC Gene Symbol": "HNF1A",
            "Assertion": "Pathogenic",
            "Retracted": "false",
            "Approval Date": "2022-01-01",
            "Expert Panel": "Monogenic Diabetes VCEP",
        }
    )
    base.update(kw)
    return base


def write_erepo(path, rows):
    with open(path, "w") as fh:
        fh.write("\t".join(EREPO_COLS) + "\n")
        for r in rows:
            fh.write("\t".join(r[c] for c in EREPO_COLS) + "\n")
    return path


def test_erepo_rules(tmp_path):
    hgvs = (
        "NM_000545.6:c.10C>T, NM_000545.8:c.10C>T, NC_000012.12:g.120978600C>T, NC_000012.11:g.1C>T"
    )
    rows = [
        erepo_row(**{"ClinVar Variation Id": "100", "Uuid": "a", "HGVS Expressions": hgvs}),
        erepo_row(
            **{
                "ClinVar Variation Id": "-",
                "Uuid": "b",
                "Assertion": "Likely Pathogenic",
                "HGVS Expressions": "NC_000012.12:g.120978601C>T",
            }
        ),
        erepo_row(
            **{"ClinVar Variation Id": "-", "Uuid": "c", "HGVS Expressions": "NC_000012.12:g.9A>G"}
        ),
        erepo_row(**{"Uuid": "d", "Retracted": "true"}),
        erepo_row(**{"Uuid": "e", "Approval Date": "01/02/2022"}),
        erepo_row(**{"Uuid": "f", "Assertion": "Risk factor"}),
        erepo_row(**{"Uuid": "a"}),  # duplicate UUID
        erepo_row(**{"Uuid": "g", "HGNC Gene Symbol": "BRCA1"}),
    ]
    releases = {
        "2023-01-07": [
            {"clinvar_id": "100", "hgvs_g": "NC_000012.12:g.120978600C>T"},
            {"clinvar_id": "200", "hgvs_g": "NC_000012.12:g.120978601C>T"},
        ]
    }
    section = Report("t").section("s")
    out = erepo.clean_erepo(
        write_erepo(tmp_path / "e.txt", rows), {"HNF1A": "NM_000545"}, releases, section
    )
    counts = {d: n for _, d, n in section.rows}
    by = {r["erepo_uuid"]: r for r in out}
    assert set(by) == {"a", "b", "c"}
    assert by["a"]["link_method"] == "clinvar_id"
    assert by["a"]["hgvs_g"] == "NC_000012.12:g.120978600C>T"  # GRCh38, not GRCh37
    assert by["a"]["transcript_hgvs"] == "NM_000545.8:c.10C>T"  # newest transcript version
    assert by["b"]["clinvar_id"] == "200" and by["b"]["link_method"] == "hgvs_g"
    assert by["b"]["classification"] == "LP"
    assert by["c"]["link_method"] == "unlinked"
    assert counts["dropped: retracted"] == 1
    assert counts["dropped: missing or invalid approval date"] == 1
    assert counts["dropped: unknown assertion text"] == 1
    assert counts["dropped: duplicate eRepo UUID"] == 1


def test_erepo_missing_column(tmp_path):
    (tmp_path / "e.txt").write_text("Uuid\tAssertion\n1\tBenign\n")
    with pytest.raises(CleaningError, match="missing columns"):
        erepo.clean_erepo(tmp_path / "e.txt", {}, {}, None)


# ---------------------------------------------------------------- full pipeline on real data


@pytest.mark.slow
@pytest.mark.skipif(
    not (RAW / "clinvar" / "clinvar_20230107.vcf.gz").exists(), reason="raw data absent"
)
def test_pipeline_on_real_data(tmp_path):
    run_all.main(
        ["--raw", str(RAW), "--clean", str(tmp_path), "--curated", str(CURATED), "--skip-manifest"]
    )
    report = (tmp_path / "QUALITY_REPORT.md").read_text()
    assert "Release-to-release changes" in report
    old = read_tsv(tmp_path / "clinvar_20230107.panel.tsv")
    new = read_tsv(tmp_path / "clinvar_20261019.panel.tsv")
    assert len(old) > 4000 and len(new) > 8000
    for rows in (old, new):
        assert all(r["classification"] for r in rows)
        keys = [(r["chrom"], r["pos"], r["ref"], r["alt"]) for r in rows]
        assert len(keys) == len(set(keys))
    # the C2 fix: no row with a pathogenic tier is left as OTHER
    assert not [
        r
        for r in new
        if r["clnsig_raw"].startswith("Pathogenic") and r["classification"] == "OTHER"
    ]
    ere = read_tsv(tmp_path / "erepo_classifications.tsv")
    assert sum(r["link_method"] in ("clinvar_id", "hgvs_g") for r in ere) > 1100
