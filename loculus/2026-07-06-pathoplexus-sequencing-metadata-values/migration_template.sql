-- DRAFT migration template for PR loculus-project/loculus#6634
-- Purpose: bring existing Pathoplexus sequence_entries.submitted_data metadata values
-- for sequencingInstrument / sequencingAssayType into conformance with the new
-- ENA-permitted-value enums before the PR's `process_options` validation goes live.
--
-- DO NOT RUN AS-IS AGAINST PRODUCTION. This is a reviewable draft:
--   1. Confirm the value mapping (see enum_migration_mapping.tsv) with whoever owns
--      the CSVs referenced in the PR's linked Slack thread - avoid a second,
--      diverging taxonomy.
--   2. Run each UPDATE inside a transaction against a staging/restore copy first,
--      inspect the diff, only then apply to production.
--   3. After updating submitted_data, entries need to be reprocessed (revision /
--      preprocessing re-run) for the corrected values to take effect - updating the
--      column alone does not re-trigger validation.
--
-- Scope note: rows where the raw value only differs from a valid option by case or
-- whitespace (e.g. "Illumina Miseq", "MinIon", "Gridion", "illumina") do NOT need a
-- SQL fix - process_options() in
-- preprocessing/nextclade/src/loculus_preprocessing/processing_functions.py
-- lowercases and collapses whitespace before matching, so those resolve automatically
-- on reprocessing. Only the "high"/"medium"/"review" rows in
-- enum_migration_mapping.tsv need this kind of UPDATE.

BEGIN;

-- Example pattern, repeat per (field, raw_value, proposed_value) row from
-- enum_migration_mapping.tsv. Restrict to the organisms actually affected
-- (see the `organisms` column) to limit blast radius per statement.

-- 1) Simple rename / ontology-suffix-strip cases with no information loss
--    (confidence = high, preserve_detail_to_sequencingProtocol column blank):
UPDATE sequence_entries
SET submitted_data = jsonb_set(
    submitted_data,
    '{metadata,sequencingInstrument}',
    to_jsonb('NextSeq 1000'::text)
)
WHERE organism IN ('mpox', 'rsv-a', 'rsv-b')
  AND submitted_data #>> '{metadata,sequencingInstrument}' = 'Illumina NextSeq 1000 [GENEPIO:0004432]';

-- 1b) Rename cases where the raw value carries detail the new enum can't hold
--     (e.g. a chip/model qualifier like "Mk1C"/"Mk1D", or an ambiguous generic
--     name). Set the enum field to the mapped value AND append the lost detail
--     to sequencingProtocol - via coalesce/append, never overwrite, since most
--     of these rows already have real protocol text (verified: e.g. 153/153
--     rsv-a "GridION Mk1" rows already have non-empty sequencingProtocol, vs.
--     229/229 mpox "Snager 3500" rows and 85/85 dengue "Illumina NextSeq" rows
--     where it's empty - so this must work in both cases):
UPDATE sequence_entries
SET submitted_data = jsonb_set(
    jsonb_set(
      submitted_data,
      '{metadata,sequencingInstrument}',
      to_jsonb('GridION'::text)
    ),
    '{metadata,sequencingProtocol}',
    to_jsonb(
      trim(both from
        coalesce(submitted_data #>> '{metadata,sequencingProtocol}', '')
        || CASE WHEN submitted_data #>> '{metadata,sequencingProtocol}' IS NULL
                  OR submitted_data #>> '{metadata,sequencingProtocol}' = ''
             THEN '' ELSE E'\n' END
        || 'Chip: GridION Mk1'
      )
    )
)
WHERE organism IN ('rsv-a', 'rsv-b')
  AND submitted_data #>> '{metadata,sequencingInstrument}'
      = 'Oxford Nanopore GridION Mk1 [OBI:0002751]';

-- 2) Placeholder / junk values -> NULL (e.g. "None None"):
UPDATE sequence_entries
SET submitted_data = submitted_data #- '{metadata,sequencingInstrument}'
WHERE organism IN ('rsv-a', 'rsv-b')
  AND submitted_data #>> '{metadata,sequencingInstrument}' = 'None None';

-- 3) Multi-instrument / free-text values: move the original text to
--    sequencingProtocol (append, don't clobber any existing protocol text) and
--    clear sequencingInstrument:
UPDATE sequence_entries
SET submitted_data = jsonb_set(
    submitted_data #- '{metadata,sequencingInstrument}',
    '{metadata,sequencingProtocol}',
    to_jsonb(
      trim(both from
        coalesce(submitted_data #>> '{metadata,sequencingProtocol}', '')
        || CASE WHEN submitted_data #>> '{metadata,sequencingProtocol}' IS NULL THEN '' ELSE ' ' END
        || 'Instrument (raw): ' || (submitted_data #>> '{metadata,sequencingInstrument}')
      )
    )
)
WHERE organism = 'andv'
  AND submitted_data #>> '{metadata,sequencingInstrument}'
      = 'Nanopore Minion, Illumina NextSeq 2000 and Element BioSciences Aviti';

-- Repeat blocks 1/1b/2/3 for every row in enum_migration_mapping.tsv, driven from
-- that file rather than hand-copied: generate the UPDATE statements with a small
-- script that reads the TSV and emits one statement per raw_value, routed by
-- `confidence` (high/medium -> direct swap; review -> hold for manual sign-off)
-- and by whether `preserve_detail_to_sequencingProtocol` is non-blank (pattern 1b
-- append, vs. plain pattern 1 rename). "review" rows should be held out of the
-- automated batch and applied individually after manual sign-off.

-- After review:
-- COMMIT;
ROLLBACK; -- placeholder so this file is never accidentally applied as a no-op COMMIT
