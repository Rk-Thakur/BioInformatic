#!/usr/bin/env python3
"""
Step 2: Build one test VCF per Synthea patient by inserting a chosen ClinVar
variant (heterozygous, GT 0/1) into real 1000 Genomes background genotypes.

Requires only Python 3 (bcftools optional, used to compress/index output).

Usage (recommended: load patients straight from the cleaned Synthea data):
    python step2_build_patient_vcfs.py --clinvar clinvar.vcf.gz \
        --patients diabetes_patients.csv --background background_7genes.vcf.gz
    # without 1000 Genomes (variant-only files):
    python step2_build_patient_vcfs.py --clinvar clinvar.vcf.gz --patients diabetes_patients.csv
    # older option: use a hand-made plan instead
    python step2_build_patient_vcfs.py --clinvar clinvar.vcf.gz --scenarios scenarios.csv

Outputs:
    patients/<patient_id>.vcf(.gz)   one test file per patient
    inserted_variants.csv            exactly which variant went to which patient
    scenarios_assigned.csv           the scenario plan built from the Synthea data (with --patients)
"""
import argparse, csv, gzip, shutil, subprocess
from collections import defaultdict
from pathlib import Path

GENES = {"GCK", "HNF1A", "HNF4A", "HNF1B", "KCNJ11", "ABCC8", "INS"}
CLASS_MAP = {"P": "Pathogenic", "LP": "Likely_pathogenic", "VUS": "Uncertain_significance",
             "LB": "Likely_benign", "B": "Benign"}
CHROM_ORDER = {str(i): i for i in range(1, 23)} | {"X": 23, "Y": 24}


def opener(path):
    return gzip.open(path, "rt") if str(path).endswith(".gz") else open(path)


def norm_chrom(c):
    return c[3:] if c.startswith("chr") else c


def info_dict(info):
    out = {}
    for item in info.split(";"):
        if "=" in item:
            k, v = item.split("=", 1)
            out[k] = v
    return out


def load_clinvar(path):
    """Collect candidate SNVs per (gene, class) for the 7 diabetes genes."""
    cands, release = defaultdict(list), "unknown"
    with opener(path) as f:
        for line in f:
            if line.startswith("##fileDate="):
                release = line.strip().split("=", 1)[1]
            if line.startswith("#"):
                continue
            chrom, pos, vid, ref, alt, _, _, info = line.rstrip("\n").split("\t")[:8]
            if len(ref) != 1 or len(alt) != 1 or alt == ".":
                continue  # keep simple SNVs for clean test files
            d = info_dict(info)
            gene = d.get("GENEINFO", "").split(":")[0]
            if gene not in GENES:
                continue
            sig = d.get("CLNSIG", "")
            review = d.get("CLNREVSTAT", "")
            if "no_assertion" in review or "no_interpretation" in review:
                continue
            expert = "reviewed_by_expert_panel" in review
            cands[(gene, sig)].append((0 if expert else 1, norm_chrom(chrom), int(pos), ref, alt, vid, sig, review))
    for k in cands:
        cands[k].sort(key=lambda x: (x[0], CHROM_ORDER.get(x[1], 99), x[2]))  # expert-panel first
    return cands, release


def load_background(path):
    """Return samples and, per sample, its non-reference variant records."""
    samples, recs = [], defaultdict(list)
    with opener(path) as f:
        for line in f:
            if line.startswith("##"):
                continue
            cols = line.rstrip("\n").split("\t")
            if line.startswith("#CHROM"):
                samples = cols[9:]
                continue
            chrom, pos, vid, ref, alt = norm_chrom(cols[0]), int(cols[1]), cols[2], cols[3], cols[4]
            fmt = cols[8].split(":")
            gi = fmt.index("GT") if "GT" in fmt else 0
            for s, val in zip(samples, cols[9:]):
                gt = val.split(":")[gi]
                if gt.replace("|", "/") in ("0/0", "./.", ".", "0"):
                    continue
                recs[s].append((chrom, pos, vid, ref, alt, gt))
    return samples, recs


