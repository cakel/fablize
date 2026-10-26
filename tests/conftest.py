"""pytest runs every suite in ONE process, so a suite that sets os.environ
(FABLIZE_DATA, FABLIZE_HOLDOUT=1) leaked into every later suite's hook
subprocesses. As scripts each suite gets a fresh process; this restores that."""

import os

import pytest


@pytest.fixture(autouse=True)
def _isolated_environ():
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)
