"""Build the reclassification test cohort (RS1-RS7, S10) for GE-EHR.

Each patient's genome is fixed: the same VCF is uploaded under the OLD ClinVar
release and re-evaluated under the NEW one. Only the knowledge base changes.

What it does, starting from an existing cohort folder (never modified):
  RS1/RS6  FAMILY1 patients get one shared HNF1A variant that is really
           VUS in the old release and P/LP in the new one (upgrade).
  RS2      The two RS2 patients get GCK variants that are really P/LP in the
           old release and VUS in the new one (downgrade).
  RS3      New patient: HNF4A LP -> P (within actionable band).
  RS4      New patient: HNF1A VUS -> LB/B (within non-actionable band).
  RS5      New patient: ABCC8 P/LP in both releases, neonatal-diabetes
           variant, diagnosed in infancy, on insulin (tests rule D4 v1 -> v2).
  RS7      A variant that changes band but that no patient carries.
  S10      Malformed VCF files.
It also writes expected_changes.csv: the ground truth for every stored variant
whose class changes between the two releases.

Usage:
  python scripts/build_reclass_cohort.py \
      --old data/raw/clinvar/clinvar_20230107.vcf.gz \
      --new data/raw/clinvar/clinvar_20261019.vcf.gz \
      --source data/archive/ge_ehr_test_data --out ge_ehr_test_data_v2
"""

import argparse
import csv
import gzip
import shutil
import struct
import sys
import uuid
import zlib
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from clinvar_panel import (  # noqa: E402
    ACTIONABLE,
    NOT_CLASSIFIED,
    band,
    change_category,
    classify,
    load_release,
)

CHROM_ORDER = [str(i) for i in range(1, 23)] + ["X", "Y", "MT"]
NON_ACTIONABLE_KNOWN = {"VUS", "LB", "B", "B/LB", "CONFLICT"}


# --------------------------------------------------------------------------
# VCF reading / writing
# --------------------------------------------------------------------------


def chrom_rank(chrom):
    return CHROM_ORDER.index(chrom) if chrom in CHROM_ORDER else len(CHROM_ORDER)


def read_patient_vcf(path):
    """Return (meta_lines, chrom_line, background_records, inserted_record)."""
    meta, chrom_line, background, inserted = [], None, [], None
    with gzip.open(path, "rt") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if line.startswith("##"):
                meta.append(line)
            elif line.startswith("#CHROM"):
                chrom_line = line
            elif line:
                if "INSERTED" in line.split("\t")[7]:
                    inserted = line
                else:
                    background.append(line)
    return meta, chrom_line, background, inserted


def record_key(line):
    c = line.split("\t")
    return (c[0], c[1], c[3], c[4])


def inserted_line(key, clinvar_id):
    chrom, pos, ref, alt = key
    return "\t".join(
        [
            chrom,
            pos,
            clinvar_id,
            ref,
            alt,
            ".",
            "PASS",
            f"INSERTED;CLINVAR_ID={clinvar_id}",
            "GT",
            "0/1",
        ]
    )


def build_vcf_text(meta, sample_id, records, note):
    records = sorted(records, key=lambda r: (chrom_rank(r.split("\t")[0]), int(r.split("\t")[1])))
    chroms = sorted({r.split("\t")[0] for r in records}, key=chrom_rank)
    out = []
    for m in meta:
        if m.startswith(("##contig=", "##bcftools_", "##clinvar_release_used=")):
            continue
        out.append(m)
        if m.startswith("##reference="):
            out.extend(f"##contig=<ID={c}>" for c in chroms)
    out.append(f"##ge_ehr_note={note}")
    out.append(
        "\t".join(
            ["#CHROM", "POS", "ID", "REF", "ALT", "QUAL", "FILTER", "INFO", "FORMAT", sample_id]
        )
    )
    out.extend(records)
    return "\n".join(out) + "\n"


def write_bgzf(path, text):
    """Write BGZF (block gzip), the format bcftools/tabix produce."""
    data = text.encode()
    with open(path, "wb") as fh:
        for i in range(0, len(data) + 1, 65280):
            chunk = data[i : i + 65280]
            if not chunk and i:
                break
            comp = zlib.compressobj(6, zlib.DEFLATED, -15)
            cdata = comp.compress(chunk) + comp.flush()
            bsize = len(cdata) + 25
            fh.write(b"\x1f\x8b\x08\x04\x00\x00\x00\x00\x00\xff\x06\x00BC\x02\x00")
            fh.write(struct.pack("<H", bsize))
            fh.write(cdata)
            fh.write(struct.pack("<II", zlib.crc32(chunk) & 0xFFFFFFFF, len(chunk)))
        # standard BGZF end-of-file marker
        fh.write(bytes.fromhex("1f8b08040000000000ff0600424302001b0003000000000000000000"))


