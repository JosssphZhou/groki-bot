# CoreS3 audio memory follow-up

## Observations

- Base: upstream PR #26 at `c564582`, preserving its three commits, merged with
  upstream main `65838c0`. Issue #2 reports silent playback, repeated I2S DMA
  allocation failures and lost touch responsiveness after a voice connection.
- PR #26 reserves DMA memory and reduces retry frequency. Its follow-up reports
  an `ESP_ERR_NO_MEM` boot regression when the reserve competes with Wi-Fi.
- No USB serial device was connected at the start of this investigation. The
  original hardware failure has not yet been reproduced in this checkout.
- `say_worker` already allocates its 12 KiB task stack in PSRAM, but every exit
  calls `vTaskDeleteWithCaps` before automatic C++ objects leave scope. The input
  owner, decoded text and PCM buffers therefore need a lifetime check.
- PR #26 reuses one static speaker TCB/PSRAM stack after self-deletion and a
  one-tick delay. A delay does not establish that the other core's idle task has
  finished processing the deleted task.

## Hypotheses

### H1: C++ allocations survive say_worker task deletion (root hypothesis)

- Supports: task deletion is not a C++ return; owned text and vectors remain in
  scope at every deletion call, including synthesis failures.
- Conflicts: this path is local synthesized speech, not every cloud voice turn;
  it cannot alone explain all issue #2 reports.
- Experiment: execute the actual production worker callback in a host harness
  whose task-delete stub records outstanding allocations and exits without
  unwinding, just as task deletion does. Cover success and early exits.

### H2: Static speaker task storage is reused before deferred deletion finishes

- Supports: `_task_handle` is cleared before `vTaskDelete`, and static storage is
  reused on the next `begin`; the extra delay is time-based rather than a
  completion signal.
- Conflicts: no hardware trace yet attributes a failure to this race.
- Experiment: inspect the pinned ESP-IDF task-deletion implementation and check
  whether a delayed idle task can still reference the old static TCB.

### H3: The DMA reserve is insufficient or competes with the radio/microphone

- Supports: PR #26's own device report shows Wi-Fi start running out of memory;
  the reserve calculation budgets audio buffers but init also allocates driver
  metadata and synchronization objects.
- Conflicts: no current hardware measurements establish the remaining deficit.
- Experiment: build the exact patched CoreS3 firmware, inspect the pinned I2S
  allocation path, then compare serial heap/driver logs over repeated turns.

## Experiments

- H1 confirmed using the exact production `start_say_worker` callback and a
  host allocation counter. At task deletion, success and synthesis-failure
  each retained 4 allocations; invalid input retained 2; empty PCM retained 3.
  Task-creation failure correctly released its input. Playback failure also
  incorrectly published `say_done`.
- After returning from an inner C++ scope before task deletion, all five
  callback paths retain zero allocations. Creation failure still releases its
  input. Failed `playRaw` no longer publishes a completion event.
- This verifies a real leak in local TTS. It does not prove the complete cause
  of cloud-conversation DMA fragmentation or establish physical speaker output.
- H2 confirmed against ESP-IDF v5.5.4 `tasks.c`: self-deletion links the TCB's
  list item into `xTasksWaitingTermination`; idle later removes it and performs
  TLS cleanup even for statically allocated tasks. Reusing that TCB before idle
  cleanup is unsafe. A host scheduler executing PR #26's actual begin/end and
  shutdown code fails on its second turn with `static speaker TCB reused before
  idle cleanup` when idle is delayed.
- The corrected speaker uses `xTaskCreatePinnedToCoreWithCaps`, suspends after
  its last I2S access, and is deleted by `end()` using `vTaskDeleteWithCaps`.
  This also avoids allocating a temporary cleanup task during self-deletion.
  100 turns pass without idle cleanup in both external-stack and fallback
  scenarios; I2S and task-allocation failure checks preserve retry backoff and
  release the reserve lock. Hardware timing remains unverified.

