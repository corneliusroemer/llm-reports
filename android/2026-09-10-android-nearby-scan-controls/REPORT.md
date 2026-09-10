# Android background BLE scanning: what can and cannot be disabled

**Date:** 2026-09-10
**Hardware:** Google Pixel 10 Pro XL (`mustang`), stock Android, unrooted, Google Play Services
**Method:** enumerate live BLE scans via `adb shell dumpsys bluetooth_manager`, then toggle each candidate control and re-measure
**Status:** LLM-generated. Every toggle→scan mapping below was verified by measurement, not inferred from documentation.

---

## Summary

Google Play Services runs **four continuous BLE scans** on an idle Pixel. Exactly **two of them can be turned off**:

| Scan | Control | Disableable? |
|---|---|---|
| `nearby_sharing` | Quick Share → "Visible to nearby devices" | ✅ yes |
| `nearby_fast_pair` | Devices → "Scan for nearby devices" | ✅ yes |
| `nearby_presence` | — none exists — | ❌ **no** |
| `nearby_connections` | — none exists — | ❌ **no** |

`nearby_presence` and `nearby_connections` have **no user-facing toggle and no adb path** on an unrooted phone. `pm disable-user` on Play Services components throws `SecurityException`, and production builds refuse `adb root`. Only the blunt Location → Bluetooth scanning master switch reaches them, and that takes Fast Pair with it.

Notably, `nearby_connections` — driven mainly by Instant Tethering — scans continuously **even with no Chromebook linked**, and its settings page is empty, so there is nothing to switch off.

---

## Method

```bash
adb shell dumpsys bluetooth_manager
```

The `LE Scanner Map` section lists registered scanners, cumulative scan time per mode, and an `Ongoing N scans:` block naming each live scan by tag, scan mode, and match mode. That block is the measurement instrument: toggle a candidate control, re-dump, see which entry disappears.

```bash
adb shell dumpsys bluetooth_manager \
  | awk '/Ongoing [0-9]+ scans:/,/^  com\.|^Profile:/' \
  | grep -E "Ongoing [0-9]+ scans|\[nearby_|\[com\."
```

---

## Baseline: what was running

Four Play Services scans, all with `AGGRESSIVE` match mode:

| Tag | Scan mode | Filter (service UUID) | Purpose |
|---|---|---|---|
| `nearby_fast_pair` | BALANCED | `FEAA`, `FFF6`, `FE2C` | Fast Pair device discovery |
| `nearby_presence` | BALANCED | `FCF1` | Nearby Presence identity beacons |
| `nearby_sharing` | AMBIENT_DISCOVERY | `FE2C` | Quick Share receive discovery |
| `nearby_connections` | AMBIENT_DISCOVERY | `FEF3`, `FC73` | Nearby Connections API (Instant Tethering) |

Plus one third-party scan from a CGM app (`com.camdiab.fx_alert.mgdl`, BALANCED).

Cumulative Play Services scan time was **109,011,328 ms active**. The Fast Pair scan alone had been running **continuously for over three hours** and logged **32,000+ results**.

Yield varied enormously:

- `nearby_fast_pair` — 32,262 results (dense environment)
- `nearby_presence` — **1 result in over three hours**
- `nearby_connections` — **0 results, the entire session**

---

## Verified control mappings

### Quick Share → `nearby_sharing`

**Settings → Google → Devices & sharing → Quick Share → Who can share with you → "Visible to nearby devices" OFF**
(deep link: `com.google.android.gms/.nearby.sharing.main.MainActivity`, then overflow → Settings)

```
before:  Ongoing 4 scans — presence, fast_pair, sharing, connections
after:   Ongoing 3 scans — presence, fast_pair, connections
```

Confirmed. The scan exists so **others can find you to send to you**; it is not needed to send.

### Fast Pair → `nearby_fast_pair`

**Settings → Google → Devices & sharing → Devices → "Scan for nearby devices"**
(deep link: `com.google.android.gms/.nearby.discovery.devices.DevicesListActivity`)

Toggled off, measured, and restored:

```
ON  → Ongoing 3 scans — presence, fast_pair, connections
OFF → Ongoing 2 scans — presence, connections
ON  → Ongoing 3 scans — presence, fast_pair, connections
```

**This toggle gates Fast Pair only.** It does not touch Presence or Connections — an assumption worth testing rather than believing, since it looks like a generic "nearby scanning" switch.

### `nearby_presence` and `nearby_connections` → nothing

Searched the entire Play Services activity table for a settings entry point:

```bash
adb shell dumpsys package com.google.android.gms \
  | grep -oE "com\.google\.android\.gms/[A-Za-z0-9_.]*(Presence|presence|CrossDevice|DevicesAndSharing)[A-Za-z0-9_.]*"
```

The only hit was `.presencemanager.communal.activity.ConsentActivity` — a consent flow, not a scan control. No settings activity exists for either subsystem. They are internal Play Services machinery, not user-facing features.

---

## Instant Tethering scans for a device you don't have

`nearby_connections` was the noisiest component in the logs (`MagicTether`, 108 log lines — more than every other Nearby component combined). Its settings page:

> **"Your Google Pixel 10 Pro XL isn't linked to a Chromebook.** Link devices to send text messages from your computer, use your phone's internet connection, and unlock both devices more easily."

