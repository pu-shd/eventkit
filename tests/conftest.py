"""eventkit's own tests load the shipped plugin the same way an app repo does.

This is the whole file, and it is the whole file in each of the five application
repositories too. Compare with ``ticketed/tests/conftest.py`` and
``posted/tests/conftest.py``, which must set environment variables before
importing the application module because ``settings = Settings()`` and
``Base.metadata.create_all()`` both run at import time.

There is deliberately no ``pytest_plugins`` line here. The ``pytest11`` entry
point in ``pyproject.toml`` registers ``eventkit.testing.plugin`` automatically
for anything that has eventkit installed, so naming it again registers the same
module under a second name and pytest aborts collection with
``ValueError: Plugin already registered under a different name``.

Installing eventkit is all an application repo needs to do; its ``conftest.py``
can be empty.
"""
