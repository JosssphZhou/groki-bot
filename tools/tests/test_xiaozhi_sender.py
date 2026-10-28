# SPDX-FileCopyrightText: 2026 Joseph Zhou
# SPDX-License-Identifier: BSL-1.0

"""Run the production PCM queue/accumulator loops with a continuously ready producer."""

from pathlib import Path
import os
import subprocess
import tempfile
import unittest


class XiaoZhiSenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[2]
        cls.work = tempfile.TemporaryDirectory(prefix="groki-xiaozhi-sender-")
        cls.addClassCleanup(cls.work.cleanup)
        work = Path(cls.work.name)
        source = (root / "components/conversation/xiaozhi_client.cpp").read_text()
        start = source.index("    void sender_loop()")
        end = source.index("    // Encodes one 60 ms frame", start)
        (work / "xiaozhi_sender.inc").write_text(source[start:end])
        cls.binary = work / "xiaozhi_sender_test"
        subprocess.run(
            [os.environ.get("CXX", "c++"), "-std=c++20", "-I", str(work),
             str(root / "tools/tests/xiaozhi_sender_harness.cpp"), "-o", str(cls.binary)],
            check=True,
        )

    def test_backlog_partial_frame_and_disconnect(self):
        for case in ("backlog", "partial", "disconnected"):
            with self.subTest(case=case):
                result = subprocess.run([str(self.binary), case], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
