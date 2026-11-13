import pytest

from roofwatch.lock import run_lock


def test_second_holder_is_refused_until_release(tmp_path):
    p = tmp_path / ".lock"
    with run_lock(p):
        with pytest.raises(RuntimeError, match="another roofwatch process"):
            with run_lock(p):
                pass
    with run_lock(p):
        pass
