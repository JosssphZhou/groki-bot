# SPDX-FileCopyrightText: 2026 Joseph Zhou
# SPDX-License-Identifier: BSL-1.0

"""Exercise the production voice DB partition initialization against IDF API semantics."""

from pathlib import Path
import os
import subprocess
import tempfile
import unittest


class VoicePartitionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[2]
        cls.work = tempfile.TemporaryDirectory(prefix="groki-voice-partition-")
        cls.addClassCleanup(cls.work.cleanup)
        work = Path(cls.work.name)
        source = (root / "main/voice_db.cpp").read_text()
        start = source.index("bool ensure_partition() {")
        end = source.index("\n}\n", start) + 2
        (work / "voice_partition.inc").write_text(source[start:end])
        cls.binary = work / "voice_partition_test"
        subprocess.run(
            [os.environ.get("CXX", "c++"), "-std=c++20", "-I", str(work),
             str(root / "tools/tests/voice_partition_harness.cpp"), "-o", str(cls.binary)],
            check=True,
        )

    def test_shared_partition_and_first_initialization(self):
        for case in ("loaded", "custom-first", "nvs-first", "missing"):
            with self.subTest(case=case):
                result = subprocess.run([str(self.binary), case], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
