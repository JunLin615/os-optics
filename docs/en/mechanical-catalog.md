# The public mechanical catalog

[User guide](README.md) · English · [简体中文](../zh-CN/mechanical-catalog.md)

A local assembly plan is derived from two public catalogs. The [optical catalog](catalog.md) describes the optics. The mechanical catalog describes the OpenSci mounts, posts, post holders and breadboards (`OS-MECH-*` part numbers) that hold them. It is a public document, `opensci.public_mechanical_catalog` version `1.0`. It lists, for each part number:

- its kind and its English and Chinese names;
- the engineering facts the plan derivation reads, such as a post's diameter, length and thread, or a mount's centre height, the optic it holds and its adjustment travel;
- the basis of each fact (`DATASHEET`, `REVIEWED_REFERENCE`, `CAD_DERIVED`, `FAMILY_RULE` or `PREVIEW_ASSUMPTION`);
- simple shapes for the viewer.

It carries no price and no supplier data.

Some OpenSci part numbers are made by more than one supplier product. For these, the document gives one conservative profile, marked `CONSERVATIVE`. The profile uses the largest envelope, the smallest clear aperture and travel, and the narrowest thickness and insertion ranges. The parameters that differ between products are listed in `profile.conservative_parameters`.

## Get the catalog

`os-optics catalog sync` downloads the current optical release. When the OpenSci service offers the mechanical catalog, the same command also downloads its current mechanical release. The SHA-256, size and content of the release are checked, and the release is made active. The output has a `mechanical` block whose `outcome` is one of these values:

| Outcome | Meaning | Exit code |
| --- | --- | --- |
| `DOWNLOADED` | The current release was stored and is now active. | 0 |
| `ALREADY_STORED` | This device already holds the current release; it is active. | 0 |
| `NOT_OFFERED` | The service does not offer the mechanical catalog. This is not an error. | from the optical sync |
| `UNAVAILABLE` | The service could not be reached, timed out, or answered with a server error. One line on stderr says so. The stored release stays in use. | from the optical sync |
| `FAILED` | The download did not match the published SHA-256 or size, or it is not a valid document. Nothing was stored. | 1 |

A computer that cannot reach the service imports a release from a file. The command recognises a mechanical document by its schema:

```bash
os-optics catalog import public_mechanical_catalog.json
```

These commands also report the mechanical catalog in their `mechanical` block:

- `catalog status` shows the active and stored releases, without using the network;
- `catalog check` asks the service for its current release;
- `catalog verify` re-checks every stored file, and exits with 66 when one is damaged.

Releases are stored in the catalog store, under `mechanical/` in `OPENSCI_CATALOG_DIR`. A release is written once and is never replaced by different bytes. None of these commands needs a token.

## Use your own hardware

A plan can also use hardware you own, described in a USER document. In a USER document:

- `publisher` is `USER`;
- part numbers have the form `USER-MECH-*`;
- `mechanical_version` is a short lower-case label;
- there is no `policy`;
- `content_digest` is the document's own digest.

Pass it with `--mechanical FILE`. The option can be repeated: at most one OPENSCI document and eight USER documents can be used, and they must not describe the same part number twice. You can also name the files in `OS_OPTICS_MECHANICAL_CATALOG`, separated by the system path separator (`;` on Windows, `:` elsewhere).

A command uses the documents in this order:

1. the `--mechanical FILE` documents;
2. otherwise, the documents named by `OS_OPTICS_MECHANICAL_CATALOG`;
3. for a plan, the OPENSCI release the plan was derived with, together with the plan's own copies of its USER documents;
4. otherwise, the active stored release.

A plan therefore reopens offline.

## Browse the catalog

```bash
os-optics catalog mechanical list --kind MOUNT
os-optics catalog mechanical list --holds-size 25.4
os-optics catalog mechanical show OS-MECH-...
os-optics catalog mechanical status
```

The commands work as follows:

- `list` prints one entry per part number, with its kind, name, key facts, profile and publisher. With `--json`, it prints the identity documents in full.
- `show` prints one identity document. It exits with 1 for an unknown part number.
- `status` names the documents in effect and the stored releases.

Without any document, these commands exit with 65 (`list` and `show`) or 1 (`status`), and the output says how to get one.
