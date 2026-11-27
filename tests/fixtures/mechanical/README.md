# Synthetic mechanical catalog fixtures

Public test data for `opensci.public_mechanical_catalog` 1.0. Every identity is invented (`OS-MECH-TEST-*`,
`USER-MECH-TEST-*`) and every number is a round made-up value. Nothing here comes from a real family catalog, a
supplier datasheet or a private test fixture.

The files are written by `tests/mechanical_fixture.py` and must not be edited by hand. To regenerate them:

```
python tests/generate_fixtures.py mechanical
```

The files are the osc1 canonical bytes of the documents: one line, no final newline. `test_mechanical_fixtures.py`
keeps them equal to the builder output.

## `test_mechanical_catalog.v1.json` (publisher OPENSCI, `m1.0123456789ab`, default policy)

| Identity | Kind | Key facts | Intended use |
| --- | --- | --- | --- |
| `OS-MECH-TEST-POST-D12P7-L{25,40,50,75,100}` | POST | diameter 12.7, stud M4 | height closure |
| `OS-MECH-TEST-POST-D6-L50` | POST | diameter 6, stud M3 | incompatible swap (thread and diameter) |
| `OS-MECH-TEST-HOLDER-D12P7-H{25,50,75}` | POST_HOLDER | bore 12.7, insertion [5, h-5], fork slot [15, 35] x 6.6, M6 | holder stacks, hole fixing |
| `OS-MECH-TEST-LENS-MOUNT-FIXED-D25` | MOUNT FIXED | ROUND 25.4, thickness [0, 8], transmissive, aperture 22, centre 20, envelope x +-5 | compact fallback |
| `OS-MECH-TEST-LENS-MOUNT-XY-D25` | MOUNT XY_TRANSLATION | as above, travel 1 mm, envelope x +-14 | focus lens (keeps Tx/Ty) |
| `OS-MECH-TEST-ROTATION-MOUNT-D25` | MOUNT ROTATION | ROUND 25.4, thickness [0, 10], travel 180, envelope x +-18 | half-wave plate Rz; collides at 30 mm spacing |
| `OS-MECH-TEST-MIRROR-MOUNT-KIN-D25` | MOUNT KINEMATIC | ROUND 25.4, thickness [3, 12], not transmissive, travel 3 deg, CONSERVATIVE profile (`envelope`, `holds.thickness_max_mm`) | fold mirror; wrong mount for a transmissive optic |
| `OS-MECH-TEST-PLATE-MOUNT-KIN-D25-T` | MOUNT KINEMATIC | transmissive, no recorded aperture (basis PREVIEW_ASSUMPTION) | dichroic / plate beamsplitter; oblique clearance not verified |
| `OS-MECH-TEST-LENS-MOUNT-FIXED-D50` | MOUNT FIXED | ROUND 50.8, centre 35 | size mismatch swap |
| `OS-MECH-TEST-BREADBOARD-{150X150,300X150,450X150,300X300}-M6G25` | BREADBOARD | thickness 12.7, pitch 25, edge 12.5, M6 | board selection; BOARD_TOO_SMALL with a 150 x 150 limit |

## `user_hardware.v1.json` (publisher USER, `test-user-hardware.1`)

`USER-MECH-TEST-POST-D12P7-L60`: a post the customer already owns, with no basis recorded. It is used for the
customer-owned supply rule.

The envelope numbers are the starting point for the assembly plan scenarios. The lane that owns the derive tests may
ask for different values; the change is then made here and the files are regenerated.