## Root causes and fixes

1. Local TTS deleted its task before C++ resources could leave scope. Return
   from the synthesis/playback scope before self-deletion; report completion
   only when playback was accepted.
2. PR #26 reused static speaker task storage before deferred kernel cleanup.
   Allocate task storage per start and synchronously reclaim it from `end()`
   after worker suspension. Retain the PR's DMA reserve and failure backoff.

## Hardware baseline

- Connected after the initial investigation: ESP32-S3 revision 0.2, 16 MiB
  flash, 8 MiB PSRAM, official `stack-chan` firmware `1.6.0`.
- Passive serial capture shows a USB/UART reset and a normal startup. No flash
  write or erase command had been sent at that initial checkpoint. Raw device logs stay local because
  they may contain device/network configuration.
- Testing this project's firmware requires replacing the official firmware;
  prepare and verify a full-flash backup before any separately approved write.
- Full 16 MiB official backup verified against the device using esptool's flash
  digest. The first read/verify pair differed only in the NVS sector at `0x9000`
  after the intervening official-firmware boot. Reading again without running
  the application before verification produced a matching full-flash digest.
  Backup and raw logs are restricted to the current user and excluded from Git.

## Local verification

- `python3 -m unittest discover -s tools/tests -p 'test_*.py' -v`: passed on
  macOS and Linux. Six local-TTS exit cases; four speaker lifecycle cases with
  and without PSRAM, including 100 start/stop cycles per successful scenario.
- The committed regression harnesses also fail against the original PR #26
  source: retained TTS allocations and static TCB reuse are reproduced.
- Existing host tests: 11 CTest tests passed across avatar_vm, scs_servo, avatar,
  groki_motion and conversation; all five jtts vowel checks passed. The eight
  CTest tests configured with ASan/UBSan also passed.
- All three avatar DSL test scripts passed.
- `make build-docker BOARD=cores3`: passed using `espressif/idf:v5.5.4`,
  esp-sr 2.6.0, Quad PSRAM, and external task stacks enabled. App image size
  `0x338740` bytes; 19% of the 4 MiB OTA slot remains free.
- `tools/check_static_ram.py --board cores3`: passed with 127,425 bytes of
  static D/IRAM remaining versus the 118,784-byte floor. This is a linker
  measurement, not a runtime Wi-Fi/audio memory watermark.
- Full 16 MiB test image prepared from the generated flash manifest. It includes
  the bootloader, partition table, OTA selector, app and speech-recognition
  models. The device was subsequently flashed after explicit approval.

## Hardware verification (2026-10-27)

Firmware code commit: `d903c0f`, built with the parameters above. The connected
CoreS3/M5Base was flashed and the write digest verified. Test configuration used
Wi-Fi and the firmware's XiaoZhi WebSocket/Opus path to a local server. Servos
were disabled; microphone packets were counted and discarded, not recorded or
forwarded to any cloud service.

- Five consecutive boots with saved Wi-Fi configuration reached `ready` and
  completed `esp_wifi_start`, without allocation failures or panic. The saved
  network was initially unavailable; the current network was then configured
  privately and the device obtained an IP and established the voice session.
- Six initial audible turns completed. The user confirmed clear speech,
  suitable volume at 100%, and touch-triggered interruption; the server observed
  two device abort messages.
- A fresh continuous run completed **50 turns**, each containing 44 Opus frames
  at 24 kHz / 60 ms (2.64 seconds of audio), followed by a two-second listening
  interval. Output volume was temporarily zero for this repeated stress test,
  then restored to 100%. Every turn resumed microphone uplink; the same
  WebSocket connection remained open. Five further audible turns completed,
  including three replayed at the user's request. The user confirmed the
  post-stress replay was clearly audible.
- During the 50-turn sequence, there were no `ALLOC_FAIL`, speaker-I2S init
  failures, asserts, panic or reboot. Last-turn metrics reported 44 chunks and
  zero evicted uplink chunks.
