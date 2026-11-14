"""Build the healthy-relatives demo dataset for Stage 7 (DD42, DD43).

24 people WITHOUT diabetes, each a relative (parent, sibling or child) of a patient in
the scenario cohort `ge_ehr_test_data_v2`. They are the people rule D5 says should be
offered cascade testing. Some carry the family variant, some do not.

Genome model (simplified Mendelian, documented in DD43):
  The cohort VCFs are phased 1000 Genomes backgrounds. A relative inherits, for each
  chromosome, one haplotype of the proband's background and one haplotype of an
  unrelated 1000 Genomes background (the "other parent"). The panel genes are small,
  so one haplotype per chromosome is realistic. Carriers also get the family's
  inserted ClinVar variant (0/1). This is a demo, not a pedigree simulator: siblings
  are modelled the same way as parents and children (about half shared).

Expected risk levels are computed here for every cleaned ClinVar release, using the
same precedence as the app (ClinGen expert classification approved on or before the
release date, else ClinVar, else "not classified") and the levels in
data/curated/risk_levels_v1.yaml. Step 6 checks the app against expected_risk.csv.

Usage:
  python -m scripts.build_healthy_relatives            # writes ge_ehr_healthy_relatives/
"""

import argparse
import csv
import datetime as dt
import hashlib
import json
import random
import sys
import uuid
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from build_reclass_cohort import build_vcf_text, chrom_rank, read_patient_vcf, write_bgzf  # noqa: E402

from app.domain import NOT_CLASSIFIED, PANEL_GENES, expert_as_of  # noqa: E402

COHORT = ROOT / "ge_ehr_test_data_v2"
CLEAN = ROOT / "data" / "clean"
LEVELS = ROOT / "data" / "curated" / "risk_levels_v1.yaml"
OUT = ROOT / "ge_ehr_healthy_relatives"
AS_OF = dt.date(2026, 11, 14)
NAMESPACE = uuid.UUID("6f1c2a52-7d1e-4b5e-9a39-2b7f0d9c4e11")

# Birth year used when the proband has no Synthea record (RS3-RS5 were created for the
# reclassification tests and have no birth date).
ANCHOR_BIRTH = {"737a78a3": 1990, "02960dc1": 1995, "781eee69": 2020}

# (proband 8-char id, relation, carrier?, sex). Chosen so every risk level and every
# kind of reclassification appears at least once; see README.md.
SPEC = [
    # FAMILY1 (RS1/RS6): HNF1A VUS in 2023 -> LP in 2026. Carriers: uncertain -> high.
    ("5dbf61ce", "child", True, "F"),
    ("5dbf61ce", "child", True, "M"),
    ("5dbf61ce", "sibling", True, "F"),
    ("5dbf61ce", "sibling", False, "M"),
    ("5dbf61ce", "child", False, "M"),
    # S1: HNF1A pathogenic in both releases -> high.
    ("0eaaad98", "child", True, "F"),
    ("0eaaad98", "child", True, "M"),
    ("0eaaad98", "child", False, "F"),
    # S2: HNF1A pathogenic -> high.
    ("074c01f3", "child", True, "M"),
    # S5: HNF4A LP -> high (pregnancy note for a woman).
    ("3a54d594", "child", True, "F"),
    ("3a54d594", "sibling", False, "M"),
    # RS3: HNF4A LP -> P (within the band) -> high in both.
    ("737a78a3", "sibling", True, "F"),
    # S4: GCK pathogenic -> mild.
    ("069da380", "child", True, "F"),
    ("069da380", "child", True, "M"),
    ("069da380", "parent", False, "F"),
    # RS2: GCK LP in 2023 -> VUS in 2026. Carriers: mild -> uncertain.
    ("7c7e4fdc", "child", True, "M"),
    ("7c7e4fdc", "child", True, "F"),
    # RS4: HNF1A VUS in 2023 -> LB in 2026. Carrier: uncertain -> normal.
    ("02960dc1", "sibling", True, "M"),
    # D5 genes: HNF1B, INS, KCNJ11, ABCC8 -> high.
    ("5f2e15ce", "child", True, "F"),
    ("5f2e15ce", "child", False, "M"),
    ("783bd394", "child", True, "F"),
    ("4db3beee", "child", True, "M"),
    ("781eee69", "parent", True, "M"),
    # S9: GCK benign -> normal even for a carrier.
    ("47ebad6b", "sibling", True, "F"),
]
OFFSET_YEARS = {"parent": -27, "sibling": 3, "child": 28}


