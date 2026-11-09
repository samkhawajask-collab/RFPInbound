# ADR-0014 — Dataverse schema is generated from a spec, applied to dev, and reconciled by unpack

- **Status:** Accepted
- **Date:** 2026-10-28
- **Deciders:** Solution architecture
- **Related:** [ADR-0011](0011-solution-source-layout-and-regeneration.md), [ADR-0007](0007-gold-data-isolation-by-architecture-not-by-prompt.md)

## Context

Slice 0 creates 22 tables (11 reference, 11 runtime), 22 global choices, about 40 lookups,
alternate keys, 5 environment variable definitions and 5 connection references. ADR-0011 fixes the
**layout** of `solution/src/` (classic XML) but not **how** the table definitions get there. The
generator must be idempotent and byte-stable, and must attach every component to the solution.

Two facts constrain the design:

1. **The primary key column is always `<table logical name>id`.** That's verified on Microsoft
   Learn: *Table definitions*, "By default, all tables include a single GUID unique identifier
   column named `<table logical name>Id`" (checked 2026-10-28). In four reference tables, the
   dataset's business-key column has exactly that name: `hrs_sme.hrs_smeid`,
   `hrs_answer.hrs_answerid`, `hrs_opportunity.hrs_opportunityid` and
   `hrs_casestudy.hrs_casestudyid`. Those can't be created as text columns, so assumption A10
   ("nothing is renamed") can't hold for them.
2. **Some business keys can't be used in alternate-key URLs.** Learn says GET, PATCH and upsert
   don't work when a key value contains `/ < > * % & : \ ? +`. The insurance keys
   "Professional Liability (E&O)" and "Umbrella / Excess Liability" contain `&` and `/`.

## Options considered

### Option A — Hand-write the entity XML in `customizations.xml`

- **For:** `solution/src/` is written first and never touched by the platform, which matches
  hard rule 2 most literally.
- **Against:** Entity XML for 22 tables is thousands of lines, with platform-generated detail
  (system attributes, forms, views, relationship metadata) that a generator would have to imitate.
  Mistakes there tend to import partially or fail quietly. It's the most fragile choice.

### Option B — Spec → Web API metadata calls in dev → export + unpack *(chosen)*

- **For:** The reviewed artifact is a small JSON spec (`solution/spec/tables.json`). The platform
  generates its own XML, so the unpacked source is exactly what Dataverse would produce. Every call
  carries `MSCRM.SolutionUniqueName`, so nothing orphans (ADR-0011 rule 3). Re-running is safe,
  because the apply script creates only what's missing and reports drift on the rest.
- **Against:** Dev gets the change before `solution/src/` does, so there's a short window where
  the environment is ahead of git. That's the same situation as a portal change, and it's closed
  the same way, by exporting and unpacking in the same session.

### Option C — The YAML source-control format with `pac solution sync`

- **Against:** ADR-0011 already rejected the YAML layout for this repo.

## Decision

**Option B.**

1. `solution/spec/tables.json` is the generator input and the place reviews happen. Choice values
   are explicit and **append-only** (publisher option prefix 43120, so values are `431200000+`).
2. `scripts/apply-schema.ps1` applies the spec to **dev only**. It creates whatever is missing,
   never deletes or retypes anything, and exits 2 on drift. Then it publishes only this solution's
   tables and choices; it never calls `PublishAllXml`, because dev is shared with other solutions.
3. `scripts/sync-solution.ps1` exports `RFPFactory` (unmanaged) from dev and unpacks it into
   `solution/src/`. From then on `solution/src/` is what gets packed, checked and imported (hard
   rule 2), and reaches test as a managed import.
4. **Business-key renames.** The four clashing columns are renamed. Each renamed column becomes
   its table's primary name column, and the spec records the original CSV header in a `csv` field:

   | Table | Dataset column | Dataverse column |
   |---|---|---|
   | `hrs_sme` | `hrs_smeid` | `hrs_smecode` |
   | `hrs_answer` | `hrs_answerid` | `hrs_answercode` |
   | `hrs_opportunity` | `hrs_opportunityid` | `hrs_opportunitycode` |
   | `hrs_casestudy` | `hrs_casestudyid` | `hrs_casestudycode` |

   In every other reference table, the dataset's key column is the primary name column under its
   original name.
5. **Seeding doesn't use alternate-key URLs.** `scripts/seed-data.ps1` finds each row with a
   `$filter` on the business key and then PATCHes it by GUID, or POSTs it if it's missing. This
   sidesteps the reserved-character limit in fact 2. It runs in two passes, scalars first and then
   lookups, because `01_hrs_category.csv` references `02_hrs_sme.csv` and `hrs_answer` references
   itself.
6. **Ownership.** Reference tables are organization-owned (read-mostly company data). Runtime tables
   are user/team-owned, so the §11 service-identity security role can scope them.
7. **Column choices §5.4 didn't specify, recorded here:**
   - `hrs_rfp.hrs_stage` and `hrs_runlog.hrs_stage` are whole numbers 0–9.
   - The runtime `hrs_status` columns get their own choices: `hrs_runstatus`, `hrs_stagestatus`,
     `hrs_requirementstatus`, `hrs_clarificationstatus`. `hrs_gap.hrs_smestatus` uses
     `hrs_smestatus`, and `hrs_approval.hrs_decision` uses `hrs_approvaldecision`.
   - `hrs_flag.hrs_resolvedby` is text.
   - `hrs_rfpdocument` belongs to `hrs_rfprevision`, per the §5.2 diagram.
   - Timestamps use User Local behavior; dataset dates are Date Only.
   - `hrs_certification.hrs_statusasof20261027` uses a `hrs_certstatus` choice and stays
     informational (§5.3).

## Consequences

- Anything that reads the reference tables by column name (the seed script, `eval/` and later
  flows) uses the Dataverse names. The `csv` mapping in the spec is the single translation point.
- Schema changes follow one loop: edit the spec, apply to dev, sync, pack, check, import. A
  hand edit to `solution/src/Entities/` would be overwritten by the next sync, so schema changes go
  through the spec.
- The evaluation tables (`hrs_evalrun`, `hrs_evalresult`, `hrs_evalgoldrequirement`) are **not** in
  this spec. They belong to a separate `RFPFactoryEval` solution, built in Slice 2 and imported to
  test only (§10.3 layer 2).