- Runtime internal free memory fluctuated with playback/listening around
  13–20 KiB. After the final audible turns it settled at 13,147 bytes across
  four successive samples; largest internal block was 6,144 bytes and largest
  free DMA block 3,712 bytes. PSRAM free was 4,583,676 bytes in those samples.
  The observed internal low-water mark was 6,015 bytes. This remains a tight
  budget; the sequence showed no continuing per-turn decline.
- Reopening the USB serial port caused a USB/UART reset before the continuous
  run. The capture then kept the port open throughout all 50 turns. This
  operator-induced reset is excluded from runtime reboot claims.

### Watchdog observation before the follow-up

Before the first playback of the continuous run, at approximately 23.7 and
28.7 seconds after boot, `task_wdt` reported that IDLE1 had not run in time.
There was no reboot or allocation failure. Backtraces decoded against the
actual firmware ELF point to Opus uplink encoding (`encode_and_send` /
`flush_encoded_frames`) and sprite/face rendering (`render_task_entry`).
These source paths are unchanged from main. No further watchdog warning
occurred during the subsequent 50 turns and final audible checks. This does
not establish the cause or prove the warning harmless. The follow-up below
reproduces and separates the two watchdog paths; this earlier run alone was
not a clean whole-device stability result.

## Remaining acceptance

Issue #2 remains open and this PR stays a draft. The local TTS allocation leak
and speaker task-storage lifetime defects are covered by regressions, and the
real device audio sequence above passed within its stated scope. The watchdog
follow-up and its later acceptance are recorded below. Hours-long operation,
cloud/direct-TLS variants and enabled-servo load remain outside this probe.
The formal Grok voice backend was not configured.

## Startup watchdog follow-up (2026-10-27)

### Observations

- The prior continuous capture reported IDLE1 starvation at 23.659 and
  28.659 seconds, during microphone uplink before the first playback at
  30.540 seconds. Both render and XiaoZhi sender have priority 5 on core 1.
- Render already blocks for at least 20 ms after each completed frame.
  Sender drains available queue entries without an explicit blocking rest;
  queue receive only blocks when no PCM is available.
- Pinned esp_audio_codec defaults already select Opus complexity 0. The
  firmware uses 16 kHz mono / 60 ms VOIP frames, 24 kbps, PSRAM sender stack.
- No watchdog timeout or priority settings have been changed for this work.

### Hypotheses

1. **H4 (leading): combined render + encode load keeps core 1 runnable.**
   Supports: both observed CPU1 tasks run at priority 5; sender can consume
   render's sleep windows while PCM queues accumulate. Conflicts: earlier
   warnings stopped after playback began. Test: first reproduce with a
   longer listening-only baseline, then change only sender affinity to core 0
   as a diagnostic and repeat under identical server/configuration.
2. **H5: network send blocks or spins on core 1.** Supports: warnings occur
   during uplink. Conflicts: decoded traces point to codec/render, and the
   local server receives packets. Test: measure codec and send elapsed time
   independently with minimal instrumentation if the baseline reproduces.
3. **H6: transient startup work, rather than sustained audio load.** Supports:
   warnings appeared soon after session setup. Conflicts: only ~18 seconds
   of listening preceded playback, so longer continuous listening is untested.
   Test: collect 120 seconds of listening-only startup without playback.

### Planned experiment W1

Run the unchanged firmware with the same local counting-only audio server,
reset once, and capture 120 seconds of continuous listening. No microphone
content is retained. Record warning times, connection continuity, and heap.

### W1 interim result / W2 plan

Unchanged firmware reproduced periodic IDLE1 warnings at 13.556, 18.556,
23.556 and 28.556 seconds while continuously listening. W1 capture continues
for its full planned duration. This contradicts a startup-only explanation.