The page is otherwise empty — account picker and a "Learn more" link. **No toggle.** So the feature scans continuously to discover a Chromebook, has never found one (0 results), and offers no way to stop.

Instant Tethering only works with ChromeOS and Android tablets. On a phone paired with a Mac or Windows PC it is pure overhead with no off switch.

---

## Why adb can't help

The obvious escape hatch is disabling the component directly:

```bash
$ adb shell pm disable-user --user 0 \
    com.google.android.gms/.magictether.service.TetherService

SecurityException: Shell cannot change component state for
ComponentInfo{com.google.android.gms/…magictether.service.TetherService} to 3
    at PackageManagerService.setEnabledSettings(PackageManagerService.java:4219)
```

Play Services is a protected package; `PackageManagerService` refuses component-state changes from the shell uid. And:

```bash
$ adb root
adbd cannot run as root in production builds

$ adb shell id
uid=2000(shell) …
```

So on a locked production device there is **no path at all** — not through settings, not through adb. Only an unlocked bootloader and root would do it.

---

## What disabling Fast Pair actually costs

Worth stating precisely, because the common assumption — that headphone battery percentage comes from Fast Pair — is **wrong**.

The headset's battery level arrives over **classic HFP**, via the Apple-defined accessory battery indicator:

```
STACK_EVENT: EVENT_TYPE_UNKNOWN_AT[15],
valString=+IPHONEACCEV=1,1,0, device=…:CF:AF
```

Corroborating evidence: the LE `BatteryService` profile is empty, and the device's stored metadata has **no `MAIN_BATTERY` key**:

```
Device: …:CF:AF [Disk] {
  Custom Metadata:
   0  Sony                          (manufacturer)
   1  WH-1000XM5                    (model)
   2  2.5.1                         (firmware)
   4  com.sony.songpal.mdr          (companion app)
   5  content://…/d446a7main.png    (device image)
   6  false                         (IS_UNTETHERED_HEADSET)
   17 Headset
   20 20                            (low battery threshold)
}
```

Fast Pair **populated** that metadata at pairing time, but the record is marked `[Disk]` — it persists regardless of scanning. So:

**You do lose:** the one-tap pairing sheet for new devices; SASS (Smart Audio Source Switching, the automatic handoff between your Google-account devices); notifications about others' nearby devices.

**You do not lose:** battery percentage; the model name, firmware version and device image in Settings; existing pairings; account registration; Find Hub registration.

Note SASS only arbitrates between *Google-account devices*. In a phone-plus-Mac setup it cannot deliver its benefit, because a Mac cannot participate.

---

## Aside: Quick Share to a Mac

Quick Share on Pixel 9/10 interoperates with AirDrop, including to **Macs** — not only iPhones.

When a transfer finds no target, this log signature identifies which side is at fault:

```
NearbyMediums: No BLE Fast/GATT advertisements found in the latest cycle.   (every 10 s)
NearbySharing: Network state changed: (isOnline=true, isWifiConnected=true)
```

Scanning is running and the network is up, but nothing is being heard — the sending phone is healthy and the target is not advertising. Requirements for the target:

- **Both** ends in "Everyone" mode. macOS's default "Contacts Only" does not work cross-platform — it relies on Apple ID identity matching.
- Android side: "Everyone for 10 minutes", or the Quick Share Receive page open.
- Mac awake with the lid open, **Bluetooth and Wi-Fi both on**.
- Set the Mac to Everyone first, so the two 10-minute windows overlap.

Because "Everyone for 10 minutes" auto-expires, you can keep the default visibility at "No one" (no background scan) and flip it on only when sharing.

---

## Does reducing scans improve audio?

**No evidence either way.** The motivation for this investigation was radio contention with A2DP audio, but the obvious metric does not support conclusions.

LDAC's adaptive bitrate ranges **396–990 kbps within minutes** on a static setup with nothing under control changing. Ambient RF variability dominates, so spot comparisons before and after a scan change cannot distinguish signal from noise.

Establishing whether background scanning measurably degrades audio requires an **A/B/A/B design** over several cycles comparing distributions. Absent that, the case for disabling these scans rests on the features being useless to the user, not on a demonstrated audio benefit.

---

## Recommendations

For a Pixel paired with a Mac (no Chromebook, no Android tablet):

1. **Quick Share visibility → "No one".** Costs nothing; you can still send, and flip to "Everyone for 10 minutes" when receiving.
2. **Fast Pair scanning → off** if you rarely pair new devices. Battery reporting and all stored device metadata are unaffected.
3. **Instant Tethering / Nearby Presence** — nothing you can do. Accept them.
4. **Location → Location services → Bluetooth scanning** is the only switch that reaches all four, at the cost of Fast Pair and some indoor location accuracy.

Net achievable on this device: **4 Play Services scans → 2.**

---

## Limitations

- Single device, single Play Services version. Settings paths and available deep links change frequently between Play Services releases.
- Play Services settings are not exposed in `Settings.Secure`; all queried keys returned `null`, so state was read from the UI and verified via the scan list rather than from stored preferences.
- Scan **counts** were measured, not radio duty cycle or power draw. BALANCED and AMBIENT_DISCOVERY are lighter than LOW_LATENCY, but this report does not quantify the actual cost of any individual scan.
- No claim is made here that reducing scans improves audio or battery life; see the section above.
