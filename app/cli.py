"""`flask ...` commands. Re-evaluation and benchmark commands are added in later
stages (plan §4); this module registers them all in one place."""

from pathlib import Path

import click
from flask import current_app
from sqlalchemy import func, select

from app.extensions import db
from app.models.base import ROLES


@click.command("init-db")
@click.option("--drop", is_flag=True, help="Drop all tables first (destroys data).")
def init_db(drop):
    """Create tables from the models (dev shortcut; normally use `flask db upgrade`)."""
    if drop:
        click.confirm("Drop ALL tables and their data?", abort=True)
        db.drop_all()
    db.create_all()
    click.echo(f"Database ready: {current_app.config['SQLALCHEMY_DATABASE_URI']}")


@click.command("show-config")
def show_config():
    """Print the non-secret settings in use."""
    for key in (
        "SQLALCHEMY_DATABASE_URI",
        "PANEL_GENES",
        "THERAPY_REFERENCE_DATE",
        "DATA_RAW_DIR",
        "DATA_CLEAN_DIR",
        "DATA_CURATED_DIR",
    ):
        click.echo(f"{key} = {current_app.config[key]}")


@click.command("create-user")
@click.argument("username")
@click.option("--role", type=click.Choice(ROLES), required=True)
@click.password_option()
def create_user(username, role, password):
    """Create a login account (passwords are stored hashed)."""
    from app.models import User

    if db.session.scalar(select(User).where(User.username == username)):
        raise click.ClickException(f"user {username!r} already exists")
    user = User(username=username, role=role)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    click.echo(f"Created {role} {username!r}")


# ------------------------------------------------------------------------ knowledge


def _run(fn, *args, **kwargs):
    """Run an import in one transaction; roll back and report on any error."""
    from app.services.knowledge_import import KnowledgeImportError
    from app.services.rules import RuleValidationError

    try:
        result = fn(db.session, *args, **kwargs)
        db.session.commit()
        return result
    except (KnowledgeImportError, RuleValidationError, FileNotFoundError) as exc:
        db.session.rollback()
        raise click.ClickException(str(exc)) from exc


@click.command("import-release")
@click.argument("path", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--make-current/--no-make-current",
    default=None,
    help="Default: current only if no release is current yet.",
)
def import_release_cmd(path, make_current):
    """Import one cleaned ClinVar release (data/clean/clinvar_YYYYMMDD.panel.tsv)."""
    from app.services.knowledge_import import import_release

    r = _run(import_release, path, make_current=make_current)
    click.echo(
        f"Imported ClinVar {r.version}: {r.record_count:,} records"
        f"{' (current)' if r.is_current else ''}"
    )


@click.command("import-expert")
@click.argument("path", type=click.Path(exists=True, dir_okay=False))
def import_expert_cmd(path):
    """Import ClinGen expert-panel classifications (data/clean/erepo_classifications.tsv)."""
    from app.services.knowledge_import import import_expert_classifications

    added, skipped = _run(import_expert_classifications, path)
    click.echo(f"Expert classifications: {added:,} added, {skipped:,} already present")


@click.command("import-rules")
@click.argument("path", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--make-current/--no-make-current",
    default=None,
    help="Default: current only if the rule code has no current version.",
)
def import_rules_cmd(path, make_current):
    """Import treatment-rule versions from a YAML file (data/curated/rules_v*.yaml)."""
    from app.services.knowledge_import import import_rules

    added, unchanged = _run(import_rules, path, make_current=make_current)
    click.echo(f"Rules: {added} added, {unchanged} already present")


@click.command("import-flags")
@click.argument("path", type=click.Path(exists=True, dir_okay=False))
def import_flags_cmd(path):
    """Import curated variant flags, e.g. activating KCNJ11/ABCC8 variants."""
    from app.services.knowledge_import import import_curated_flags

    click.echo(f"Curated flags added: {_run(import_curated_flags, path)}")


@click.command("load-knowledge")
def load_knowledge():
    """Import everything Stage 1a produced: all ClinVar releases, eRepo, transcripts, rules v1.

    The oldest release becomes current; newer ones wait for re-evaluation (Stage 3).
    Releases that are already imported are skipped.
    """
    from app.models import KbRelease
    from app.services.knowledge_import import (
        import_expert_classifications,
        import_release,
        import_rules,
        import_transcripts,
    )

    clean = Path(current_app.config["DATA_CLEAN_DIR"])
    curated = Path(current_app.config["DATA_CURATED_DIR"])
    files = sorted(clean.glob("clinvar_*.panel.tsv"))
    if not files:
        raise click.ClickException(f"no cleaned releases in {clean}; run the cleaning step first")

    have = set(db.session.scalars(select(KbRelease.source_file)))
    for f in files:
        if f.name in have:
            click.echo(f"  skip {f.name} (already imported)")
            continue
        r = _run(import_release, f)
        state = " (current)" if r.is_current else ""
        click.echo(f"  ClinVar {r.version}: {r.record_count:,} records{state}")
    added, skipped = _run(import_expert_classifications, clean / "erepo_classifications.tsv")
    click.echo(f"  eRepo: {added:,} added, {skipped:,} already present")
    click.echo(f"  transcripts: {_run(import_transcripts, curated / 'gene_transcripts.tsv')}")
    added, unchanged = _run(import_rules, curated / "rules_v1.yaml")
    click.echo(f"  rules v1: {added} added, {unchanged} already present")
    flags = curated / "activating_variants.tsv"
    if flags.exists():
        from app.services.knowledge_import import import_curated_flags

        click.echo(f"  curated flags: {_run(import_curated_flags, flags)} added")
    else:
        click.echo(f"  curated flags: {flags} not found yet (needed for rule D4, Stage 2)")