W2 changes exactly one line: pin only XiaoZhi sender to core 0 instead of
core 1. Build separately from the running baseline, then flash only the app
partition (preserving configuration) and repeat 120 seconds of listening.
If IDLE1 warnings cease without shifting to IDLE0 or causing packet loss,
this supports combined core-1 load as the cause. This is diagnostic code,
not yet a production fix. Baseline app and ELF have been preserved locally.

W1 completed 120 seconds: six watchdog warnings (13.556 through 38.556 s,
5-second spacing), with one connection and continuing microphone packets.
Warnings stopped later even without playback. Thus timing/startup load may
contribute, and H6 is not fully excluded; it is not an isolated single alert.

### W2 result / W3 plan

W2 is rejected as a fix: moving sender alone shifted starvation from IDLE1
to IDLE0 (warnings at 21.625, 26.625 and 31.625 s). Rendering improved to
roughly 9-10 fps, but the sender's core still missed idle. Reverted the affinity
experiment. This points to the sender's own runnable duty cycle, not just
co-location with rendering.

W3 adds three diagnostic lines around encode/send, reporting elapsed encode
and send time and queue depth every tenth frame. Affinity is back to the
baseline core 1. The pinned build runs at 160 MHz and Opus complexity 0;
no speed, codec, priority or watchdog changes are made. Expect large encode
cost and/or backlog if CPU work is responsible; large send time instead
would promote H5. Diagnostic lines will be removed after measurement.

### W3 result / W4 plan

Timing samples show encode median about 51 ms (maximum 89.5 ms) versus send
median about 5 ms (maximum 10.1 ms); wall time includes preemption. Queue
backlog grew to 37 chunks, then drained near 34 s, after which watchdog
warnings stopped. This supports busy backlog draining without a blocking
point; no multi-second network-send stall was observed. H5 is not supported
in this reproduction. The 160 MHz CPU and render work leave little headroom.

W3 instrumentation was removed. W4 changes one line in the original affinity
configuration: block for 2 ms after each processed 60 ms Opus frame. This tests
whether a small, real scheduling gap (rather than a yield to equal-priority
tasks) permits idle work under backlog without compromising audio throughput.
Watchdog timeout, codec settings and task priority stay unchanged. Repeat
continuous listening and check watchdogs, uplink continuity and audio metrics.