def write_vcf(path, patient, records, release):
    contigs = sorted({r[0] for r in records}, key=lambda c: CHROM_ORDER.get(c, 99))
    with open(path, "w") as f:
        f.write("##fileformat=VCFv4.2\n##source=GE-EHR test VCF builder\n")
        f.write(f"##clinvar_release_used={release}\n##reference=GRCh38\n")
        for c in contigs:
            f.write(f"##contig=<ID={c}>\n")
        f.write('##INFO=<ID=INSERTED,Number=0,Type=Flag,Description="Variant inserted from ClinVar for testing">\n')
        f.write('##INFO=<ID=CLINVAR_ID,Number=1,Type=String,Description="ClinVar variation ID">\n')
        f.write('##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n')
        f.write(f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{patient}\n")
        for chrom, pos, vid, ref, alt, gt, info in sorted(records, key=lambda r: (CHROM_ORDER.get(r[0], 99), r[1])):
            f.write(f"{chrom}\t{pos}\t{vid}\t{ref}\t{alt}\t.\tPASS\t{info}\tGT\t{gt}\n")


# Scenario plan, built from each patient's real Synthea data (alive patients only).
# (scenario, how many, therapy test, gene, ClinVar class, shared group, note)
PLAN = [
    ("S1", 4, lambda r: r["ins"], "HNF1A", "P", "", "On insulin; expect D2 high alert"),
    ("S2", 2, lambda r: r["met"] and not r["ins"], "HNF1A", "P", "", "ADD a sulfonylurea medication row by hand; expect info note only"),
    ("S3", 4, lambda r: r["met"], "GCK", "P", "", "On metformin; expect D1 medium alert"),
    ("S4", 3, lambda r: r["therapy"] == "none", "GCK", "P", "", "No medication; expect info note only"),
    ("S5", 3, lambda r: r["met"], "HNF4A", "LP", "", "On metformin; expect D3 medium alert"),
    ("S6", 2, lambda r: r["ins"], "KCNJ11", "P", "", "EDIT diagnosis age to <0.5 years for D4; expect high alert"),
    ("S8/RS1/RS6", 4, lambda r: r["ins"], "HNF1A", "VUS", "FAMILY1", "Shared HNF1A VUS (family); upgrade it in new ClinVar release for RS1/RS6"),
    ("S9", 3, lambda r: r["therapy"] != "none", "GCK", "B", "", "Benign only; expect no alert"),
    ("D5-HNF1B", 1, lambda r: r["therapy"] != "none", "HNF1B", "P", "", "Expect D5 cascade-testing alert"),
    ("D5-ABCC8", 1, lambda r: r["therapy"] != "none", "ABCC8", "P", "", "Expect D5 alert (D4 only if infant onset)"),
    ("D5-INS", 1, lambda r: r["therapy"] != "none", "INS", "P", "", "Expect D5 alert"),
    ("RS2", 2, lambda r: r["met"], "GCK", "LP", "", "Downgrade this GCK LP to VUS in new release; expect supersede"),
]


def truthy(v):
    return str(v).strip().lower() in ("true", "1", "yes")


def assign_scenarios(patients_csv):
    """Read the cleaned Synthea patients file and assign each test scenario
    to suitable living patients, based on their real current therapy."""
    pts = []
    for r in csv.DictReader(open(patients_csv)):
        if truthy(r.get("IS_DECEASED", "False")):
            continue
        pts.append({"id": r["PATIENT_ID"], "ins": truthy(r["CURRENT_INSULIN"]), "met": truthy(r["CURRENT_METFORMIN"]),
                    "su": truthy(r["CURRENT_SULFONYLUREA"]), "therapy": r["CURRENT_THERAPY"],
                    "age_dx": r.get("AGE_AT_DIAGNOSIS", ""), "gender": r.get("GENDER", "")})
    pts.sort(key=lambda r: r["id"])  # deterministic: same input -> same plan
    used, rows = set(), []
    for scen, n, test, gene, cls, grp, note in PLAN:
        chosen = [p for p in pts if p["id"] not in used and test(p)][:n]
        if len(chosen) < n:
            print(f"  ! only {len(chosen)} of {n} patients fit {scen}")
        for p in chosen:
            used.add(p["id"])
            rows.append({"patient_id": p["id"], "scenario": scen, "gene": gene, "clinvar_class": cls,
                         "share_group": grp, "current_therapy": p["therapy"], "age_at_diagnosis": p["age_dx"],
                         "sample_id": "", "note": note})
    print(f"Loaded {len(pts)} living Synthea patients; assigned {len(rows)} to test scenarios")
    with open("scenarios_assigned.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clinvar", required=True)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--patients", help="cleaned Synthea file diabetes_patients.csv (recommended)")
    src.add_argument("--scenarios", help="older option: a hand-made scenario plan CSV")
    ap.add_argument("--background", help="background_7genes.vcf.gz from step 1 (optional)")
    ap.add_argument("--out", default="patients")
    a = ap.parse_args()

    cands, release = load_clinvar(a.clinvar)
    print(f"ClinVar release {release}: candidates per gene/class:")
    for (g, s), v in sorted(cands.items()):
        if s in CLASS_MAP.values():
            print(f"  {g:7s} {s:24s} {len(v)}")

    samples, bg = load_background(a.background) if a.background else ([], {})
    rows = assign_scenarios(a.patients) if a.patients else list(csv.DictReader(open(a.scenarios)))
    Path(a.out).mkdir(exist_ok=True)

    used_idx, group_pick, manifest = defaultdict(int), {}, []
    for i, r in enumerate(rows):
        gene, cls, grp = r["gene"], CLASS_MAP[r["clinvar_class"]], r.get("share_group", "")
        pool = cands.get((gene, cls), [])
        if not pool:
            print(f"  ! no {cls} SNV for {gene} in ClinVar; skipping {r['patient_id']}")
            continue
        if grp and grp in group_pick:
            pick = group_pick[grp]              # family members share the same variant
        else:
            pick = pool[used_idx[(gene, cls)] % len(pool)]
            used_idx[(gene, cls)] += 1
            if grp:
                group_pick[grp] = pick
        _, chrom, pos, ref, alt, vid, sig, review = pick

        sample = r.get("sample_id") or (samples[i % len(samples)] if samples else "")
        records = [(c, p, v, rf, al, gt, ".") for c, p, v, rf, al, gt in bg.get(sample, [])
                   if not (c == chrom and p == pos)]
        records.append((chrom, pos, vid, ref, alt, "0/1", f"INSERTED;CLINVAR_ID={vid}"))
        vcf = Path(a.out) / f"{r['patient_id']}.vcf"
        write_vcf(vcf, r["patient_id"], records, release)
        if shutil.which("bcftools"):
            subprocess.run(["bcftools", "view", str(vcf), "-Oz", "-o", str(vcf) + ".gz"], check=True)
            subprocess.run(["bcftools", "index", "-t", str(vcf) + ".gz"], check=True)
            vcf.unlink()
        manifest.append({"patient_id": r["patient_id"], "scenario": r["scenario"],
                         "current_therapy": r.get("current_therapy", ""), "age_at_diagnosis": r.get("age_at_diagnosis", ""),
                         "background_sample": sample,
                         "gene": gene, "chrom": chrom, "pos": pos, "ref": ref, "alt": alt,
                         "clinvar_id": vid, "clinvar_class": sig, "review_status": review,
                         "clinvar_release": release, "background_variants": len(records) - 1})

    with open("inserted_variants.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(manifest[0].keys()), lineterminator="\n")
        w.writeheader()
        w.writerows(manifest)
    print(f"Done: {len(manifest)} patient VCFs in {a.out}/ and inserted_variants.csv")


if __name__ == "__main__":
    main()
