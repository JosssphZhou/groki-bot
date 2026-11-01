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

## Mac mini continuation (2026-10-28)

The checkout and all 58 transfer-manifest hashes matched the handoff. The
running device still identified the `820b654` firmware. The existing local
counting server was reused; its connection counter counts accepted WebSocket
upgrades, while its error list only records playback-job exceptions. Neither
an empty error list nor a single connected snapshot establishes continuity.
An uncommanded connection-counter increment was observed before new serial
capture began. The historical server log has no timestamps or close reasons,
so its cause remains undetermined; no firewall change was made.

### Completed local audio measurements

- All four existing host regression suites passed again on the Mac mini.
- A separate 600-second serial/counter capture had one intentional initial
  USB reset, no later reconnect, and 9,646 additional microphone packets.
  It had no watchdog, allocation, speaker-I2S, assertion or panic markers.
- A subsequent independent **1,800.13-second** run completed startup, about
  14.8 minutes of uninterrupted listening, 50 muted playback/listening turns,
  three controlled session closes, two turns at 100% volume, and about 9.7
  further minutes of listening. Each turn delivered 44 Opus frames; all 50
  repeated turns resumed uplink with 24–26 packets in the observation window.
  The three recoveries took 4.16, 4.17 and 4.17 seconds. Final audio metrics
  reported zero evicted uplink chunks.
- The 30-minute run had no unexpected reconnect or runtime reset, and no
  watchdog, allocation, I2S, assertion, panic or brownout markers. Its sole
  ROM boot marker was the deliberate initial USB reset. Transport-error logs
  corresponded to the three explicitly requested close-code-1012 events.
- Across 169 heap samples, internal low-water was **5,759 bytes**, lower than
  the earlier 6,243-byte acceptance result. Pre-stress listening began and
  ended with a five-sample median of 13,611 internal-free bytes; the final
  listening segment began and ended with a median of 12,903. Its final five
  samples were 12,903 / 12,903 / 12,775 / 12,903 / 12,903 bytes. Largest
  internal and DMA blocks were 6,144 and 3,072 bytes respectively. Final
  listening PSRAM medians were both 4,582,332 bytes. There is a step between
  phases, but no continuing per-turn/recovery decline in this bounded run.
- The user was away from the device. The two audible turns have protocol
  and playback-metric evidence only; human hearing acceptance is still open.

These are independent bounded runs, not a single hours-long acceptance.
Microphone packets were counted and discarded, never saved or forwarded.
Raw evidence and the migration receipt remain in private ignored `.scratch/`.

### Enabled-servo HTTP startup failure and candidate fix

After the user confirmed calibration and physical clearance, enabling the
servos exposed a new failure before the planned servo soak could start.
Both yaw and pitch acknowledged their startup pings and microphone uplink
continued, but the HTTP server failed to allocate its 12,288-byte internal
task stack (`ESP_ERR_HTTPD_TASK`). One captured boot had 21,467 internal-free
bytes with a largest block of 8,704; a second controlled reproduction had
21,503 free with a largest block of 9,216. Delaying the existing probe server
did not avoid the failure; that server was resumed afterward.