Scheduling interpretation was cross-checked against Espressif's
[ESP-IDF 5.5 task watchdog documentation](https://docs.espressif.com/projects/esp-idf/en/v5.5/esp32s3/api-reference/system/wdts.html):
the watched idle tasks need CPU time. Resetting the watchdog in the busy task
would not validate that idle cleanup can run, so no watchdog bypass is used.

### W4 result / refined startup hypothesis / W5 plan

W4 had no IDLE1 warning in 150 seconds, but exposed a separate IDLE0 warning
at 14.650 s. The decoded CPU0/main stack is `voice_db::ensure_partition` ->
`nvs_flash_init_partition_ptr` -> NVS PageManager/flash reads/heap scanning.
The `storage` partition is 0x5a0000 bytes. `avatar_vm::storage::load()` already
initialized this same partition earlier in app_main.

Pinned IDF 5.5.4 implementation confirms that the pointer API calls
`init_custom`, which unconditionally calls `Storage::init` even when storage
exists; this scans every page again. The by-name API first checks the loaded
storage list and returns ESP_OK without rescanning. This refines H6: the
redundant startup flash scan can both monopolize main/core0 and compete with
codec/cache work, triggering the temporary queue backlog on core1. Heavy
voice parsing is not implicated by this stack; no voice blob is installed.

W5 reverts W4's 2 ms rest and changes only the voice DB init call to the
by-name API for this diagnostic (the earlier avatar init is present on this
boot path). Repeat startup/listening. If both core warnings disappear with
the original sender, redundant initialization is the minimal fix target.
A final implementation must retain pointer initialization as a fallback
when voice_db is the first user, because existing partition tables mark
storage as spiffs rather than NVS. Do not change partition offsets or erase
user data.

### W5 final result and two confirmed paths

The complete 150-second W5 capture disproved a single-cause conclusion:
startup stayed clear, but IDLE1 warned at 140.395 and 145.395 s during normal
listening. The earlier observation of two clean minutes was only interim.
Thus skipping the redundant NVS scan fixes startup CPU0 work, but does not
bound sender CPU1 occupancy under later backlog. W4 separately eliminated
IDLE1 for its capture while leaving the NVS/main IDLE0 warning.

Final changes combine the independently tested paths: reuse registered NVS
with the first-use custom-subtype fallback, and block 2 ms after each encoded
60 ms uplink frame. No affinity/priority/watchdog/codec changes remain.
The original sender regression fails after a continuously replenished PCM
queue produces over five seconds of runnable work; tests also verify exact
sample order, partial-frame retention and disconnected-buffer discard. This
models sender scheduling, not the complete dual-core runtime, so final-device
acceptance remains necessary.

The partition regression executes the actual ensure_partition function.
Original code fails the already-loaded case; name-only diagnostic code fails
first use of a custom-subtype partition. Final fallback passes both, ordinary
NVS first use, and absent-partition handling. No partition erase/layout change.

### Local verification of the combined fix

All four host regression suites pass on macOS and Linux, including seven new
partition/sender cases. The CoreS3 Docker build succeeds with the unchanged
IDF 5.5.4 configuration. Combined-fix device acceptance is in progress;
interim clean captures are not counted as completed runs.

## Combined watchdog-fix hardware acceptance (2026-10-27)

Final code commit: `820b654`. App SHA256:
`cfb2d1d8783b2071a37a60cf8e87a76b8dd85a064db6548033d0b4925a7ae104`.
The app partition was flashed and verified; private NVS configuration was
retained. App size is `0x338750` (19% OTA headroom); static D/IRAM remains
127,425 bytes, passing the 118,784-byte floor. The patched M5Unified submodule
is intentionally dirty because the committed patch is applied during build.

- The combined source first passed a 180-second startup/listening capture.
  After committing and rebuilding, the exact final app passed another
  180-second startup/listening capture, then a separate **900-second** run.
- The long run began with seven minutes of uninterrupted listening, followed
  by 50 playback/listening turns (44 Opus frames, 2.64 seconds each). Playback
  was muted for repetition, but followed the real decoder/I2S path. Every
  turn resumed microphone upload; the connection remained unchanged through
  all 50 turns. Final metrics recorded zero evicted uplink chunks.
- Three server-initiated WebSocket closes each recovered a new session and
  microphone upload in approximately 4.1 seconds. Two further audible turns
  completed at 100% volume; user hearing feedback for this version is pending.
- All three captures had zero task-watchdog warnings, allocation failures,
  speaker-I2S init failures, assertions or panic. The 900-second capture has
  one ROM boot marker, its intentional initial reset; no runtime reboot.
- Final four internal-free samples were 13,151 bytes, with a largest internal
  block of 6,656 bytes and largest free DMA block of 3,200 bytes. The last PSRAM-free
  sample was 4,582,160 bytes; internal low-water mark was 6,243 bytes. The reserved
  speaker DMA block is separate from these free-block figures. No continuing
  per-turn or per-reconnect decline was observed in this bounded run.
- Two earlier long-test starts were excluded: the private controller first
  requested settings before HTTP readiness, then hit a Python direct-network
  routing error. It now waits for HTTP/session readiness and uses the verified
  local curl route. Those short captures had no firmware failure markers.

Raw logs and the counting-only server remain private because serial startup
contains device/network identifiers. No microphone audio was saved or sent
to cloud services. All observations above use the same device/network;
ambient microphone input was not a fixed acoustic fixture.

This resolves the reproduced watchdog paths within the stated test scope.
It does not prove hours-long stability, cloud/direct-TLS behavior or enabled-
servo load. Issue #2 and the draft PR remain open for that broader acceptance;
the internal-memory budget is still tight. No release or merge was performed.