# --------------------------------------------------------------------------
# Variant selection
# --------------------------------------------------------------------------


def is_expert(rec):
    rs = rec["review_status"] if rec else ""
    return "expert_panel" in rs or "practice_guideline" in rs


def pick(old, new, blocked_pos, n, genes, accept, prefer=lambda o, nw: True, label=""):
    """Choose n variant keys whose (old, new) records satisfy accept().

    Ranked by: preferred exact change, expert-panel review in the new release,
    SNV, then genomic position (deterministic).
    """
    cands = []
    for key in set(old) | set(new):
        o, nw = old.get(key), new.get(key)
        gene = (nw or o)["gene"]
        if gene not in genes or (key[0], key[1]) in blocked_pos:
            continue
        if not accept(o, nw):
            continue
        snv = len(key[2]) == 1 and len(key[3]) == 1
        rank = (
            not prefer(o, nw),
            genes.index(gene),
            not is_expert(nw),
            not snv,
            chrom_rank(key[0]),
            int(key[1]),
        )
        cands.append((rank, key))
    cands.sort()
    if len(cands) < n:
        sys.exit(f"ERROR: only {len(cands)} candidate(s) for {label}, need {n}")
    chosen = [k for _, k in cands[:n]]
    blocked_pos.update((k[0], k[1]) for k in chosen)
    return chosen


def cls_of(rec):
    return rec["cls"] if rec else NOT_CLASSIFIED


