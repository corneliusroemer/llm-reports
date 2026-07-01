# ENA `LIBRARY_SOURCE` for viral raw reads — findings

**Date:** 2026-06-30
**Question:** What value should be used for the ENA/SRA controlled-vocabulary field
`LIBRARY_SOURCE` (allowed values: `GENOMIC`, `GENOMIC SINGLE CELL`, `TRANSCRIPTOMIC`,
`TRANSCRIPTOMIC SINGLE CELL`, `METAGENOMIC`, `METATRANSCRIPTOMIC`, `SYNTHETIC`,
`VIRAL RNA`, `OTHER`) when submitting virus raw reads, especially for DNA viruses
like mpox?

**Verify everything below yourself:** `verify.sh` in this directory re-runs every
query against the live ENA Portal API and Pathoplexus LAPIS API (no auth needed).

## Bottom line

**There is no fixed organism→LIBRARY_SOURCE mapping.** The field tracks the wet-lab
**sequencing protocol**, not whether the virus genome is RNA or DNA:

| Protocol | Typical `LIBRARY_SOURCE` | Typical `LIBRARY_STRATEGY` / `LIBRARY_SELECTION` |
|---|---|---|
| Tiled-amplicon / PCR-tiling (ARTIC-style) | `VIRAL RNA` (used by convention even for DNA viruses) | `AMPLICON` / `PCR` |
| Shotgun / metagenomic WGS, random priming | `METAGENOMIC` | `WGS` / `RANDOM` |
| Shotgun WGS of isolated/purified virus | `GENOMIC` | `WGS` / `RANDOM` or non-random |
| Hybrid capture / targeted enrichment | `METAGENOMIC` | `Targeted-Capture` / `Hybrid Selection` |

`VIRAL RNA`'s INSDC schema definition is literally just "Viral RNA" — there is no
documented restriction to RNA-genome viruses. In practice it gets reused for DNA
viruses too whenever an amplicon-tiling pipeline (often adapted from SARS-CoV-2
protocols) is used.

## Evidence 1 — Pathoplexus raw-read accessions → ENA lookup

Pulled raw-read accessions (`insdcRawReadsAccession`) from Pathoplexus LAPIS for
mpox, West Nile, RSV-A/B, Dengue, Measles, CCHF, then looked up each accession's
actual `LIBRARY_SOURCE`/`STRATEGY`/`SELECTION` on the ENA Portal API.

| Organism | Accession | LIBRARY_SOURCE | STRATEGY | SELECTION | Platform |
|---|---|---|---|---|---|
| mpox (Vitor Borges) | ERR14334743 | METAGENOMIC | WGS | RANDOM | Illumina NextSeq 2000 |
| mpox (Vitor Borges) | ERR14673163 | METAGENOMIC | WGS | RANDOM | Illumina NextSeq 550 |
| mpox (other) | SRR24016847 | METAGENOMIC | WGS | RANDOM | — |
| mpox (other) | SRR22110860 | METAGENOMIC | WGS | RANDOM | — |
| West Nile (Borges) | ERR17072037 | METAGENOMIC | WGS | other | Illumina MiSeq |
| West Nile (other) | SRR3218229 | GENOMIC | WGS | RANDOM | — |
| West Nile (other) | SRR32328383 | VIRAL RNA | AMPLICON | PCR | — |
| RSV-A | SRR37517698 | VIRAL RNA | AMPLICON | PCR | Nanopore GridION |
| RSV-B | SRR28689755 | VIRAL RNA | AMPLICON | PCR | Nanopore GridION |
| Dengue | SRR35515481 | METAGENOMIC | WGS | RANDOM | Illumina NextSeq 1000 |
| Measles | SRR38861900 | METAGENOMIC | Targeted-Capture | Hybrid Selection | Illumina NovaSeq 6000 |
| CCHF | SRR21504617 | METAGENOMIC | WGS | RANDOM | Nanopore GridION |

**Key takeaway:** Vitor Borges' own mpox (and West Nile) submissions consistently
use `METAGENOMIC` because they're shotgun sequencing, not amplicon. mpox being a
DNA virus didn't drive the choice — the protocol did.

## Evidence 2 — ENA Portal API bulk counts across 6 viruses

Queried `https://www.ebi.ac.uk/ena/portal/api/count` with
`tax_eq(<taxid>) AND library_source="<value>"` for RNA and DNA viruses.