@click.command("list-knowledge")
def list_knowledge():
    """Show imported ClinVar releases and rule versions."""
    from app.models import ExpertClassification, KbRelease, Rule

    click.echo("ClinVar releases:")
    for r in db.session.scalars(select(KbRelease).order_by(KbRelease.release_date)):
        click.echo(
            f"  {r.version}  {r.record_count:>6,} records  {'CURRENT' if r.is_current else ''}"
        )
    n = db.session.scalar(select(func.count()).select_from(ExpertClassification))
    click.echo(f"Expert classifications: {n:,}")
    click.echo("Rules:")
    for r in db.session.scalars(select(Rule).order_by(Rule.code, Rule.version)):
        state = "current" if r.is_current else "pending/retired"
        click.echo(f"  {r.code} v{r.version}  {r.severity:<6}  {','.join(r.genes):<40} {state}")


@click.command("kb-diff")
@click.argument("old_version")
@click.argument("new_version")
def kb_diff(old_version, new_version):
    """Count upgrades, downgrades and within-band changes between two imported releases."""
    from app.services.knowledge_import import release_diff

    d = _run(release_diff, old_version, new_version)
    c = d["categories"]
    click.echo(
        f"{old_version} -> {new_version}: {d['both']:,} variants in both releases, "
        f"{d['added']:,} added, {d['removed']:,} removed"
    )
    for cat in ("upgrade", "downgrade", "within_actionable", "within_nonactionable", "unchanged"):
        click.echo(f"  {cat:<22}{c[cat]:>7,}")
    click.echo(f"  {'gene':<8}{'up':>6}{'down':>6}")
    for gene, gc in d["by_gene"].items():
        click.echo(f"  {gene:<8}{gc['upgrade']:>6}{gc['downgrade']:>6}")


# ------------------------------------------------------------------------ re-evaluation (F5)


def _as_of():
    import datetime as dt

    return dt.date.fromisoformat(current_app.config["THERAPY_REFERENCE_DATE"])


def _print_run(run):
    click.echo(
        f"Run {run.id} ({run.trigger}, {run.mode}): {run.old_ref} -> {run.new_ref}\n"
        f"  variants examined {run.variants_examined:,}, changed {run.variants_changed:,}\n"
        f"  patients re-evaluated {run.patients_touched:,}\n"
        f"  alerts created {run.alerts_created:,}, superseded {run.alerts_superseded:,}, "
        f"provenance updates {run.provenance_updates:,}\n"
        f"  {run.notes or ''}"
    )


@click.command("reevaluate")
@click.argument("version")
@click.option(
    "--mode",
    type=click.Choice(["change_driven", "full", "alert_once"]),
    default="change_driven",
    show_default=True,
)
def reevaluate_cmd(version, mode):
    """Algorithm 2: make ClinVar release VERSION current and re-evaluate affected patients."""
    from app.services.reevaluate import ReevaluationError, reevaluate_release

    try:
        run = reevaluate_release(db.session, version, _as_of(), mode=mode)
        db.session.commit()
    except ReevaluationError as exc:
        db.session.rollback()
        raise click.ClickException(str(exc)) from exc
    _print_run(run)


@click.command("apply-rule")
@click.argument("code")
@click.argument("version", type=int)
@click.option(
    "--mode",
    type=click.Choice(["change_driven", "full", "alert_once"]),
    default="change_driven",
    show_default=True,
)
def apply_rule_cmd(code, version, mode):
    """Algorithm 3: make rule CODE version VERSION current and re-run it for carriers."""
    from app.services.reevaluate import ReevaluationError, reevaluate_rule

    try:
        run = reevaluate_rule(db.session, code, version, _as_of(), mode=mode)
        db.session.commit()
    except ReevaluationError as exc:
        db.session.rollback()
        raise click.ClickException(str(exc)) from exc
    _print_run(run)


@click.command("runs")
@click.option("--limit", default=10, show_default=True)
def runs_cmd(limit):
    """Show the most recent re-evaluation runs (the F5 run log)."""
    from app.models import ReevaluationRun

    q = select(ReevaluationRun).order_by(ReevaluationRun.id.desc()).limit(limit)
    for run in db.session.scalars(q):
        _print_run(run)


@click.command("seed-cohort")
@click.option(
    "--cohort",
    default="ge_ehr_test_data_v2",
    show_default=True,
    type=click.Path(exists=True, file_okay=False),
)
def seed_cohort_cmd(cohort):
    """Load the 33-patient test cohort (therapy from its scenarios.csv) and upload its VCFs."""
    from app.genes import load_genes
    from app.services.seed import seed_cohort
    from app.services.workflow import NoCurrentReleaseError

    genes = load_genes(Path(current_app.config["DATA_CURATED_DIR"]) / "panel_genes.tsv")
    try:
        created, skipped, rejected = seed_cohort(db.session, cohort, genes, _as_of())
        db.session.commit()
    except NoCurrentReleaseError as exc:
        db.session.rollback()
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Patients: {created} created, {skipped} already present, {rejected} VCFs rejected")


COMMANDS = (
    init_db,
    show_config,
    create_user,
    import_release_cmd,
    import_expert_cmd,
    import_rules_cmd,
    import_flags_cmd,
    load_knowledge,
    list_knowledge,
    kb_diff,
    reevaluate_cmd,
    apply_rule_cmd,
    runs_cmd,
    seed_cohort_cmd,
)


def register_cli(app):
    for cmd in COMMANDS:
        app.cli.add_command(cmd)
