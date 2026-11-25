# SPDX-FileCopyrightText: 2026 Joseph Zhou
# SPDX-License-Identifier: BSL-1.0

"""Execute the firmware's say worker without hardware or FreeRTOS.

Only the task factory is extracted: the rest of settings_sinks.cpp wires BLE,
HTTP and board settings. The callback under test is copied verbatim, not
reimplemented. A task-delete stub checks live allocations before terminating
the process, so it cannot accidentally unwind objects like a thrown exception.
"""

from pathlib import Path
import os
import subprocess
import tempfile
import unittest


class SayWorkerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[2]
        cls.work = tempfile.TemporaryDirectory(prefix="groki-say-worker-")
        cls.addClassCleanup(cls.work.cleanup)
        work = Path(cls.work.name)
        source = (root / "main/settings_sinks.cpp").read_text()
        start = source.index("void start_say_worker(")
        end = source.index("// Render the last-turn", start)
        (work / "worker.inc").write_text(source[start:end])
        cls.binary = work / "say_worker_test"
        subprocess.run(
            [os.environ.get("CXX", "c++"), "-std=c++20", "-I", str(work),
             str(root / "tools/tests/say_worker_harness.cpp"), "-o", str(cls.binary)],
            check=True,
        )

    def test_all_task_exit_paths_release_owned_memory(self):
        for case in ("success", "invalid", "synth-fail", "empty", "play-fail", "create-fail"):
            with self.subTest(case=case):
                result = subprocess.run([str(self.binary), case], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
