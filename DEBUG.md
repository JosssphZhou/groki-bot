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

### Unresolved watchdog observation

Before the first playback of the continuous run, at approximately 23.7 and
28.7 seconds after boot, `task_wdt` reported that IDLE1 had not run in time.
There was no reboot or allocation failure. Backtraces decoded against the
actual firmware ELF point to Opus uplink encoding (`encode_and_send` /
`flush_encoded_frames`) and sprite/face rendering (`render_task_entry`).
These source paths are unchanged from main. No further watchdog warning
occurred during the subsequent 50 turns and final audible checks. This does
not establish the cause or prove the warning harmless; it is an outstanding
scheduling investigation, not a clean whole-device stability result.

## Remaining acceptance

Issue #2 remains open and this PR stays a draft. The local TTS allocation leak
and speaker task-storage lifetime defects are covered by regressions, and the
real device audio sequence above passed within its stated scope. Hours-long
operation, cloud/direct-TLS variants, enabled-servo load, and the startup
IDLE1 watchdog warnings still require follow-up. The formal Grok voice backend
was not configured by this hardware probe.
