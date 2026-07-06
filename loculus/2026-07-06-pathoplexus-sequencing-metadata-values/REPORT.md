# Pathoplexus sequencing metadata field values (2026-07-06)

## Task

Look at all existing values present for `sequencingInstrument`, `sequencingAssayType`,
and `sequencingLibrarySelection` across all organisms on Pathoplexus, using the LAPIS
`aggregated` endpoint.

## Method

Pathoplexus LAPIS is at `https://lapis.pathoplexus.org/<organism>/sample/aggregated`.
Organism keys (14 total, from pathoplexus.org homepage config):

```
andv, cchf, dengue, ebola-bdbv, ebola-sudan, ebola-zaire, hmpv, marburg,
measles, mpox, rsv-a, rsv-b, west-nile, yellow-fever
```

For each organism x field, queried:

```
GET https://lapis.pathoplexus.org/<organism>/sample/aggregated?fields=<field>
```

Raw JSON responses saved to the session scratchpad; parsed with a small Python script
that summed counts per distinct value across organisms (excluding `null`) and recorded
which organisms contributed each value.

## Findings

- **`sequencingLibrarySelection`** is not a defined metadata field for *any* of the 14
  organisms — every request returned HTTP 400 "Unknown field." It isn't collected on
  Pathoplexus at all.
- **`sequencingInstrument`** exists as a field for all organisms but is free text with
  no controlled vocabulary. Result: heavy duplication of the same real value written
  differently (case variants, typos like `Snager 3500` for Sanger, ontology-ID suffixes
  like `[GENEPIO:0004432]`, and even a full sentence in one `andv` record). `cchf`,
  `marburg`, and `yellow-fever` have no non-null values submitted yet.
- **`sequencingAssayType`** exists for all organisms but is sparsely populated — only
  `rsv-a`, `rsv-b`, `mpox`, `hmpv`, `ebola-sudan`, and `measles` have any non-null
  values, and it's likewise free text (`WGS` vs `Whole genome sequencing assay` vs
  `whole genome sequencing assay [OBI:0002117]`).

Full distinct-value breakdown with counts and contributing organisms:
[sequencing_metadata_values.tsv](./sequencing_metadata_values.tsv)

## Conclusion

Only two of the three requested fields (`sequencingInstrument`, `sequencingAssayType`)
are populated in Pathoplexus; `sequencingLibrarySelection` isn't part of the schema for
any organism. Both existing fields suffer from lack of a controlled vocabulary /
enum, producing many near-duplicate free-text values for the same instrument or assay
type.

## Migration for loculus-project/loculus#6634

PR 6634 adds `options:` enums (backed by `process_options`) for `sequencingInstrument`
and `sequencingAssayType`, plus a brand-new `sequencingLibrarySelection` field. Once
merged, any submitted value not in the ENA-permitted-values list for these fields will
be rejected (non-INSDC-ingest submissions) or accepted with a warning (INSDC ingest).
Cloned `loculus-project/loculus` (full history) into
`scratch/loculus-ppx-metadata-migration/` to check the actual matching logic before
proposing a migration.

Key finding: `process_options` in
`preprocessing/nextclade/src/loculus_preprocessing/processing_functions.py`
(`standardize_option`) matches case-insensitively and collapses whitespace before
comparing to the option list — it does **not** strip ontology-ID suffixes, fix typos,
or handle multi-instrument text. So a chunk of the observed values (case/whitespace-only
diffs, e.g. `Illumina Miseq`, `MinIon`, `Gridion`) will resolve automatically on
reprocessing and need **no SQL migration at all**. The values that genuinely need a
data fix are ontology-ID-suffixed values (`[GENEPIO:...]`, `[OBI:...]`), typos, bare
ontology IDs, ambiguous/generic instrument names, and free-text/multi-instrument
entries.

Also confirmed metadata is not zstd-compressed at rest (only nucleotide/amino-acid
sequences are, per `CompressionService.kt`), so `submitted_data->'metadata'` values are
plain text and directly matchable/updatable via `jsonb`/`#>>` operators — the
where-in-jsonb-and-replace approach is sound.

Deliverables:
- [enum_migration_mapping.tsv](./enum_migration_mapping.tsv) — every raw value that
  needs attention, with a proposed new value and a `confidence` column
  (`auto` = resolves on reprocess, no SQL needed; `high`/`medium` = safe direct
  rename; `review` = ambiguous/multi-instrument/needs a human to confirm before
  applying).
- [migration_template.sql](./migration_template.sql) — a draft, **not executed**,
  `UPDATE ... jsonb_set(...)` template against `sequence_entries.submitted_data`
  (the live/pending table, not `archive_of_submitted_data`), covering the three
  patterns needed: direct value swap, placeholder→NULL, and multi-instrument
  free-text→moved into `sequencingProtocol`.

### Preserving precision lost in the enum mapping

Several raw values carry detail the new controlled vocabulary can't represent — chip/
model qualifiers (`GridIon MK1C`, `MinION Mk1D`), ambiguous generic names
(`Illumina NextSeq`, `Illumina NovaSeq`), or multi-instrument lists. Rather than
discard that on mapping to the enum, the plan appends it to `sequencingProtocol` (the
existing free-text field whose own guidance already asks for "sequencing instrument"
and "library kit" detail - no other empty/related field exists in the schema; checked
the full `Sequencing`-header field list in `kubernetes/loculus/values.yaml`).

Checked via LAPIS `sample/details` whether this would clobber real data: for most of
these rows `sequencingProtocol` is already non-empty with real protocol text (e.g. all
153/153 `rsv-a` "GridION Mk1" rows, all 20/20 `ebola-bdbv` "Nextseq1000/2000" rows), but
for others it's fully empty (all 229/229 mpox `Snager 3500` rows, all 85/85 dengue
`Illumina NextSeq` rows, 675/675 west-nile `Illumina NovaSeq` rows). So the migration
appends (via `coalesce` + newline, pattern 1b in `migration_template.sql`) rather than
overwrites, and costs nothing where the field was empty. `enum_migration_mapping.tsv`
now has a `preserve_detail_to_sequencingProtocol` column listing exactly what text to
append for each affected raw value.

**Before running any of this for real:**
1. The PR body links to CSV files already circulated in Slack
   (`https://loculus.slack.com/archives/C0B95CH8BEG/p1780998449855789`) proposing a
   curation mapping — check with Anna/the team whether to align with those rather than
   this independently-derived mapping, to avoid two diverging taxonomies.
2. `enum_migration_mapping.tsv` rows marked `review` are guesses (e.g. `Snager 3500` →
   `AB 3500 Genetic Analyzer`, bare `OBI:0002750`, ambiguous `Illumina NextSeq`) and
   should not be applied without sign-off from someone who knows the submission.
3. Updating `submitted_data` alone does not re-trigger preprocessing/validation —
   affected entries still need to go through a revision/reprocessing cycle for the
   fixed values to actually validate against the new enum.