# --------------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--old", required=True, help="old ClinVar VCF (.vcf.gz)")
    ap.add_argument("--new", required=True, help="new ClinVar VCF (.vcf.gz)")
    ap.add_argument(
        "--source",
        default="data/archive/ge_ehr_test_data",
        help="existing cohort folder (read only)",
    )
    ap.add_argument("--out", default="ge_ehr_test_data_v2", help="output folder (must not exist)")
    args = ap.parse_args()

    src, out = Path(args.source), Path(args.out)
    if out.exists():
        sys.exit(f"ERROR: {out} already exists; remove it or pick another --out")

    print(f"Loading {args.old} ...")
    old_date, old = load_release(args.old)
    print(f"  {len(old)} panel records, release {old_date}")
    print(f"Loading {args.new} ...")
    new_date, new = load_release(args.new)
    print(f"  {len(new)} panel records, release {new_date}")

    scenarios = list(csv.DictReader(open(src / "scenarios.csv")))
    inserted_rows = {
        r["patient_id"]: r for r in csv.DictReader(open(src / "inserted_variants.csv"))
    }

    patients = {}
    for row in scenarios:
        pid = row["patient_id"]
        patients[pid] = read_patient_vcf(src / "patients" / f"{pid}.vcf.gz")

    blocked_pos = set()
    for _, _, bg, ins in patients.values():
        for line in bg + ([ins] if ins else []):
            k = record_key(line)
            blocked_pos.add((k[0], k[1]))

    # ---- choose variants -------------------------------------------------
    rs1 = pick(
        old,
        new,
        blocked_pos,
        1,
        ["HNF1A"],
        accept=lambda o, n: cls_of(o) == "VUS" and cls_of(n) in ACTIONABLE,
        prefer=lambda o, n: cls_of(n) == "LP",
        label="RS1/RS6 (HNF1A VUS->P/LP)",
    )[0]
    rs2 = pick(
        old,
        new,
        blocked_pos,
        2,
        ["GCK"],
        accept=lambda o, n: cls_of(o) in ACTIONABLE and cls_of(n) in NON_ACTIONABLE_KNOWN,
        prefer=lambda o, n: cls_of(n) == "VUS",
        label="RS2 (GCK P/LP->VUS)",
    )
    rs3 = pick(
        old,
        new,
        blocked_pos,
        1,
        ["HNF4A", "HNF1A", "GCK"],
        accept=lambda o, n: cls_of(o) == "LP" and cls_of(n) in {"P", "P/LP"},
        prefer=lambda o, n: cls_of(n) == "P",
        label="RS3 (LP->P)",
    )[0]
    rs4 = pick(
        old,
        new,
        blocked_pos,
        1,
        ["HNF1A", "GCK", "HNF4A"],
        accept=lambda o, n: cls_of(o) == "VUS" and cls_of(n) in {"LB", "B", "B/LB"},
        prefer=lambda o, n: cls_of(n) == "LB",
        label="RS4 (VUS->LB/B)",
    )[0]

    def neonatal(rec):
        d = rec["disease"].lower() if rec else ""
        return "neonatal" in d and "hyperinsulin" not in d

    rs5 = pick(
        old,
        new,
        blocked_pos,
        1,
        ["ABCC8"],
        accept=lambda o, n: cls_of(o) in ACTIONABLE and cls_of(n) in ACTIONABLE and neonatal(n),
        prefer=lambda o, n: cls_of(o) == cls_of(n),
        label="RS5 (ABCC8 neonatal, stable P/LP)",
    )[0]
    rs7 = pick(
        old,
        new,
        blocked_pos,
        1,
        ["HNF1A", "GCK", "HNF4A"],
        accept=lambda o, n: (
            o is not None
            and n is not None
            and change_category(cls_of(o), cls_of(n)) in {"upgrade", "downgrade"}
        ),
        prefer=lambda o, n: band(cls_of(n)) == "actionable",
        label="RS7 (uncarried change)",
    )[0]

    # ---- assign variants to patients ------------------------------------
    assignment = {}  # patient_id -> key
    for row in scenarios:
        if row["share_group"] == "FAMILY1":
            assignment[row["patient_id"]] = rs1
    for row, key in zip([r for r in scenarios if r["scenario"] == "RS2"], rs2, strict=True):
        assignment[row["patient_id"]] = key

    donors = [r["patient_id"] for r in scenarios if r["scenario"] == "S9"]
    new_patients = [
        (
            "RS3",
            rs3,
            "metformin",
            "34.0",
            "Upload under old release: D3 medium alert. "
            "Re-eval: provenance updated, no notification",
        ),
        (
            "RS4",
            rs4,
            "insulin",
            "29.0",
            "Upload under old release: D6 info. Re-eval: provenance updated, no notification",
        ),
        (
            "RS5",
            rs5,
            "insulin",
            "0.25",
            "Neonatal ABCC8, infant onset, on insulin. "
            "Rule D4 v1: no D4 alert; D4 v2: new high alert",
        ),
    ]
    new_rows = []
    for i, (scen, key, therapy, age, note) in enumerate(new_patients):
        pid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"ge-ehr/{scen}"))
        donor = donors[i % len(donors)]
        meta, chrom_line, bg, _ = patients[donor]
        patients[pid] = (meta, chrom_line, bg, None)
        assignment[pid] = key
        rec = new.get(key) or old.get(key)
        new_rows.append(
            {
                "patient_id": pid,
                "scenario": scen,
                "gene": rec["gene"],
                "clinvar_class": cls_of(new.get(key)),
                "share_group": "",
                "current_therapy": therapy,
                "age_at_diagnosis": age,
                "sample_id": "",
                "note": note,
                "_donor": donor,
            }
        )

    # ---- write patient VCFs ---------------------------------------------
    (out / "patients").mkdir(parents=True)
    carriers = defaultdict(list)
    vcf_rows = {}
    for pid, (meta, _, bg, ins) in patients.items():
        if pid in assignment:
            key = assignment[pid]
            rec = new.get(key) or old.get(key)
            ins_line = inserted_line(key, rec["clinvar_id"])
            text = build_vcf_text(
                meta,
                pid,
                bg + [ins_line],
                f"inserted variant chosen by build_reclass_cohort.py "
                f"(old {old_date} / new {new_date})",
            )
            write_bgzf(out / "patients" / f"{pid}.vcf.gz", text)
        else:
            for ext in (".vcf.gz", ".vcf.gz.tbi"):
                f = src / "patients" / f"{pid}{ext}"
                if f.exists():
                    shutil.copy2(f, out / "patients" / f.name)
            ins_line = ins
        vcf_rows[pid] = ins_line
        for line in bg + [ins_line]:
            carriers[record_key(line)].append(pid)

    # ---- scenarios.csv / inserted_variants.csv ---------------------------
    scen_fields = list(scenarios[0].keys()) + ["old_class", "new_class", "change"]
    all_rows = scenarios + new_rows
    with open(out / "scenarios.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, scen_fields, extrasaction="ignore")
        w.writeheader()
        original_ids = {r["patient_id"] for r in scenarios}
        for row in all_rows:
            key = record_key(vcf_rows[row["patient_id"]])
            o, n = classify(old, key), classify(new, key)
            row = dict(row, old_class=o, new_class=n, change=change_category(o, n))
            if row["patient_id"] in assignment and row["patient_id"] in original_ids:
                row["clinvar_class"] = n
                row["note"] = {
                    "RS2": f"GCK {o} -> {n}: upload gives D1 alert; "
                    "re-eval supersedes it, review notice",
                }.get(
                    row["scenario"],
                    f"Shared HNF1A {o} -> {n} (family): "
                    "re-eval gives new D2 high alert, reclassified",
                )
            w.writerow(row)

    iv_fields = list(next(iter(inserted_rows.values())).keys()) + ["old_clinvar_class", "change"]
    with open(out / "inserted_variants.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, iv_fields)
        w.writeheader()
        for row in all_rows:
            pid = row["patient_id"]
            key = record_key(vcf_rows[pid])
            o, n = old.get(key), new.get(key)
            base = inserted_rows.get(
                pid,
                {
                    "patient_id": pid,
                    "scenario": row["scenario"],
                    "background_sample": inserted_rows[row.get("_donor", pid)]["background_sample"]
                    if row.get("_donor")
                    else "",
                    "background_variants": len(patients[pid][2]),
                },
            )
            w.writerow(
                dict(
                    base,
                    gene=(n or o)["gene"],
                    chrom=key[0],
                    pos=key[1],
                    ref=key[2],
                    alt=key[3],
                    clinvar_id=(n or o)["clinvar_id"],
                    clinvar_class=n["clnsig"] if n else NOT_CLASSIFIED,
                    review_status=n["review_status"] if n else "",
                    clinvar_release=new_date,
                    old_clinvar_class=o["clnsig"] if o else NOT_CLASSIFIED,
                    change=change_category(cls_of(o), cls_of(n)),
                )
            )

    # ---- expected_changes.csv (ground truth for F5) ----------------------
    with open(out / "expected_changes.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "chrom",
                "pos",
                "ref",
                "alt",
                "gene",
                "clinvar_id",
                "old_class",
                "new_class",
                "category",
                "n_carriers",
                "carrier_ids",
                "note",
            ]
        )
        keys = set(carriers) | {rs7}
        rows = []
        for key in keys:
            o, n = classify(old, key), classify(new, key)
            cat = change_category(o, n)
            if cat == "unchanged":
                continue
            rec = new.get(key) or old.get(key)
            if rec is None:
                continue  # not in either release: never classified, nothing to test
            rows.append(
                [
                    *key,
                    rec["gene"],
                    rec["clinvar_id"],
                    o,
                    n,
                    cat,
                    len(carriers.get(key, [])),
                    ";".join(carriers.get(key, [])),
                    "RS7: carried by no patient" if key == rs7 else "",
                ]
            )
        rows.sort(key=lambda r: (chrom_rank(r[0]), int(r[1])))
        w.writerows(rows)

    # ---- S10 malformed VCFs ----------------------------------------------
    bad = out / "malformed"
    bad.mkdir()
    header = (
        "##fileformat=VCFv4.2\n##reference=GRCh38\n##contig=<ID=12>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS10\n"
    )
    (bad / "S10_truncated_row.vcf").write_text(header + "12\t120978486\t.\tA\n")
    (bad / "S10_non_numeric_pos.vcf").write_text(header + "12\tabc\t.\tA\tC\t.\tPASS\t.\tGT\t0/1\n")
    (bad / "S10_missing_header.vcf").write_text("12\t120978486\t.\tA\tC\t.\tPASS\t.\tGT\t0/1\n")
    (bad / "S10_not_a_vcf.vcf.gz").write_bytes(b"this is not gzip or VCF data\n")

    # ---- verify & summary --------------------------------------------------
    print(f"\nWrote {out}/  ({len(patients)} patients)\n")
    print(f"{'scenario':<12}{'gene':<8}{'variant':<26}{'old':<7}{'new':<7}change")
    checks = [
        ("RS1/RS6", rs1, "upgrade"),
        ("RS2 #1", rs2[0], "downgrade"),
        ("RS2 #2", rs2[1], "downgrade"),
        ("RS3", rs3, "within_actionable"),
        ("RS4", rs4, "within_nonactionable"),
        ("RS5", rs5, None),
        ("RS7", rs7, None),
    ]
    ok = True
    for name, key, expect in checks:
        o, n = classify(old, key), classify(new, key)
        cat = change_category(o, n)
        rec = new.get(key) or old.get(key)
        print(f"{name:<12}{rec['gene']:<8}{':'.join(key):<26}{o:<7}{n:<7}{cat}")
        if expect and cat != expect:
            ok = False
    if band(classify(new, rs5)) != "actionable" or carriers.get(rs7):
        ok = False
    for pid, key in assignment.items():
        _, _, bg, ins = read_patient_vcf(out / "patients" / f"{pid}.vcf.gz")
        if record_key(ins) != key:
            ok = False
            print(f"  VCF check failed for {pid}")
    print(f"\nexpected_changes.csv: {len(rows)} changed variant(s) among stored keys + RS7")
    print("Self-check:", "PASSED" if ok else "FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
