#!/usr/bin/env bash
# Verify ENA LIBRARY_SOURCE findings for viral raw-read submissions.
# Requires: curl
set -euo pipefail

echo "### 1. Spot-check specific accessions ###"
echo

check_run() {
  local acc=$1 note=$2
  echo "-- $acc ($note) --"
  curl -s "https://www.ebi.ac.uk/ena/portal/api/filereport?accession=${acc}&result=read_run&fields=run_accession,library_source,library_strategy,library_selection,instrument_platform&format=tsv"
  echo
}

# Vitor Borges' mpox submissions (via Pathoplexus) -> shotgun, METAGENOMIC
check_run ERR14334743 "Borges mpox, expect METAGENOMIC/WGS/RANDOM"
check_run ERR14673163 "Borges mpox, expect METAGENOMIC/WGS/RANDOM"

# Amplicon-tiled RNA virus -> VIRAL RNA
check_run SRR37517698 "RSV-A amplicon, expect VIRAL RNA/AMPLICON/PCR"

# DNA virus tagged VIRAL RNA despite being DNA (shows the convention is sloppy)
check_run ERR10019299 "Mpox tagged VIRAL RNA via amplicon pipeline"

echo
echo "### 2. Bulk counts: LIBRARY_SOURCE distribution per virus ###"
echo

declare -A TAXA=(
  [SARS-CoV-2]=2697049
  [Influenza_A]=11320
  [Zika]=64320
  [Monkeypox]=10244
  [HepatitisB]=10407
  [Variola]=10255
)
SOURCES=("GENOMIC" "VIRAL RNA" "METAGENOMIC" "TRANSCRIPTOMIC" "METATRANSCRIPTOMIC" "SYNTHETIC" "OTHER")

ena_count() {
  curl -s --get "https://www.ebi.ac.uk/ena/portal/api/count" \
    --data-urlencode "result=read_run" \
    --data-urlencode "query=$1"
}

for name in "${!TAXA[@]}"; do
  taxid=${TAXA[$name]}
  total=$(ena_count "tax_eq(${taxid})")
  echo "== $name (taxid $taxid), total runs: $total =="
  for src in "${SOURCES[@]}"; do
    n=$(ena_count "tax_eq(${taxid}) AND library_source=\"${src}\"")
    [ "$n" != "0" ] && printf "  %-20s %s\n" "$src" "$n"
  done
  echo
done

echo "### 3. Pull Pathoplexus mpox metadata with raw-read accessions ###"
echo
curl -s "https://lapis.pathoplexus.org/mpox/sample/details?fields=accession,authors,insdcRawReadsAccession&dataFormat=tsv&limit=20" \
  || echo "(LAPIS endpoint/organism slug may need adjusting - verify against https://pathoplexus.org)"
