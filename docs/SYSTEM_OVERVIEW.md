# GE-EHR — everything the application does, in diagrams

Reclassification-aware genomic decision support for monogenic diabetes. This page maps the
whole system: who can log in and what each role sees, every page and command, how a gene
test becomes advice or a risk level, how ClinVar changes are followed, the data model, the
data pipeline, Docker and the evaluation. Diagrams are Mermaid (GitHub, VS Code and most
Markdown viewers render them).

> Research prototype, synthetic data only. Recommendations and risk levels are advisory;
> GE-EHR never changes therapy by itself. Passwords are never written in this document.

**Contents**
1. [The big picture](#1-the-big-picture)
2. [Accounts, credentials and roles](#2-accounts-credentials-and-roles)
3. [Logging in](#3-logging-in)
4. [Pages and what each role can open](#4-pages-and-what-each-role-can-open)
5. [From gene test to advice](#5-from-gene-test-to-advice)
6. [Classification](#6-classification)
7. [Treatment rules D1–D6 (patients with diabetes)](#7-treatment-rules-d1d6-patients-with-diabetes)
8. [Genetic risk (people without diabetes)](#8-genetic-risk-people-without-diabetes)
9. [Re-evaluation when knowledge changes](#9-re-evaluation-when-knowledge-changes)
10. [Life of an alert](#10-life-of-an-alert)
11. [Family tree](#11-family-tree)
12. [Data model](#12-data-model)
13. [Data pipeline and datasets](#13-data-pipeline-and-datasets)
14. [Commands](#14-commands)
15. [Docker start-up](#15-docker-start-up)
16. [Evaluation](#16-evaluation)
17. [Security](#17-security)
18. [How it was built](#18-how-it-was-built)

---

## 1. The big picture

```mermaid
flowchart LR
    subgraph Sources["Public and synthetic sources"]
        CV["ClinVar releases<br/>2023-01-07 · 2023-12-30 · 2026-10-19"]
        CG["ClinGen expert panel<br/>1,167 classifications"]
        SY["Synthea patients<br/>87 with diabetes"]
        KG["1000 Genomes<br/>phased backgrounds"]
    end
    subgraph Prep["Stage 1a · cleaning"]
        CL["scripts/clean/run_all<br/>rules C1–C15<br/>QUALITY_REPORT.md"]
    end
    subgraph KB["Versioned knowledge base"]
        REL["ClinVar releases"]
        EXP["Expert classifications"]
        RUL["Treatment rules D1–D6<br/>v1 current · D4 v2 pending"]
        RSK["Risk levels G1–G6<br/>v1 current · v2 pending"]
    end
    subgraph App["GE-EHR (Flask)"]
        F1["F1 store VCF"]
        F2["F2 classify"]
        F3["F3 rules / risk"]
        F5["F5 re-evaluate"]
        F4["F4 FHIR export"]
        WEB["Website"]
    end
    subgraph People["Users"]
        LT["Lab technician"]
        CLN["Clinician"]
        ADM["Admin"]
    end
    CV & CG & SY --> CL --> KB
    KG --> DS["Test cohorts<br/>33 patients · 24 relatives · 10k scale"]
    DS --> F1
    KB --> F2
    F1 --> F2 --> F3 --> WEB
    KB -. "new release / rule / risk version" .-> F5 --> F3
    F3 --> F4
    WEB --- LT & CLN & ADM
```

## 2. Accounts, credentials and roles

Three demo accounts are created automatically. Their **password is generated** and only
stored in a file the operator can read; it is never shown on a page, in logs or here.

| Where | Accounts | Where to read the password |
|---|---|---|
| Local development | `demo_clinician`, `demo_lab_tech`, `demo_admin` | `cat instance/dev_accounts.txt` |
| Docker | the same three | `docker compose exec web cat /app/instance/demo_accounts.txt` |
| Your own users | any name | `flask create-user NAME --role clinician` (asks for a password) |

```mermaid
flowchart TB
    V(["Visitor<br/>not logged in"])
    V -->|"home page, login page"| L{{"Login"}}
    L -->|"username + password<br/>(stored as a hash)"| R{"Role?"}
    L -->|"Demo mode only (DEMO_LOGIN=1):<br/>dropdown, demo_* accounts only, no password"| R
    R -->|lab_tech| LT["Lab technician"]
    R -->|clinician| CL["Clinician"]
    R -->|admin| AD["Admin"]

    LT --> LT1["Patients list, search, filter"]
    LT --> LT2["Patient page: details, variants,<br/>uploads, family tree without risk"]
    LT --> LT3["Upload VCF"]
    LT -.-x LTX["Never: alerts, advice, risk levels,<br/>medications, FHIR, Overview, Knowledge"]

    CL --> C1["Overview dashboard"]
    CL --> C2["Everything the lab tech sees"]
    CL --> C3["Alerts worklist · Mark reviewed"]
    CL --> C4["Treatment advice, genetic risk card,<br/>family tree with risk and ClinVar replay"]
    CL --> C5["Add patient or relative · medications · FHIR"]
    CL --> C6["Knowledge page (read-only)"]

    AD --> A1["Website: same as clinician"]
    AD --> A2["Terminal: create users, import knowledge,<br/>re-evaluate, apply rules and risk levels"]
```

| Action | Visitor | Lab tech | Clinician | Admin |
|---|:-:|:-:|:-:|:-:|
| Home and login pages | ✅ | – | – | – |
| Overview dashboard | ❌ | ❌ | ✅ | ✅ |
| Patients list, search, With / Without diabetes filter | ❌ | ✅ | ✅ | ✅ |
| Patient page: details, variants, uploads, family tree | ❌ | ✅ | ✅ | ✅ |
| Upload a VCF | ❌ | ✅ | ✅ | ✅ |
| Alerts, review notices, info notes, history | ❌ | ❌ | ✅ | ✅ |
| Genetic risk card, risk column, family-tree risk and replay | ❌ | ❌ | ✅ | ✅ |
| Add patient / relative, medications, FHIR export | ❌ | ❌ | ✅ | ✅ |
| Alerts worklist, Mark reviewed | ❌ | ❌ | ✅ | ✅ |
| Knowledge page | ❌ | ❌ | ✅ | ✅ |
| Users, imports, re-evaluation, rule / risk revisions | ❌ | ❌ | ❌ | terminal |

## 3. Logging in

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant B as Browser
    participant W as GE-EHR
    participant DB as Database
    U->>B: open /login
    B->>W: GET /login
    W-->>B: form with CSRF token (+ demo dropdown if DEMO_LOGIN=1)
    alt username and password
        U->>B: type credentials
        B->>W: POST /login
        W->>DB: find user, check password hash
        alt valid and active
            W->>DB: audit "login"
            W-->>B: session cookie (HttpOnly, SameSite=Lax), redirect
        else wrong user or password
            W->>DB: audit "login_failed"
            W-->>B: 401 "Invalid username or password." (same text either way)
        end
    else demo account (demo mode only)
        U->>B: choose demo_clinician / demo_lab_tech / demo_admin
        B->>W: POST /login/demo
        W->>W: only demo_* accounts accepted (403 otherwise)
        W->>DB: audit "demo_login"
        W-->>B: session cookie, redirect
    end
    Note over W,B: clinician / admin → Overview · lab technician → Patients
```

## 4. Pages and what each role can open

```mermaid
flowchart LR
    H["/ · home page"] --> LG["/login"]
    LG --> OV["/overview<br/>clinical"]
    LG --> PL["/patients/<br/>all roles"]
    OV --> AL["/alerts/<br/>worklist · clinical"]
    OV --> KN["/knowledge/<br/>releases · rules · risk levels · runs"]
    PL --> NP["/patients/new<br/>clinical"]
    PL --> PD["/patients/ID<br/>all roles"]
    PD --> UP["POST …/upload<br/>all roles"]
    PD --> MD["POST …/medications · …/stop<br/>clinical"]
    PD --> FH["GET …/fhir<br/>clinical"]
    AL --> RV["POST /alerts/ID/review<br/>clinical"]
    HL["/health"]:::ops
    classDef ops fill:#eef,stroke:#88a
```

What the patient page shows:

```mermaid
flowchart TB
    P["Patient page"] --> HD["Header: ID, family, active, no diabetes, relative of …"]
    P --> DX["Diagnosis · or 'No diabetes'"]
    P --> TH["Current therapy"]
    P --> RC["Genetic risk card<br/>(people without diabetes, clinical)"]
    P --> AL["Alerts / Risk findings timeline<br/>(clinical)"]
    P --> VA["Variants in panel genes<br/>class badge, expert-panel badge, ClinVar link"]
    P --> UP["Upload VCF + upload history"]
    P --> ME["Medications (+ add / stop, clinical)"]
    P --> FA["Family: animated family tree + table"]
    P --> HI["History: superseded and reviewed items"]
    P --> FX["FHIR export button (clinical)"]
```

## 5. From gene test to advice

```mermaid
sequenceDiagram
    autonumber
    actor LT as Lab tech / clinician
    participant W as Website
    participant V as F1 vcf_service
    participant C as F2 classify
    participant E as F3 rule engine
    participant DB as Database
    LT->>W: upload VCF (.vcf / .vcf.gz, GRCh38, one sample)
    W->>V: validate (structure, columns, sample, chromosomes)
    alt malformed
        V-->>W: rejected + reason (e.g. "expected 10 columns")
    else valid
        V->>DB: store variants in the 7 panel genes (± 5 kb)
        W->>C: classify each variant with the CURRENT ClinVar release
        C->>DB: expert panel (as of release date) → ClinVar → "not classified"
        W->>E: evaluate
        alt person has diabetes
            E->>E: treatment rules D1–D6 + current therapy + age at diagnosis
        else no diabetes
            E->>E: genetic risk levels G1–G6 (+ frequency filter in v2)
        end
        E->>DB: create / keep / supersede alerts, append provenance
        W-->>LT: "VCF accepted: N variants" (+ "M new alerts" for clinicians)
    end
```

## 6. Classification

```mermaid
flowchart TD
    K["Variant key<br/>chrom · pos · ref · alt"] --> Q{"In this ClinVar release?"}
    Q -- no --> NC["NC · not classified<br/>(never treated as VUS)"]
    Q -- yes --> E{"ClinGen expert classification<br/>approved on or before<br/>the release date?"}
    E -- yes --> EX["Expert class<br/>(badge: ClinGen expert panel)"]
    E -- no --> CV["ClinVar class<br/>P · LP · P/LP · VUS · LB · B · CONFLICT · OTHER"]
    EX & CV --> BAND{"P / LP?"}
    BAND -- yes --> ACT["actionable"]
    BAND -- no --> NON["non-actionable"]
```

Change categories used by re-evaluation: **upgrade** (into P/LP), **downgrade** (out of
P/LP), **within actionable** (LP → P), **within non-actionable** (VUS → LB), unchanged.

## 7. Treatment rules D1–D6 (patients with diabetes)

```mermaid
flowchart TD
    V["Classified variant"] --> G{"Gene"}
    G -- GCK --> GP{"P/LP?"}
    GP -- yes --> D1{"On any glucose-lowering drug?"}
    D1 -- yes --> A1["D1 · medium<br/>medication usually not needed<br/>(pregnancy caveat)"]
    D1 -- no --> N1["info note: nothing to stop"]
    G -- HNF1A --> HP{"P/LP?"}
    HP -- yes --> D2{"insulin or metformin,<br/>no sulfonylurea?"}
    D2 -- yes --> A2["D2 · high<br/>consider low-dose sulfonylurea"]
    D2 -- no --> N2["info note: already on sulfonylurea …"]
    G -- HNF4A --> D3["D3 · medium (same logic as D2)"]
    G -- "KCNJ11 (v2: + ABCC8)" --> D4{"activating P/LP, on insulin,<br/>diagnosed before 6 months?"}
    D4 -- yes --> A4["D4 · high<br/>refer for high-dose sulfonylurea"]
    D4 -- no --> N4["info note with the reason"]
    GP & HP --> D5["D5 · low<br/>cascade testing of relatives<br/>(any panel gene, P/LP)"]
    G -. "VUS in GCK / HNF1A / HNF4A" .-> D6["D6 · info<br/>monitored for reclassification"]
```

## 8. Genetic risk (people without diabetes)

Relatives offered testing (rule D5) get a risk level instead of treatment advice.

```mermaid
flowchart TD
    V["Variant of a person<br/>without diabetes"] --> C{"Classification"}
    C -- "P/LP in HNF1A · HNF4A" --> G1["G1 · HIGH<br/>glucose checks, counselling<br/>(+ HNF4A pregnancy note for women)"]
    C -- "P/LP in HNF1B" --> G2["G2 · HIGH<br/>+ kidney check"]
    C -- "P/LP in KCNJ11 · ABCC8 · INS" --> G3["G3 · HIGH<br/>specialist referral"]
    C -- "P/LP in GCK" --> G4["G4 · MILD<br/>lifelong mild, stable<br/>(+ GCK pregnancy note)"]
    C -- "VUS (v1)<br/>VUS or CONFLICT (v2)" --> F{"v2: population frequency<br/>≥ 5 %? (BA1)"}
    F -- no --> G5["G5 · UNCERTAIN (info note)"]
    F -- yes --> G6
    C -- "benign · other · not classified" --> G6["G6 · NORMAL (no finding)"]
    G1 & G2 & G3 & G4 & G5 --> L["Person's level = most serious finding<br/>high › mild › uncertain › normal"]
```

| Version | Status in the demo | What it does |
|---|---|---|
| v1 | current | CONFLICT counts as normal (healthy genomes carry 11–20 common conflicting variants) |
| v2 | pending → `flask apply-risk-levels 2` | CONFLICT counts as uncertain again, but common variants (≥ 5 %) are ignored |

## 9. Re-evaluation when knowledge changes

The paper's main contribution (F5). Only people a change can affect are re-checked.

```mermaid
flowchart TD
    S(["flask reevaluate 2026-10-19"]) --> D["Classify every STORED variant key<br/>under the old and the new release"]
    D --> CH["Δ = keys whose class changed"]
    CH --> P{"For each carrier"}
    P -- "patient with diabetes" --> R1{"Can a treatment rule match<br/>the old or the new class? (DD23)"}
    P -- "no diabetes" --> R2{"Does the risk finding differ?<br/>class + population frequency (DD47)"}
    R1 -- no --> SKIP["no work"]
    R2 -- no --> SKIP
    R1 -- yes --> EV["Re-run rules for that person"]
    R2 -- yes --> EV
    EV --> UPG{"Category"}
    UPG -- upgrade --> NEW["new alert · reclassified · HIGH priority"]
    UPG -- downgrade --> SUP["old alert superseded + REVIEW NOTICE<br/>(therapy never changed automatically)"]
    UPG -- "within band" --> PROV["same alert · provenance appended"]
    NEW & SUP & PROV --> DONE["new release becomes current · run logged"]
```

Revisions use the same idea:

```mermaid
flowchart LR
    RR(["flask apply-rule D4 2"]) --> RS["patients with diabetes carrying a variant<br/>the old OR new rule can match"] --> RE["re-run · notify only on change"]
    RL(["flask apply-risk-levels 2"]) --> RQ["people without diabetes whose finding<br/>differs between the versions"] --> RE
```

## 10. Life of an alert

```mermaid
stateDiagram-v2
    [*] --> active: rule or risk finding fires
    active --> reviewed: clinician clicks "Mark reviewed"
    active --> superseded: therapy changed / variant downgraded / rule revised
    reviewed --> superseded: later re-evaluation
    superseded --> [*]: kept forever in History
    note right of active
        kinds: alert · info note · review notice
        severity: high · medium · low · info
        priority: high when caused by a reclassification
        provenance: every release, rule and risk-levels
        version it was confirmed under (append-only)
    end note
```

## 11. Family tree

```mermaid
flowchart TB
    subgraph I["I · Parents"]
        F["□ father<br/>not tested"] --- M["○ mother<br/>not tested"]
    end
    subgraph II["II · Patient & siblings"]
        S1["○ sibling<br/>HIGH · carrier"]
        S2["□ sibling<br/>NORMAL"]
        PR["■ PATIENT (P)<br/>diabetes"] --- PA["○ partner<br/>not tested"]
    end
    subgraph III["III · Children"]
        C1["○ child · HIGH · carrier"]
        C2["□ child · HIGH · carrier"]
        C3["□ child · NORMAL"]
    end
    F --> S1 & S2 & PR
    PR --> C1 & C2 & C3
```

Features: pedigree symbols, age inside each symbol, risk-coloured rings and tints (clinical),
carrier badges, glowing inheritance paths to carriers, generation bands, and a **ClinVar
timeline with Replay history** that recomputes everyone's level for each release.

## 12. Data model

```mermaid
erDiagram
    USER ||--o{ AUDIT_EVENT : "acts"
    PATIENT ||--o{ DIAGNOSIS : has
    PATIENT ||--o{ MEDICATION : takes
    PATIENT ||--o{ CONDITION : has
    PATIENT ||--o{ VCF_UPLOAD : uploads
    PATIENT ||--o{ PATIENT_VARIANT : carries
    PATIENT ||--o{ PATIENT : "relative of"
    PATIENT ||--o{ ALERT : receives
    PATIENT_VARIANT ||--o{ ALERT : "about"
    ALERT ||--o{ ALERT_PROVENANCE : "confirmed under"
    KB_RELEASE ||--o{ KNOWN_VARIANT : contains
    KB_RELEASE ||--o{ ALERT_PROVENANCE : ""
    RULE ||--o{ ALERT : "from"
    REEVALUATION_RUN ||--o{ ALERT : creates
    PATIENT {
        string external_id
        bool has_diabetes
        int relative_of_id
        string relation
        bool is_active
    }
    KNOWN_VARIANT {
        string chrom_pos_ref_alt
        string classification
        float allele_frequency
    }
    EXPERT_CLASSIFICATION {
        string clinvar_id
        string classification
        date approval_date
    }
    RULE {
        string code
        int version
        bool is_current
    }
    RISK_LEVEL_SET {
        int version
        text content
        bool is_current
    }
    ALERT {
        string kind
        string rule_code
        string severity
        string priority
        string status
        bool reclassified
    }
    ALERT_PROVENANCE {
        int release_id
        int rule_id
        int risk_levels_version
        string classification
    }
```

## 13. Data pipeline and datasets

```mermaid
flowchart LR
    RAW["data/raw<br/>never modified"] --> C1["C1 manifest · checksums"]
    C1 --> CV["C2–C8, C15 ClinVar<br/>panel genes, CLNSIG mapping,<br/>review stars, frequencies"]
    C1 --> ER["C9–C11 ClinGen eRepo<br/>link by ClinVar ID or HGVS"]
    C1 --> SY["C12–C14 Synthea<br/>therapy periods, deceased, age at diagnosis"]
    CV & ER & SY --> CLEAN["data/clean + QUALITY_REPORT.md"]
    CLEAN --> KB["flask load-knowledge"]
    COH["ge_ehr_test_data_v2<br/>33 patients · S1–S12 · RS1–RS7"] --> SEED["flask seed-synthea"]
    REL["ge_ehr_healthy_relatives<br/>24 relatives · expected levels"] --> SEED2["flask seed-relatives"]
    SCALE["10,000-patient derived cohort"] --> BENCH["eval/benchmark.py"]
```

## 14. Commands

```mermaid
mindmap
  root((flask))
    Setup
      init-db
      show-config
      create-user
    Knowledge
      load-knowledge
      import-release
      import-expert
      import-rules
      import-flags
      import-risk-levels
      list-knowledge
      kb-diff
    Re-evaluation
      reevaluate
      apply-rule
      apply-risk-levels
      runs
    Demo data
      seed-synthea
      seed-cohort
      seed-relatives
```

## 15. Docker start-up

```mermaid
flowchart TD
    UP(["docker compose up --build"]) --> BUILD["Build: install, run cleaning pipeline,<br/>copy app, data, cohorts"]
    BUILD --> KEY["SECRET_KEY: environment or instance/secret_key<br/>(generated once, kept in the volume)"]
    KEY --> MIG["flask db upgrade"]
    MIG --> Q{"Empty database?"}
    Q -- yes --> LK["load-knowledge<br/>(ClinVar 2023 current, rules v1, risk levels v1 + v2 pending)"]
    LK --> SS["seed-synthea · 90 patients + 33 VCFs"]
    SS --> SR["seed-relatives · 24 healthy relatives"]
    SR --> RE["reevaluate 2026-10-19<br/>(REEVALUATE_TO)"]
    Q -- no --> LK2["load-knowledge (adds only what is missing)<br/>+ seed-relatives if missing"]
    RE & LK2 --> DEMO["demo accounts (once) → instance/demo_accounts.txt"]
    DEMO --> RUN["gunicorn on :5000 · healthcheck /health"]
```

## 16. Evaluation

```mermaid
flowchart LR
    CH["4 knowledge changes"] --> A["alert-once<br/>(baseline)"]
    CH --> B["full recompute"]
    CH --> C["GE-EHR change-driven"]
    A & B & C --> O["compare with an oracle:<br/>sensitivity, stale alerts, work, time"]
    O --> T["eval/results/SUMMARY.md · figures"]
```

| 10,000 patients | GE-EHR | Full recompute | Missed / stale |
|---|---|---|---|
| Real ClinVar update | 1,031 patients · 29.6 s | 7,301 patients · 88.6 s | 0 / 0 |
| Rule revision D4 v2 | 20 patients · 3.8 s | 7,301 patients · 71.9 s | 0 / 0 |

## 17. Security

```mermaid
flowchart LR
    REQ["Request"] --> AUTH{"Logged in?"}
    AUTH -- no --> LOGIN["→ /login (only same-site 'next')"]
    AUTH -- yes --> ROLE{"Role allowed?"}
    ROLE -- no --> F403["403 Not allowed"]
    ROLE -- yes --> CSRF{"POST? CSRF token valid?"}
    CSRF -- no --> F400["400"]
    CSRF -- yes --> OK["Handle + audit log"]
    OK --> HDR["Headers: CSP 'self', no framing,<br/>nosniff, no-store on pages"]
```

- Passwords are salted hashes; login errors never reveal whether a user exists.
- Demo login exists only with `DEMO_LOGIN=1` and only for `demo_*` accounts.
- Every login, upload, medication change, review and knowledge import is audited.
- Uploaded files are processed from a temporary file and deleted at once.

## 18. How it was built

```mermaid
timeline
    title GE-EHR stages
    Stage 0 : project, config, tests, lint
    Stage 1a : data cleaning C1–C14
    Stage 1b : data model, knowledge import
    Stage 2 : VCF upload, classification, rules D1–D6
    Stage 3 : re-evaluation (Algorithms 2 and 3)
    Stage 4 : website, roles, FHIR
    Stage 5 : 10,000-patient evaluation, Docker
    Stage 6 : coverage, figures, demo guide
    Extras : redesign, home page, demo login
    Stage 7 : healthy relatives, genetic risk, risk levels v2, family tree
```

Design decisions DD1–DD48 are in `docs/DEVELOPMENT_PLAN.md`; the demo script is
`docs/DEMO.md`; the website guide is `docs/GE-EHR_Website_Guide.docx`.