| Virus | Genome | Total runs | GENOMIC | VIRAL RNA | METAGENOMIC | other |
|---|---|---|---|---|---|---|
| SARS-CoV-2 | RNA | 7,545,265 | 3.2% | **96.1%** | 0.1% | 0.6% |
| Influenza A | RNA | 49,130 | 2.8% | **91.7%** | 0.9% | 4.6% |
| Zika | RNA | 3,865 | 13.5% | **67.7%** | 10.0% | 8.8% |
| **Monkeypox** | **dsDNA** | 5,195 | **47.7%** | 11.2% | **40.0%** | 1.1% |
| **Hepatitis B** | **dsDNA(RT)** | 3,591 | **61.5%** | 20.9% | 3.0% | 14.6% |
| **Variola** | **dsDNA** | 54 | 0% | 0% | **100%** | 0% |

**Key takeaway:** RNA viruses overwhelmingly use `VIRAL RNA` (68–96%). DNA viruses
split mostly between `GENOMIC` and `METAGENOMIC`, but a non-trivial minority
(11% mpox, 21% HepB) are still tagged `VIRAL RNA` — almost always paired with
`LIBRARY_STRATEGY=AMPLICON`, suggesting submitters copy SARS-CoV-2-style amplicon
submission templates regardless of actual nucleic-acid chemistry.

Example DNA-virus runs tagged `VIRAL RNA` despite being DNA: `ERR10019299`,
`ERR10094836` (mpox); `ERR13617596`, `ERR13617598` (Hepatitis B) — all
`AMPLICON`/`PCR`.

## Evidence 3 — Documentation / spec wording

- INSDC `SRA.experiment.xsd` (mirrored at ENA: https://ena-docs.readthedocs.io/en/latest/submit/reads/webin-cli.html)
  defines the enum with bare, circular descriptions:
  - `GENOMIC` = "Genomic DNA (includes PCR products from genomic DNA)"
  - `VIRAL RNA` = "Viral RNA"
- **No clause anywhere restricts `VIRAL RNA` to RNA-genome viruses.** The
  ambiguity is real, not just submitter confusion — the spec simply doesn't say.
- No ENA/NCBI page, ARTIC mpox guide, or PHA4GE/CIDGOH Mpox Contextual Data
  Specification was found stating an explicit rule for DNA viruses.

## Recommendation

Pick based on **what you actually did in the lab**, not the organism:

- **Amplicon/PCR-tiling protocol** (ARTIC-like primer panels), any virus:
  `VIRAL RNA` is the de facto community standard (even for DNA viruses), though
  `GENOMIC` is arguably more technically correct if your input material was DNA.
- **Shotgun/metagenomic sequencing** of a clinical/environmental sample with host
  background: `METAGENOMIC` — this is what Borges' own mpox/West Nile submissions
  use, and the only thing Variola submissions use.
  - Use `LIBRARY_STRATEGY=WGS`, `LIBRARY_SELECTION=RANDOM` alongside it.
- **Shotgun WGS of purified/isolated virus** (e.g., cultured virus, not from a
  clinical sample): `GENOMIC`.
- **Hybrid-capture/targeted enrichment**: `METAGENOMIC` +
  `LIBRARY_STRATEGY=Targeted-Capture`.

## Files in this directory

- `verify.sh` — re-runs all ENA Portal API and Pathoplexus LAPIS queries live.
- `verify_output.txt` — captured output from the last run (2026-06-30).
- `ppx_raw_reads_ena_stats.py` — general-purpose script: takes any Pathoplexus
  organism slug, pulls every latest-version sample with a linked
  `insdcRawReadsAccession` via LAPIS, batch-queries the ENA Portal API for
  run-level metadata (`library_source`, `library_strategy`, `library_selection`,
  instrument, read/base counts), and writes a joined TSV (Pathoplexus sample
  metadata × ENA read_run metadata), one row per run accession.

  ```bash
  python3 ppx_raw_reads_ena_stats.py mpox -o mpox_raw_reads.tsv
  ```

  Full-scale run for mpox (1,513 run accessions across 1,498 samples) confirms
  the pattern from Evidence 1/2 at scale:
  `GENOMIC` 663, `METAGENOMIC` 635, `VIRAL RNA` 193, no ENA record found 22.
