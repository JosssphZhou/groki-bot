# SPDX-FileCopyrightText: 2026 Joseph Zhou
# SPDX-License-Identifier: BSL-1.0

"""Exercise production servo task allocation and its bus-init failure exit."""

from pathlib import Path
import os
import subprocess
import tempfile
import unittest


class ServoTaskMemoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[2]
        cls.work = tempfile.TemporaryDirectory(prefix="groki-servo-memory-")
        cls.addClassCleanup(cls.work.cleanup)
        work = Path(cls.work.name)
        source = (root / "main/servo_task.cpp").read_text()
        start = source.index("void servo_task_entry(void* arg)")
        end = source.index("    auto bus = std::move", start)
        entry = source[start:end] + "}\n"
        start = source.index("void start_servo_task(ServoTaskArgs& args)")
        end = source.index("\n}\n", start) + 2
        (work / "servo_task_memory.inc").write_text(entry + source[start:end])
        cls.binaries = {}
        for psram, external in ((0, 0), (1, 0), (1, 1)):
            binary = work / f"servo_task_memory_{psram}_{external}"
            subprocess.run(
                [os.environ.get("CXX", "c++"), "-std=c++20",
                 f"-DCONFIG_SPIRAM={psram}",
                 f"-DCONFIG_FREERTOS_TASK_CREATE_ALLOW_EXT_MEM={external}", "-I", str(work),
                 str(root / "tools/tests/servo_task_memory_harness.cpp"), "-o", str(binary)],
                check=True,
            )
            cls.binaries[(psram, external)] = binary

    def test_http_headroom_and_task_failure_cleanup(self):
        for config, binary in self.binaries.items():
            for case in ("budget", "allocation-failure", "bus-failure"):
                with self.subTest(config=config, case=case):
                    result = subprocess.run([str(binary), case],
                                            capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
