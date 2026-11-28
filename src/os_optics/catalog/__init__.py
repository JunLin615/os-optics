"""The public optical catalog on this device: a local, versioned store synchronized with the OpenSci catalog service.

The catalog is data, not part of the Engine package: OpenSci publishes catalog releases (``catalog_version``) on its
service, and the Engine keeps verified copies in a local store, so a new catalog release never needs a new Engine
release and local design keeps working offline.

* Store: ``$OPENSCI_CATALOG_DIR`` (default ``~/.opensci/catalog/``): ``releases/<catalog_version>.json`` (exact
  published bytes, verified by SHA-256, never replaced by different bytes) and ``state.json`` (active release, last
  check). Concurrent processes may share one store: every change is made under the store's lock against the current
  index. A lost index is rebuilt from the release files with no active release: the next check selects the service's
  current release, else an unpinned use takes the newest stored release and says so (``catalog use`` selects another;
  ``CATALOG_ACTIVE_UNKNOWN`` only when this Engine reads none of the stored releases).
* Service: ``OPENSCI_CATALOG_URL``, else ``OPENSCI_API_URL``, else the OpenSci service (``GET /api/v1/catalog``: the
  release manifest; ``GET /api/v1/catalog/releases/<catalog_version>``: one release).
* Automatic: the first local use with an empty store synchronizes once; afterwards a catalog-consuming entry point
  checks the service at most every ``OPENSCI_CATALOG_CHECK_INTERVAL`` seconds (default 3600, ``0`` = never
  automatically) with a conditional request, and downloads only a release it does not hold (one line on stderr says
  so). Offline, the stored release stays in use.
* Reproducibility: a Project's components pin a ``catalog_version``; running that Project uses exactly that release
  (fetched once when missing; into an empty store it becomes the active release, an active release is never changed).
  ``os_optics.local.update_project_catalog`` moves a Project to the active release.

Python::

    from os_optics import catalog

    catalog.status().as_dict()      # local: active release, stored releases, last service check (no network)
    catalog.check().as_dict()       # ask the service: up_to_date, upgrade_required, its current release
    catalog.sync()                  # download the service's current release if needed and make it active
    catalog.sync("2026-09-30.3")    # fetch one retained release (active only when no release is active)
    catalog.verify()                # re-check every stored release's SHA-256 (no network)
    catalog.use_release("2026-09-30.3")   # make a stored release the active one (no network)
    catalog.import_release("public_optical_catalog.json")   # an offline device: add a release from a file

    catalog.mechanical_status()     # local: the stored public mechanical catalog releases (no network)
    catalog.check_mechanical()      # ask the service: CHECKED, NOT_OFFERED, UNAVAILABLE or FAILED
    catalog.sync_mechanical()       # download the current mechanical release when the service offers it
    catalog.verify_mechanical()     # re-check every stored mechanical release (no network)
    catalog.import_mechanical("public_mechanical_catalog.json")   # add a mechanical release from a file

Shell: ``os-optics catalog status|check|sync [--release V]|verify|import FILE|use V`` and ``os-optics catalog
mechanical list|show|status``. Failures raise
``CatalogError`` with a stable ``CatalogErrorCode`` (``CATALOG_NOT_AVAILABLE``, ``ENGINE_UPGRADE_REQUIRED``,
``CATALOG_INTEGRITY_FAILED``, ...). The browser runtime never uses the network: its host installs the catalog with
``os_optics.local.json_api.install_catalog``.
"""

from .errors import CatalogError, CatalogErrorCode
from .remote import ENV_API_URL, ENV_CATALOG_URL, manifest_url
from .service import (
    DEFAULT_CHECK_INTERVAL_SECONDS,
    ENV_CHECK_INTERVAL,
    CatalogStatus,
    automatic_access,
    check,
    check_interval,
    check_mechanical,
    import_mechanical,
    import_release,
    mechanical_status,
    network_available,
    release_bytes,
    status,
    sync,
    sync_mechanical,
    use_release,
    verify,
    verify_mechanical,
)
from .store import (
    ENV_CATALOG_DIR,
    SUPPORTED_SCHEMA_VERSIONS,
    CatalogStore,
    StoredRelease,
    catalog_version_key,
    store_directory,
    validate_release,
)

__all__ = [
    "DEFAULT_CHECK_INTERVAL_SECONDS",
    "ENV_API_URL",
    "ENV_CATALOG_DIR",
    "ENV_CATALOG_URL",
    "ENV_CHECK_INTERVAL",
    "SUPPORTED_SCHEMA_VERSIONS",
    "CatalogError",
    "CatalogErrorCode",
    "CatalogStatus",
    "CatalogStore",
    "StoredRelease",
    "automatic_access",
    "catalog_version_key",
    "check",
    "check_interval",
    "check_mechanical",
    "import_mechanical",
    "import_release",
    "manifest_url",
    "mechanical_status",
    "network_available",
    "release_bytes",
    "status",
    "store_directory",
    "sync",
    "sync_mechanical",
    "use_release",
    "validate_release",
    "verify",
    "verify_mechanical",
]