def read_csv(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def write_csv(path, rows):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def rng_for(*parts):
    return random.Random(int(hashlib.sha256("|".join(parts).encode()).hexdigest()[:16], 16))


# ----------------------------------------------------------------------- knowledge


def load_releases(clean):
    """{release date: {key: (gene, classification, clinvar_id)}} from the cleaned panels."""
    out = {}
    for f in sorted(clean.glob("clinvar_*.panel.tsv")):
        d = f.stem.split("_")[1].split(".")[0]
        date = dt.date(int(d[:4]), int(d[4:6]), int(d[6:]))
        with open(f, newline="") as fh:
            rows = csv.DictReader((ln for ln in fh if not ln.startswith("#")), delimiter="\t")
            out[date] = {
                (r["chrom"], r["pos"], r["ref"], r["alt"]): (
                    r["gene"],
                    r["classification"],
                    r["clinvar_id"],
                )
                for r in rows
            }
    return out


def load_experts(clean):
    experts = defaultdict(list)
    with open(clean / "erepo_classifications.tsv", newline="") as fh:
        for r in csv.DictReader(fh, delimiter="\t"):
            if r["clinvar_id"]:
                experts[r["clinvar_id"]].append(
                    {
                        "approval_date": dt.date.fromisoformat(r["approval_date"]),
                        "classification": r["classification"],
                    }
                )
    return experts


def classify(key, release_date, release, experts):
    """(gene, classification) with the app's precedence (classify.py, DD6)."""
    hit = release.get(key)
    if hit is None:
        return None, NOT_CLASSIFIED
    gene, cls, cid = hit
    expert = expert_as_of(experts.get(cid, []), release_date)
    return gene, (expert["classification"] if expert else cls)


def load_levels(path):
    spec = yaml.safe_load(Path(path).read_text())
    table = {}
    for a in spec["assignments"]:
        for g in a["genes"]:
            for c in a["classes"]:
                table[(g, c)] = (a["level"], a["code"])
    return spec["precedence"], table


def risk_level(findings, precedence, table):
    """findings: [(gene, classification, key)] -> (level, code, gene, class, key)."""
    best = ("normal", "G6", "", "", "")
    for gene, cls, key in findings:
        level, code = table[(gene, cls)]
        if precedence.index(level) < precedence.index(best[0]):
            best = (level, code, gene, cls, ":".join(key))
    return best


# ----------------------------------------------------------------------- genomes


def haplotypes(records):
    """Split phased background records into {chrom: [hap0 lines, hap1 lines]}."""
    by_chrom = defaultdict(lambda: ([], []))
    for line in records:
        c = line.split("\t")
        gt = c[9].replace("/", "|").split("|")
        for h in (0, 1):
            if len(gt) > h and gt[h] == "1":
                by_chrom[c[0]][h].append(c[:9])
    return by_chrom


def child_genome(proband_haps, other_haps, rng):
    """One haplotype per chromosome from each side; returns phased VCF lines."""
    gts = {}
    for side, haps in ((0, proband_haps), (1, other_haps)):
        for chrom in sorted(haps, key=chrom_rank):
            for cols in haps[chrom][rng.randrange(2)]:
                key = (cols[0], cols[1], cols[3], cols[4])
                gts.setdefault(key, [cols, ["0", "0"]])[1][side] = "1"
    return ["\t".join(cols + ["|".join(gt)]) for cols, gt in gts.values()]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cohort", default=COHORT, type=Path)
    ap.add_argument("--clean", default=CLEAN, type=Path)
    ap.add_argument("--levels", default=LEVELS, type=Path)
    ap.add_argument("--out", default=OUT, type=Path)
    args = ap.parse_args(argv)
    return build(args.cohort, args.clean, args.levels, args.out)


def build(cohort, clean, levels_path, out):
    cohort, clean, out = Path(cohort), Path(clean), Path(out)
    inserted = {r["patient_id"]: r for r in read_csv(cohort / "inserted_variants.csv")}
    scenarios = {r["patient_id"]: r for r in read_csv(cohort / "scenarios.csv")}
    synthea = {r["patient_id"]: r for r in read_csv(clean / "patients.csv")}
    full_id = {pid[:8]: pid for pid in scenarios}

    # Background haplotypes per 1000 Genomes sample (read from the cohort VCFs).
    sample_haps, sample_of = {}, {}
    for pid, ins in inserted.items():
        sample_of[pid] = ins["background_sample"]
        if ins["background_sample"] not in sample_haps:
            _, _, bg, _ = read_patient_vcf(cohort / "patients" / f"{pid}.vcf.gz")
            sample_haps[ins["background_sample"]] = haplotypes(bg)
    samples = sorted(sample_haps)

    releases = load_releases(clean)
    experts = load_experts(clean)
    precedence, table = load_levels(levels_path)

    (out / "vcfs").mkdir(parents=True, exist_ok=True)
    people, expected = [], []
    counter = defaultdict(int)
    for short, relation, carrier, sex in SPEC:
        pid = full_id[short]
        counter[short] += 1
        n = counter[short]
        rid = str(uuid.uuid5(NAMESPACE, f"{pid}/{n}"))
        rng = rng_for(rid)

        proband_birth = (
            dt.date.fromisoformat(synthea[pid]["birth_date"])
            if pid in synthea
            else dt.date(ANCHOR_BIRTH[short], 6, 1)
        )
        year = proband_birth.year + OFFSET_YEARS[relation] + (n % 3) - 1
        birth = dt.date(year, 1 + rng.randrange(12), 1 + rng.randrange(28))
        age = (AS_OF - birth).days / 365.25
        if not 5 <= age <= 90:
            raise SystemExit(f"{rid}: implausible age {age:.0f} for a {relation} of {short}")

        other = [s for s in samples if s != sample_of[pid]][rng.randrange(len(samples) - 1)]
        records = child_genome(sample_haps[sample_of[pid]], sample_haps[other], rng)
        meta, _, _, ins_line = read_patient_vcf(cohort / "patients" / f"{pid}.vcf.gz")
        if carrier:
            records.append(ins_line)
        records.sort(key=lambda r: (chrom_rank(r.split("\t")[0]), int(r.split("\t")[1])))
        note = (
            f"healthy relative ({relation}) of {short}; "
            f"{'carries' if carrier else 'does not carry'} the family variant; "
            "built by build_healthy_relatives.py"
        )
        write_bgzf(out / "vcfs" / f"{rid}.vcf.gz", build_vcf_text(meta, rid, records, note))

        ins = inserted[pid]
        scen = scenarios[pid]
        people.append(
            {
                "relative_id": rid,
                "name": f"Relative {rid[:8]} ({relation} of {short})",
                "proband_id": pid,
                "proband_scenario": scen["scenario"],
                "relation": relation,
                "sex": sex,
                "birth_date": birth.isoformat(),
                "diabetes": "none",
                "carrier": int(carrier),
                "family_gene": ins["gene"],
                "family_variant": ":".join((ins["chrom"], ins["pos"], ins["ref"], ins["alt"])),
                "family_clinvar_id": ins["clinvar_id"],
                "background_from_proband": sample_of[pid],
                "background_other_parent": other,
                "variants": len(records),
            }
        )

        keys = [tuple(r.split("\t")[i] for i in (0, 1, 3, 4)) for r in records]
        for date, release in releases.items():
            findings = []
            for key in keys:
                gene, cls = classify(key, date, release, experts)
                if gene in PANEL_GENES:
                    findings.append((gene, cls, key))
            level, code, gene, cls, key = risk_level(findings, precedence, table)
            expected.append(
                {
                    "relative_id": rid,
                    "release": date.isoformat(),
                    "level": level,
                    "code": code,
                    "deciding_gene": gene,
                    "deciding_class": cls,
                    "deciding_variant": key,
                    "uncertain_findings": sum(
                        table[(g, c)][0] == "uncertain" for g, c, _ in findings
                    ),
                }
            )

    write_csv(out / "relatives.csv", people)
    write_csv(out / "expected_risk.csv", expected)
    manifest = {
        "relatives": len(people),
        "carriers": sum(p["carrier"] for p in people),
        "families": len({p["proband_id"] for p in people}),
        "releases": [d.isoformat() for d in releases],
        "levels": {
            d.isoformat(): {
                lv: sum(e["level"] == lv for e in expected if e["release"] == d.isoformat())
                for lv in precedence
            }
            for d in releases
        },
        "risk_levels_file": str(Path(levels_path).relative_to(ROOT))
        if Path(levels_path).is_relative_to(ROOT)
        else str(levels_path),
        "note": "Synthetic demo data (DD43): 1000 Genomes backgrounds recombined per "
        "chromosome, family variants from ge_ehr_test_data_v2. Not real people.",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))
    return manifest


if __name__ == "__main__":
    main()
