"""Service layer (F1-F5). No Flask imports here: every function takes a DB session,
so the benchmark and tests can call it directly (plan §4)."""
