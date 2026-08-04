import subprocess
from unittest.mock import MagicMock, patch

from recon.discover import harness


class _FakeProc:
    """A Popen stand-in: raises TimeoutExpired `stalls` times, then exits."""
    def __init__(self, stalls: int, output: bytes):
        self._stalls = stalls
        self._output = output
        self._exited = False
        self.pid = 4321

    def wait(self, timeout=None):
        if self._stalls > 0:
            self._stalls -= 1
            raise subprocess.TimeoutExpired(cmd="katana", timeout=timeout)
        self._exited = True
        return 0

    def poll(self):
        return 0 if self._exited else None


def test_run_crawl_beats_then_returns_output(tmp_path):
    proc = _FakeProc(stalls=2, output=b'{"request":{"endpoint":"https://acme.io/a.js"}}\n')
    tmpfile = tmp_path / "out"
    tmpfile.write_bytes(proc._output)
    beats = []
    redis = MagicMock()
    with patch("recon.discover.harness.subprocess.Popen", return_value=proc), \
         patch("recon.discover.harness.tempfile.TemporaryFile", return_value=open(tmpfile, "rb")), \
         patch("recon.discover.harness.progress.beat", side_effect=lambda *a, **k: beats.append(k)):
        result = harness.run_crawl(
            redis, ["katana"], tenant_id="t", run_id="r", job_id="j",
            duration_seconds=100.0, kill_grace_seconds=5.0,
            heartbeat_interval_seconds=0.01, max_output_bytes=1 << 20,
        )
    assert result.timed_out is False
    assert b"a.js" in result.stdout
    assert len(beats) == 2  # one per stall tick


def test_run_crawl_kills_group_on_backstop(tmp_path):
    proc = _FakeProc(stalls=1000, output=b"")   # never exits on its own
    tmpfile = tmp_path / "out"; tmpfile.write_bytes(b"")
    killed = []
    redis = MagicMock()
    with patch("recon.discover.harness.subprocess.Popen", return_value=proc), \
         patch("recon.discover.harness.tempfile.TemporaryFile", return_value=open(tmpfile, "rb")), \
         patch("recon.discover.harness.progress.beat"), \
         patch("recon.discover.harness.os.getpgid", return_value=4321, create=True), \
         patch("recon.discover.harness.os.killpg", side_effect=lambda *a: killed.append(a), create=True):
        result = harness.run_crawl(
            redis, ["katana"], tenant_id="t", run_id="r", job_id="j",
            duration_seconds=0.0, kill_grace_seconds=0.0,
            heartbeat_interval_seconds=0.01, max_output_bytes=1 << 20,
        )
    assert result.timed_out is True
    assert killed  # os.killpg was called on the backstop
