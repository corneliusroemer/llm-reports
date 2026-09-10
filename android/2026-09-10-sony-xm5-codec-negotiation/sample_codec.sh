#!/bin/bash
# Poll A2DP codec state so toggles are captured regardless of message timing.
OUT=/Users/cr/.claude/jobs/ae8e67a2/tmp/codec_timeline.txt
END=$((SECONDS + 3000))
while [ $SECONDS -lt $END ]; do
  D=$(adb shell dumpsys bluetooth_manager 2>/dev/null)
  TS=$(date +%H:%M:%S)
  SEP=$(printf '%s' "$D" | awk '/=== A2dpStateMachine for/,/StateMachine: name=/' \
        | grep -E '^      \{codecName' | grep -oE 'codecName:[A-Za-z0-9-]+' \
        | cut -d: -f2 | paste -sd+ -)
  CUR=$(printf '%s' "$D" | grep -m1 'Current Codec:' | awk '{print $3}')
  LD=$(printf '%s' "$D" | awk '/^A2DP LDAC State:/,/^A2DP AptX-HD/' \
        | grep -E 'transmission bitrate|encode quality mode index|adjustments' \
        | grep -oE '[0-9]+$' | paste -sd/ -)
  RATE=$(printf '%s' "$D" | awk '/=== A2dpStateMachine for/,/StateMachine: name=/' \
         | grep -m1 'mCodecConfig:' | grep -oE '\([0-9]{4,6}\)' | head -1)
  STREAM=$(printf '%s' "$D" | grep -m1 'Streaming:' | awk '{print $2}')
  echo "$TS | SEPs=${SEP:-none} | cur=${CUR:-?} ${RATE} | ldac(kbps/idx/adj)=${LD:-n/a} | streaming=${STREAM}" >> "$OUT"
  sleep 6
done
