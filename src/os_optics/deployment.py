"""Deployment defaults shipped with the engine (``os_optics/deployment.toml``), read at run time.

Deployment facts such as the service origin live in that data file rather than in the Python sources, so a host move
does not change :func:`os_optics.manifest.engine_build_id` (which hashes the ``.py`` sources only). Environment
variables (``OPENSCI_CATALOG_URL``, ``OPENSCI_API_URL``, ``OPENSCI_ACCOUNT_URL``) still take precedence where a value is
resolved.
"""

from __future__ import annotations

import tomllib
from importlib import resources

DEPLOYMENT_FILE = "deployment.toml"


def _deployment() -> dict:
    return tomllib.loads(resources.files("os_optics").joinpath(DEPLOYMENT_FILE).read_text(encoding="utf-8"))


def default_service_origin() -> str:
    """The OpenSci service origin used when no environment variable names a service (``[service] origin``)."""

    return str(_deployment()["service"]["origin"])


def default_account_origin() -> str:
    """The OpenSci Account website used when ``OPENSCI_ACCOUNT_URL`` is not set (``[account] origin``)."""

    return str(_deployment()["account"]["origin"])
