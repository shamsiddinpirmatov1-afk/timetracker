import tempfile
from datetime import datetime
from pathlib import Path


def local_ts(*args):
    """Timestamp для локального времени: local_ts(2026, 3, 2, 9, 30)."""
    return datetime(*args).timestamp()


class FakeClock:
    """Управляемые часы, чтобы тесты не зависели от реального времени."""

    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class TempDbMixin:
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "test.db"
        self.clock = FakeClock(local_ts(2026, 3, 2, 9, 0))

    def tearDown(self):
        self._tmp.cleanup()
