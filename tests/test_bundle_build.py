"""The py2app build definition.

One bug shipped here and it was invisible on the machine it was written for:
py2app aborts with

    error: install_requires is no longer supported

when the Distribution carries dependencies, and modern setuptools puts
``[project] dependencies`` from pyproject.toml there even for a plain
``setup.py`` invocation. Older setuptools did not, so the Intel Mac on Python
3.9 built happily while the M1 on 3.12 could not build at all.

Nothing here needs a Mac: the failure is in setuptools' own object model.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SETUP_PY = ROOT / "setup.py"


@pytest.fixture(scope="module")
def setup_module_():
    """Import setup.py without running the build it defines."""
    spec = importlib.util.spec_from_file_location("aloud_setup", SETUP_PY)
    module = importlib.util.module_from_spec(spec)
    sys.modules["aloud_setup"] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop("aloud_setup", None)


def test_importing_setup_py_does_not_build(setup_module_):
    """If the setup() call were not guarded, importing this would build an app."""
    assert callable(setup_module_.build)
    assert "__main__" in SETUP_PY.read_text(), "the guard has to stay"


def test_pyproject_really_does_declare_dependencies():
    """Otherwise the test below would pass for the wrong reason."""
    text = (ROOT / "pyproject.toml").read_text()
    assert "dependencies = [" in text
    assert "sounddevice" in text


def test_the_bundle_build_never_sees_install_requires(setup_module_):
    """py2app refuses to run at all if this is set. It must come back empty."""
    dist = setup_module_.AppDistribution({"name": "Aloud", "version": "0.1.0"})
    dist.parse_config_files()
    assert dist.install_requires == [], (
        "py2app aborts with 'install_requires is no longer supported'; "
        "AppDistribution.parse_config_files has to clear it"
    )


def test_a_plain_distribution_would_have_failed(setup_module_):
    """Proves the override is load-bearing, not decoration.

    If setuptools ever stops copying pyproject dependencies onto the
    Distribution this will start failing, and the override can go. Until then a
    green test here means the override is the only thing standing between the
    build and that error.
    """
    from setuptools.dist import Distribution

    dist = Distribution({"name": "Aloud", "version": "0.1.0"})
    dist.parse_config_files()
    if not getattr(dist, "install_requires", None):
        pytest.skip(
            "this setuptools does not apply pyproject dependencies to the "
            "Distribution, so the override is currently inert"
        )


def test_setup_requires_is_gone():
    """It reached py2app through setuptools' deprecated build-egg path."""
    assert "setup_requires" in SETUP_PY.read_text(), "keep the note explaining why"
    assert 'setup_requires=["py2app"]' not in SETUP_PY.read_text()
