# SPDX-FileCopyrightText: 2026 Joseph Zhou
# SPDX-License-Identifier: BSL-1.0

"""Test patched M5Unified speaker lifecycle with delayed idle cleanup.

Apply the committed patch to the pinned submodule source in a temporary
directory. Execute its actual begin/end and task-shutdown code; the mixer and
hardware I2S work are outside this host test's scope.
"""

from pathlib import Path
import os
import subprocess
import tempfile
import unittest


class SpeakerLifecycleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[2]
        cls.work = tempfile.TemporaryDirectory(prefix="groki-speaker-")
        cls.addClassCleanup(cls.work.cleanup)
        work = Path(cls.work.name)
        for name in ("src/utility/Speaker_Class.cpp", "src/utility/rtc/RTC_PowerHub_Class.cpp"):
            target = work / name
            target.parent.mkdir(parents=True, exist_ok=True)
            original = subprocess.check_output(
                ["git", "-C", str(root / "components/M5Unified"), "show", "HEAD:" + name]
            )
            target.write_bytes(original)
        subprocess.run(["git", "apply", str(root / "patches/m5unified.patch")], cwd=work, check=True)
        source = (work / "src/utility/Speaker_Class.cpp").read_text()
        start = source.index("#if !defined (SDL_h_)\n  // Weak on purpose:")
        end = source.index("\n  void Speaker_Class::stop(", start)
        methods = source[start:end]
        shutdown = source.rindex("    _i2s_stop(i2s_port);", 0, start)
        worker = (
            "void Speaker_Class::spk_task(void* arg) {\n"
            "auto* self = static_cast<Speaker_Class*>(arg);\n"
            "int i2s_port = self->_cfg.i2s_port;\n"
            "#if !defined(SDL_h_)\n" + source[shutdown:start]
        )
        (work / "speaker.inc").write_text(worker + "\n" + methods)
        cls.binaries = []
        for spiram in (0, 1):
            binary = work / f"speaker_lifecycle_test_{spiram}"
            subprocess.run(
                [os.environ.get("CXX", "c++"), "-std=c++20", f"-DTEST_SPIRAM={spiram}",
                 "-I", str(work), str(root / "tools/tests/speaker_lifecycle_harness.cpp"),
                 "-o", str(binary)],
                check=True,
            )
            cls.binaries.append(binary)

    def test_repeated_turns_and_failed_allocations(self):
        for binary in self.binaries:
            for case in ("repeated", "fallback", "i2s-fail", "task-fail"):
                with self.subTest(binary=binary.name, case=case):
                    result = subprocess.run([str(binary), case], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