The servo task previously requested an 8 KiB internal stack. The candidate
fix puts that stack in PSRAM when external task stacks are enabled, preserving
the internal path on other configurations. Its bus-initialization failure
uses `vTaskDeleteWithCaps`, and task-creation failure is now logged rather
than silently ignored. There is no internal fallback after a failed PSRAM
allocation. Scheduling, stack size, servo motion, audio parameters, watchdog
settings and the HTTP stack size remain unchanged. The HTTP stack must stay
internal because its handlers perform NVS/flash operations; the servo loop
performs UART/motion work. The pinned
[IDF 5.5.4 configuration](https://github.com/espressif/esp-idf/blob/v5.5.4/components/freertos/Kconfig#L588)
allows external stacks on this ESP32-S3 configuration.

The new host harness executes the production allocation function and bus
failure exit. Its bounded contiguous-headroom model fails on the original
source and passes with the change. It covers allocation success/failure and
paired deletion across PSRAM-enabled, external-stacks-disabled and no-PSRAM
configurations. All five host suites pass. This model does not emulate the
full ESP-IDF allocator; the candidate still requires a firmware build and
device validation before acceptance.

HTTP could not restore the setting because its task never started. Recovery
used the firmware's existing encrypted BLE configuration protocol, verified
the device identity, staged only `servo-enabled=0`, and applied a deliberate
restart. HTTP returned, the firmware remained `820b654`, volume read back
100%, and every staged-domain setting matched the pre-servo-test snapshot.
No flash write or NVS erase was used for this recovery. The attempted servo
soak is **not** a completed or passing run. Formal cloud/Grok/TLS testing was
explicitly deferred by the user. CI was not queried in this continuation;
no push, merge, or publication has been performed.

### Longer uninterrupted listening exposed PCM queue loss

After BLE restoration, a separate **1,801.03-second** capture kept one
connection after its deliberate initial reset: 29,594 uplink packets, no
unexpected disconnect, runtime reboot, watchdog, allocation or I2S failure.
The observed interval with confirmed uplink progress was 1,784.81 seconds
(startup is included in the capture duration). Internal-free memory stayed
at a five-sample median of 13,607 bytes, with a 6,483-byte low-water mark.
PSRAM initially stayed around 4,582,364 bytes, then fell toward 4,457,340
and partially recovered before the capture ended.

A separate muted playback immediately afterward, without opening serial or
restarting the session, exposed **221 cumulative evicted PCM chunks** in
that same session. Each chunk is 40 ms, so about 8.84 seconds of input was
discarded. The existing 96-slot PCM queue accounts for about 123 KiB of
bounded occupancy; the PSRAM pattern is consistent with that backlog but
does not by itself establish leak freedom. This run passes connection/crash
checks but **does not pass loss-free microphone acceptance**. The earlier
short-turn results remain valid within their recorded scope.

The servo candidate built successfully on native macOS arm64 with IDF
5.5.4: static heap headroom 127,425 bytes (floor 118,784), and the same 17
dependency versions and partition table as the transferred build. Its
enabled-servo acceptance was postponed to investigate the uplink loss.
A temporary `175271e-uplink-profile` application adds one timing/queue
summary per 100 encoded frames without changing CPU frequency, codec or
scheduling. Only the application at 0x10000 was written and hash-verified;
NVS, partition table and model partitions were preserved. This diagnostic
build is not a final accepted firmware.

### CPU budget comparison and a separate transport interruption

The first 600.79-second timing capture had 98 complete 100-frame windows:
mean encode time 50.85 ms and send time 5.11 ms (both include preemption).
Eleven windows exceeded 60 ms when the existing 2 ms sender rest was added;
queue depth reached 24 chunks, then drained. That shorter capture had no
evictions or unexpected reconnect, illustrating why a short pass does not
cover the earlier 30-minute loss.

The next candidate increases the render rest floor from 20 to 60 ms only
while the conversation is in its actual `Listening` state and drawing has
already overrun the frame target. Standby uses an Idle face even while
streaming microphone audio, so this condition reads the conversation status,
not the face's voice state. Other render states retain their existing pacing.
CPU frequency, codec settings, sender priority and its 2 ms rest are unchanged.
Rate-limited PCM eviction messages are promoted from debug to warning so
ordinary serial captures can detect data loss during uninterrupted listening.

In a separate 600.52-second diagnostic comparison, mean encode time fell to
39.73 ms; normal send windows were about 4 ms and queue depth was 0–2 chunks.
Rendering was approximately 5 fps instead of 6 fps. However, around device
time 391 seconds, a send took 3.007 seconds, the PCM queue hit its 96-slot
limit, an eviction warning appeared, and the device recovered a new session.
This was **not a passing connection test**. The final profile window's
eviction count was zero because reconnect resets that counter; the earlier
warning proves at least one chunk was lost and must not be hidden by the reset.

The Mac mini's `.119` address belongs to Wi-Fi, while its route to the device
uses Ethernet. The existing counting service was healthy and an active
`caffeinate` assertion covered the interruption. This does not establish the
network root cause. A further single-variable comparison changes only the
device probe URL to the same server's Ethernet address `.11`; all other
saved setting keys, 100% volume and disabled servos were verified unchanged.
No firewall or host-interface setting was changed.

The uninstrumented `175271e-audio-servo-fix` candidate has been built and
archived privately; all five host suites pass (5.724 seconds), and static
heap headroom remains 127,425 bytes. At this point it has not yet completed
long hardware or enabled-servo acceptance. The private acceptance controller
now fails on PCM eviction warnings and nonzero per-session eviction metrics,
as well as codec failures and the prior watchdog/allocation/I2S checks.

The wired-endpoint timing comparison then completed **600.76 seconds** with
no unexpected reconnect or PCM eviction. Across 97 complete windows, mean
encode/send times were 40.40/4.11 ms, maximum send time was 497.75 ms, and
queue depth peaked at 13 chunks before draining. No window's mean encode +
send + 2 ms rest exceeded 60 ms. This bounded improvement does not prove
that the Wi-Fi-address interruption's root cause was the host interface.
The final uninstrumented candidate was subsequently app-flashed and
hash-verified; a separate one-hour local acceptance run is in progress.

### Uninstrumented one-hour local acceptance

The `175271e-audio-servo-fix` native build completed a separate
**3,600.13-second** run on the Ethernet probe endpoint with servos disabled.
Readiness took 18.47 seconds; the initial listening phase then continued
without interruption until test time 2,700 seconds (about 44.7 minutes of
confirmed continuous listening). Fifty muted turns each delivered 44 frames
and resumed uplink with 23–26 packets in the observation window. Three
explicit close-code-1012 events recovered in **4.16 / 3.14 / 4.16 seconds**.
Two further turns completed at 100% volume, followed by about 9.7 minutes
of listening. Both per-session eviction snapshots were zero, and no PCM
eviction warning appeared anywhere in the capture.

There was no unexpected reconnect, runtime reset, watchdog, allocation,
speaker-I2S, codec, assertion, panic, event-queue overflow or brownout marker.
The sole ROM boot marker was the deliberate initial serial reset. Transport
errors corresponded to the three controlled closes. Across 356 heap samples,
internal low-water was **6,323 bytes**. Before playback, first/last five-sample
internal-free medians were both 13,631 bytes; after stress they were both
12,923, with all 57 final-listening samples at that value. The last sample
had a largest internal block of 6,656 and a largest DMA block of 2,816 bytes.
PSRAM first/last medians were 4,582,336 / 4,582,380 before stress and
4,582,336 / 4,583,652 afterward; transient queue occupancy recovered.
No continuing decline was observed within these listening phases.

The final source also passed all five host suites on macOS (5.724 seconds)
and Linux arm64 (1.282 seconds), native IDF 5.5.4 compilation and official
IDF 5.5.4 Docker compilation. Both maps had 127,425 bytes of static heap
headroom above the 118,784-byte floor. The first Docker configure attempt
lacked Node; its failure evidence was retained, and a separate retry used
the Node installation required by the repository's release workflow. These
are local checks, not GitHub CI. The Docker binary was not flashed; device
evidence corresponds to the separately archived native binary and ELF.

Human hearing acceptance remains open because the user is away. This is a
bounded one-hour local result; cloud/TLS and single-run multi-hour behavior
are not established. Enabled-servo acceptance follows separately.

### Enabled-servo acceptance and final restored state

The same uninstrumented native binary then completed an independent
**2,700.20-second** enabled-servo run. Both the configuration-Apply boot and
the capture's deliberate initial reset returned a working HTTP service;
the yaw and pitch startup pings succeeded. Readiness took 14.41 seconds,
followed by about 29.8 minutes of uninterrupted listening. Fifty muted turns
each delivered 44 frames and resumed upload with 22–26 packets in the
observation window. Three controlled closes recovered in
**4.17 / 3.13 / 3.11 seconds**; two 100%-volume turns and about 9.7 further
minutes of listening followed. The normal audio guard continued to pause
servo motion during playback; it was not disabled for this test.

There were no unexpected reconnects or runtime resets, PCM evictions,
watchdog, allocation, I2S, codec, assertion, panic, event-queue overflow or
brownout markers. Its sole ROM boot marker was the intentional initial
capture reset. Across 267 heap samples, the pre-stress internal-free
five-sample medians were both 13,163 bytes, and the final-listening medians
were both 12,467 bytes. Internal low-water was **5,683 bytes**; the last
sample's largest internal/DMA blocks were 5,632 / 3,072 bytes. PSRAM
pre-stress medians were 4,572,544 / 4,572,588 bytes and final medians were
4,572,576 / 4,572,572. The final five PSRAM samples were all 4,572,572 bytes.
No continuing decline was observed within these listening phases. Captured
servo-task stack headroom was 6,004 bytes. Internal-memory margin remains
tight despite these passing bounded tests.

After the test, HTTP Apply deliberately restarted the device with the
saved servo master switch restored to **off**. The M5 base initializes its
servo power rail off and does not start the servo task in that state. Final
readback confirmed firmware `175271e-audio-servo-fix`, volume **100%**, HTTP
availability and local microphone upload. All saved setting keys matched
the original snapshot except the deliberately changed local probe URL
(`.119` Wi-Fi address to `.11` Ethernet address). The original counting
server process was retained throughout; microphone packets were discarded,
not recorded or forwarded. NVS, partition table, model data and original
private firmware backups were preserved. Three application-only flashes
(two diagnostics and the final native candidate) were hash-verified.

These final **one-hour and separate 45-minute** runs passed on the tested
local Ethernet endpoint. They do not establish a single multi-hour run,
human hearing quality, cloud/TLS behavior, or the root cause of the earlier
Wi-Fi-address transport interruptions. The earlier failures remain part of
the evidence; no firewall setting was changed. Formal cloud configuration
remains deferred by the user.

Hardware validation used `fix/cores3-audio-memory` at base commit `175271e`
plus the local source changes documented here. The tested binary is identified
by the private binary/ELF/source-hash manifest and an ignored `version.txt`
override; a subsequent source commit does not change that firmware identity.
The pre-existing M5Unified patch remains applied. At completion of hardware
validation, these changes had not been committed, pushed, merged or published,
and current GitHub CI/PR/issue state was not queried.
The private migration receipt and each independent result are retained
under `.scratch/handoff-mac-mini/` and `.scratch/mac-mini-investigation/`.
