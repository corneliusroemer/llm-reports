# Sony WH-1000XM5: how the app's settings map to A2DP codec negotiation

**Date:** 2026-09-10
**Hardware:** Google Pixel 10 Pro XL (`mustang`), Sony WH-1000XM5 firmware **2.5.1**
**Method:** live `adb` observation of the Android Bluetooth stack while toggling settings in the Sony Sound Connect app
**Status:** LLM-generated. Categorical findings reproduced multiple times; one scalar claim was made and then retracted mid-session (see [Negative results](#negative-results-a-claim-i-made-and-retracted)).

---

## Summary

1. **Sony's "Bluetooth streaming quality" setting is an LDAC on/off switch, not a bitrate control.** It changes which AVDTP stream endpoints (SEPs) the headset advertises. "Priority on stable connection" removes the LDAC endpoint entirely; the phone then falls back to AAC.
2. **Sony's documented claim that multipoint disables LDAC is false on firmware 2.5.1.** LDAC was advertised, negotiated and actively streamed with "Connect to 2 devices simultaneously" enabled *and* a second device (a Mac) genuinely connected — verified after a full A2DP teardown and fresh endpoint discovery.
3. **There is no phone-side workaround.** Android's Developer Options codec picker can only choose among endpoints the headset advertises. The gate is entirely headset-side.
4. **A perverse consequence:** "Priority on stable connection" lands you on AAC, which on Android has no congestion-driven bitrate fallback, whereas "Priority on sound quality" gives you LDAC in adaptive-bitrate mode. The setting named for stability selects the *less* adaptive transport.

---

## Method

### Reading the negotiated state

```bash
adb shell dumpsys bluetooth_manager
```

The useful fields:

| Field | Meaning |
|---|---|
| `mCodecsSelectableCapabilities` | codecs the **headset advertised** (the SEP list) |
| `Number of SEPs` | count of advertised endpoints |
| `Current Codec` | what was actually negotiated |
| `A2DP LDAC State:` block | `quality mode` (ABR/fixed), `transmission bitrate`, `adaptive bit rate encode quality mode index`, `adjustments` counter |
| `Streaming:` | whether a stream is running |
| `ACL BR/EDR:Y` lines | which devices hold a live classic link **to the phone** |

### Gotcha: stale records

`mCodecsSelectableCapabilities` appears **multiple times** in the dump. The `A2dpStateMachine` keeps a history of past `CODEC_CONFIG_CHANGED` events, and each carries its own copy from when it happened. A naive `grep -m1` returns a stale list — this produced one wrong reading during the session.

Extract from the *current* state machine block only:

```bash
adb shell dumpsys bluetooth_manager \
  | awk '/=== A2dpStateMachine for/,/StateMachine: name=/' \
  | grep -E '^      \{codecName' | grep -oE 'codecName:[A-Za-z0-9-]+'
```

### Continuous sampling

Toggling headset settings causes a disconnect/reconnect, and human confirmation lags the event. Polling every 6 s and correlating afterwards proved far more reliable than synchronous hand-offs. See [`sample_codec.sh`](sample_codec.sh); output in [`codec_timeline.txt`](codec_timeline.txt).

---

## The mechanism

A2DP codec selection is a negotiation:

1. The **sink** (headset) advertises a set of Stream End Points, one per supported codec.
2. The **source** (phone) picks by its own priority order.

This phone's local priorities:

```
LHDCv5  5002
LDAC    5001
aptX-HD 4001
aptX    3001
AAC     2001
Opus    1501
SBC     1001
```

with `optional_codecs_enabled: true` for the headset. So the phone will always take the best endpoint offered. **Everything therefore hinges on what the headset advertises**, which is what the Sony app controls.

The XM5 supports SBC, AAC and LDAC only — no aptX (Sony dropped it on this model), and the phone's LHDCv5 support is irrelevant.

---

## Findings

### 1. "Bluetooth streaming quality" gates LDAC

| Multipoint | Mode | Advertised SEPs | Negotiated |
|---|---|---|---|
| ON | Stable connection | AAC, SBC (2) | AAC 44.1 kHz / 16-bit |
| ON | Sound quality | **LDAC**, AAC, SBC (3) | LDAC 96 kHz / 32-bit, ABR |
| OFF | Sound quality | **LDAC**, AAC, SBC (3) | LDAC 96 kHz / 32-bit, ABR |
| OFF | Stable connection | AAC, SBC (2) | AAC 44.1 kHz / 16-bit |

Rows 3 and 4 differ **only** by the mode, with multipoint held OFF. Transition captured live:

```
15:20:02 | SEPs=LDAC+AAC+SBC | cur=LDAC (96000) | 660 kbps
15:20:08 | SEPs=LDAC+AAC+SBC | cur=?            |   <- reconnect
15:20:14 | SEPs=AAC+SBC      | cur=AAC (44100)  |   <- LDAC gone
```

The endpoint count drops 3 → 2. This is a categorical, reproducible observation.

### 2. Sony documented this once, then stopped

The 2016 MDR-1000X help guide is explicit:

> **Priority on sound quality:** "SBC, AAC, aptX, or LDAC is selected automatically."
> **Priority on stable connection:** uses **"SBC"** codec only.

The WH-1000XM4, WH-1000XM5 and WF-1000XM4 "About the sound quality mode" pages all now say only *"Prioritizes the sound quality"* / *"Prioritizes the stable connection"*, with **no codec statement at all**.

The behaviour also changed: on the XM5, stable-connection mode does **not** force SBC-only. AAC remained advertised and was selected. So neither the old documentation nor the new documentation describes what the hardware does.

### 3. LDAC and multipoint coexist (contradicting Sony's help guide)

Sony's multipoint page states:

> "When [Connect to 2 devices simultaneously] is turned on with the 'Sony | Headphones Connect' app, LDAC cannot be used. The codec is automatically switched to AAC or SBC."

*(exact wording from the WH-1000XM4 page; the same sentence is indexed for the XM5 page)*

**This is false on firmware 2.5.1.** Two levels of evidence:

**(a) Streaming with both devices connected.** Multipoint enabled, a Mac connected to the headset, audio playing from the phone:

```
15:50:50 | SEPs=LDAC+AAC+SBC | cur=LDAC (96000) | 660 | streaming=false   <- Mac had audio
15:50:56 | SEPs=LDAC+AAC+SBC | cur=LDAC (96000) | 492 | streaming=true    <- switched to phone
15:51:15 | SEPs=LDAC+AAC+SBC | cur=LDAC (96000) | 660 | streaming=true
```

**(b) Fresh endpoint discovery in the two-device state.** The objection to (a) is that the SEP list might be cached from before the Mac attached — AVDTP discovery only reruns on a new connection, not on stream start. So the headset was disconnected from the phone with the Mac still attached, then reconnected:

```
15:53:08 | SEPs=none                                        <- A2DP fully torn down
15:53:14 | SEPs=LDAC+AAC+SBC | cur=LDAC (96000) | 660       <- fresh discovery
15:53:21 | SEPs=LDAC+AAC+SBC | cur=LDAC (96000) | streaming=true
```

`SEPs=none` confirms the endpoint list was destroyed and rebuilt from scratch. LDAC came back.

**Caveat:** the phone cannot observe the headset's *other* link, so "the Mac was still connected during the reconnect" rests on the operator's report, not on instrumented evidence.

### 4. No phone-side workaround

Developer Options → "Bluetooth audio codec" only selects among advertised endpoints. When the headset advertises AAC+SBC, LDAC is not selectable at any setting. `optional_codecs_enabled` was already `true`. Nothing on the Android side can change the outcome.

---

## Negative results (a claim I made and retracted)

Mid-session I claimed that multipoint **costs LDAC bitrate** — based on 990 kbps observed with the Mac absent versus 492–660 kbps with it connected, at constant scan configuration.

**A direct A/B disproved this.** Disconnecting the Mac while streaming:

```
15:53:33 | 660 | idx 1     <- Mac connected
15:53:39 | 660 | idx 1
--- Mac switched off ~15:53:47 ---
15:53:45 | 396 | idx 4     <- went DOWN
15:53:58 | 492 | idx 3
15:54:10 | 492 | idx 3
```

The rate **fell** when the second device went away.

**Conclusion: LDAC ABR bitrate is dominated by ambient RF variability.** It ranged 396–990 kbps within minutes with nothing under our control changing. Any causal claim about it — multipoint, BLE scan load, anything — requires an A/B/A/B design over several cycles comparing *distributions*, never two spot readings.

This also retracts an earlier claim from the same session that reducing background BLE scan load doubled achievable LDAC bitrate. It may be true; the evidence gathered does not support it.

**Methodological lesson.** Two kinds of observation, very different reliability:

- **Categorical** (is LDAC in the SEP list?) — binary, reproducible, robust. All findings above rest on this.
- **Scalar** (ABR bitrate) — noisy enough that single readings are near-meaningless.

---

## Practical recommendations

For a WH-1000XM5 on Android:

- **"Priority on sound quality" is the better default even if you care mainly about stability.** It gives LDAC with adaptive bitrate, which degrades gracefully under congestion. "Priority on stable connection" gives AAC, whose Android encoder lacks an equivalent congestion-driven fallback and tends to drop frames — heard as stutter rather than as reduced quality.
- **You can keep multipoint and still get LDAC** on firmware 2.5.1, contrary to the documentation.
- **If you genuinely need maximum dropout resistance**, force **SBC** in Developer Options. AOSP's SBC encoder reduces bitpool when the transmit queue backs up. *(Confidence: general knowledge of the AOSP stack, not measured here.)*

---

## Limitations

- Single headset, single phone, single firmware version (2.5.1). Behaviour may differ on other firmware or other XM-series models.
- The headset's own link state (whether the Mac was attached) is not observable from the phone.
- One test run ended with the headset battery flat; a near-empty battery may itself affect link behaviour, so data from that window was discarded.
- Timings correlate operator-reported timestamps against a 6-second sampler, giving up to ~6 s of attribution slack around each transition.
- AAC's lack of congestion adaptation is asserted from general knowledge of AOSP, not measured.

## Files

- [`sample_codec.sh`](sample_codec.sh) — the polling script
- [`codec_timeline.txt`](codec_timeline.txt) — raw sampler output for the whole session
