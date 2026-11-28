"""``os-optics view``: the local 3D viewer from the shell (``os_optics.viewer``).

``register(subparsers)`` adds the command; ``command(args)`` runs it. ``present(scene, args)`` shows any
``ViewerScene`` the same way (browser page, self-contained HTML, snapshots), so another source of a scene, such as an
assembly preview opened in memory, reuses it::

    scene = assembly_view_scene(preview)          # in memory; nothing is written
    return present(scene, args, allow_html=False)
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

USAGE_ERROR = 64
VIEW_INPUT_ERROR = 65
PLAN_DIR_ERROR = 66
#: What ``view TARGET`` opens: a run directory, an Assembly Preview Package (a ``.zip`` archive or a directory with
#: ``manifest.json`` and ``scene.json``) or a local assembly plan directory (``plan.json`` and ``history.jsonl``).
TARGET_RUN, TARGET_PACKAGE, TARGET_PLAN = "RUN", "PACKAGE", "PLAN"
PACKAGE_SUFFIX = ".zip"
PACKAGE_FILES = ("manifest.json", "scene.json")
PLAN_FILES = ("plan.json", "history.jsonl")
SIZE_SEPARATOR = "x"
DEFAULT_SNAPSHOT_SIZE = "1280x960"
FRAME_NAMES = ("scene", "optics")
BOX_VALUES = 6
CAMERA_NAME = "camera"
CAMERA_VALUES = (2, 4)  # angle to the axis and elevation, then roll and distance
MAX_ANGLE_TO_AXIS_DEG = 180.0
DISTANCE_TIMES = "x"  # a distance "3.5x": 3.5 times the size of what the image frames
BACKGROUNDS = ("studio", "transparent")
HEX_COLOUR_DIGITS = 6
HEX_BASE = 16
CHANNEL_MAX = 255.0
SHOT_FIELDS = ("name", "view", "angle_to_axis_deg", "elevation_deg", "roll_deg", "distance", "focus", "frame")
VIEW_DESCRIPTION = (
    "Look at a result in 3D on this computer. RUN_DIR is a directory written by 'os-optics project run --out-dir': its "
    "Optical Simulation View (optical_scene.json + optical_scene.glb) opens in your browser as a local page (127.0.0.1, a "
    "free port, a random path; Ctrl+C stops it). Parts are drawn exactly where the design puts them (millimetres, "
    "+Z up) and coloured by class, main beams thick and weak branches (leaks, ghosts) thin and dashed. Controls: camera "
    "presets (iso, path horizontal, top, front, side, along the beam, the XY / ZX / YZ planes), roll and turn about the beam, "
    "fit / fit all / reset, class and beam toggles, labels, hover and "
    "click for id, name, SKU and size, English / Chinese. --snapshot writes PNG images (orthographic, with legend, axes "
    "and scale bar) plus scene_summary.json and legend.md without a browser; --section ELEMENT adds the exact section of an "
    "element and its axis (SVG, PNG and the numbers as JSON); --write-html writes one self-contained HTML file instead of serving. "
    "The 2D layout of a lens: '--section ALL --snapshot DIR' draws one true-scale section through every element on the axis "
    "with the stop, the image plane and the traced rays, framed on the optics. To trace more of the pupil, run the project with "
    "--pupil-rays ALL. 'os-optics layout draw' is the Optical Layout Drawing of an optics-only purchase and takes an Optical "
    "Layout Package ('os-optics order layout'), not a run directory. "
    "A camera stated by angles: '--camera 86,8' looks at the optics 86 degrees from the main beam's axis with the eye turned 8 "
    "degrees about the axis towards +Z (0 degrees: along the beam from behind its start; 90: square-on); '--camera-file "
    "shots.json' writes one image per camera; '--focus element:L2' frames one element; '--background transparent' writes "
    "RGBA PNGs. Several RUN_DIRs with --snapshot DIR write DIR/<run directory name>/ for each. scene_summary.json records "
    "per image its pixel size (image_px), pixels_per_mm, scale_bar_mm and the camera pose it was drawn from (camera: "
    "view_direction, up, target_mm, distance_mm, angle_to_axis_deg, elevation_deg, roll_deg). The browser page takes the "
    "same camera from its address: '#camera=86,8' (also ',ROLL,DISTANCE_MM'), and '&focus=L2' frames that element."
)
VIEW_DESCRIPTION += (
    " TARGET may also be an Assembly Preview Package (a .zip archive or a package directory, e.g. written by 'os-optics "
    "assembly plan preview') or a local assembly plan directory ('os-optics assembly plan derive --out PLAN'): its assembly "
    "opens without a token or a network connection, every part coloured by its checks (class colour: passed; amber: "
    "warning; red: failed or an error finding; grey: not evaluated), and each part's details state its check status, its "
    "findings, its design BOM line and who supplies it. --finding FINDING_ID highlights and frames the part or pair of one "
    "plan finding; --revision N opens an earlier plan revision; --no-checks shows a package without its layout check."
)
VIEW_EPILOG = ("exit codes: 0 = shown / written, 64 = usage error or unwritable output, 65 = TARGET holds no readable view: no "
               "optical view, no verifiable package, a plan that cannot be shown (JSON error on stdout), 66 = the plan directory "
               "is inconsistent")


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    parser = subparsers.add_parser("view", help="open a run's 3D view in the browser, or write snapshots / a self-contained HTML file",
                                   description=VIEW_DESCRIPTION, epilog=VIEW_EPILOG)
    parser.add_argument("run_dir", nargs="*", metavar="TARGET",
                        help="a run directory written by 'os-optics project run --out-dir' (several with --snapshot DIR: one "
                             "subdirectory of DIR per run, named after the run directory), an Assembly Preview Package (.zip or "
                             "directory) or a local assembly plan directory")
    parser.add_argument("--revision", type=int, metavar="N", help="plan directory: show revision N (default: the head revision)")
    parser.add_argument("--mechanical", action="append", metavar="FILE",
                        help="plan directory: a mechanical catalog document the plan binds (repeatable; default: the local store "
                             "and the plan's own copies)")
    parser.add_argument("--finding", metavar="FINDING_ID", help="plan directory: highlight and frame the part or pair of this finding")
    parser.add_argument("--no-checks", action="store_true", help="package: show it without running its layout check")
    parser.add_argument("--no-browser", action="store_true", help="serve the page and print its address instead of opening a browser")
    parser.add_argument("--write-html", metavar="PATH", help="write one self-contained HTML file (three.js included) and exit")
    parser.add_argument("--snapshot", metavar="DIR", help="write <view>.png, scene_summary.json and legend.md into DIR and exit")
    parser.add_argument("--views", default=None, help="snapshot views, comma separated: iso, path_horizontal, top, front, side, beam_axis, plane_xy, plane_zx, plane_yz "
                                                        "(default iso,top,front,side; none with --section unless given)")
    parser.add_argument("--section", metavar="ELEMENT", action="append",
                        help="also write the exact section of this element and of every element on its axis: section-<ELEMENT>-<PLANE>.svg, "
                             ".png and .json in the --snapshot DIR (true scale, exact profiles, clear apertures, rims, observation planes, "
                             "rays projected onto the plane). Repeat it or separate ids by commas for several. ALL is the whole system: "
                             "section-ALL-<PLANE>, referred to the element the light reaches first (a folded system: one more file per axis, "
                             "section-ALL-2-<PLANE>, ...)")
    parser.add_argument("--section-plane", choices=["XZ", "YZ"], default="XZ", help="the element's local section plane (default XZ)")
    parser.add_argument("--frame", metavar="FRAME",
                        help="what --section and --snapshot images show; the image takes its aspect within --size, and the scale bar follows. "
                             "optics: the optical elements and the observation planes near them, no source (default of --section). "
                             "scene: everything, with the whole beam and far planes. "
                             "--frame=X0,Y0,Z0,X1,Y1,Z1: that box of the design frame in mm. "
                             "Without it, --snapshot shows the whole setup with the source and leaves far planes out")
    parser.add_argument("--size", default=None, help="image size WIDTHxHEIGHT in pixels (snapshots: default 1280x960; sections: at most 1400x760 by default)")
    parser.add_argument("--size-mode", choices=["fit", "exact"], default=None,
                        help="snapshots with --frame or --focus: fit (default) gives the image the aspect of what it frames, at most "
                             "--size and exactly --size on the side the content fills; exact keeps --size and centres the framed "
                             "content on it. scene_summary.json states each image's image_px and content_box_px")
    parser.add_argument("--camera", metavar="ANGLE,ELEVATION[,ROLL[,DISTANCE]]",
                        help="also write camera.png from a camera stated by angles to the main beam's axis: ANGLE degrees between "
                             "the line of sight and the axis (0: along the beam from behind its start, 90: square-on, 180: back "
                             "towards the source), ELEVATION degrees the eye is turned about the axis from the level side view "
                             "towards +Z (+Y for a vertical axis), ROLL degrees the picture turns clockwise, DISTANCE the eye "
                             "distance recorded for a perspective camera (mm, or e.g. 3.5x: 3.5 times the size of what is "
                             "framed; orthographic snapshots do not depend on it). Without --views only the camera images are written")
    parser.add_argument("--camera-file", metavar="SHOTS_JSON",
                        help="one image per camera of a JSON list: [NAME, ANGLE, ELEVATION, ROLL?, DISTANCE?] or "
                             "{\"name\", \"angle_to_axis_deg\", \"elevation_deg\", \"roll_deg\", \"distance\", \"focus\", \"frame\"} "
                             "(or {\"name\", \"view\": a preset}); written as NAME.png")
    parser.add_argument("--focus", metavar="TARGET",
                        help="snapshots: frame element:ID (several: element:ID+ID+...; elements and observation planes) or "
                             "axis:ID (the element and every element and observation plane on its axis) instead of --frame")
    parser.add_argument("--background", metavar="BACKGROUND",
                        help="snapshots: studio (default, the view's backdrop), transparent (RGBA PNG, alpha = coverage of what is "
                             "drawn) or a flat colour #rrggbb")
    parser.add_argument("--port", type=int, default=0, help="local port for the page (default: a free port)")
    return parser


def _json_out(doc: object) -> None:
    print(json.dumps(doc, indent=2, ensure_ascii=True))


def _size(text: str) -> tuple[int, int] | None:
    try:
        width, height = (int(v) for v in text.lower().split(SIZE_SEPARATOR))
    except ValueError:
        return None
    return width, height


def _frame(text: str | None) -> str | tuple[float, ...] | None:
    """``--frame``: a name, or the box ``(x0, y0, z0, x1, y1, z1)``. ``ValueError`` when it is neither."""

    if text is None or text in FRAME_NAMES:
        return text
    values = tuple(float(v) for v in text.split(","))
    if len(values) != BOX_VALUES or not all(math.isfinite(v) for v in values) or any(a > b for a, b in zip(values[:3], values[3:])):
        raise ValueError(text)
    return values


class _Usage(ValueError):
    """A usage error: its message is printed after ``error:``."""


def _number(value: object, what: str) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise _Usage(f"{what} must be a number, got {value!r}") from None
    if not math.isfinite(number) or isinstance(value, bool):
        raise _Usage(f"{what} must be a finite number, got {value!r}")
    return number


def _distance(value: object, what: str) -> float | str | None:
    """A camera distance: mm, or ``<k>x`` (k times the size of what is framed)."""

    if value is None or value == "":
        return None
    if isinstance(value, str) and value.strip().lower().endswith(DISTANCE_TIMES):
        times = _number(value.strip()[:-1], what)
        if times <= 0.0:
            raise _Usage(f"{what} must be positive, got {value!r}")
        return f"{times:g}{DISTANCE_TIMES}"
    distance = _number(value, what)
    if distance <= 0.0:
        raise _Usage(f"{what} must be positive, got {value!r}")
    return distance


def _angle_shot(name: str, values: list, where: str, **extra):
    from .viewer import Shot

    if not CAMERA_VALUES[0] <= len(values) <= CAMERA_VALUES[1]:
        raise _Usage(f"{where} takes ANGLE,ELEVATION[,ROLL[,DISTANCE]], got {len(values)} value(s)")
    angle = _number(values[0], f"{where}: the angle to the axis")
    if not 0.0 <= angle <= MAX_ANGLE_TO_AXIS_DEG:
        raise _Usage(f"{where}: the angle to the axis is 0..180 degrees, got {values[0]!r}")
    elevation = _number(values[1], f"{where}: the elevation")
    roll = _number(values[2], f"{where}: the roll") if len(values) > 2 and values[2] not in (None, "") else 0.0
    distance = _distance(values[3], f"{where}: the distance") if len(values) > 3 else None
    return Shot(name, angle_to_axis_deg=angle, elevation_deg=elevation, roll_deg=roll, distance=distance, **extra)


def _camera(text: str):
    """``--camera ANGLE,ELEVATION[,ROLL[,DISTANCE]]``: the shot ``camera``."""

    return _angle_shot(CAMERA_NAME, [v.strip() for v in text.split(",")], "--camera")


def _camera_file(path: str, scene) -> list:
    """``--camera-file``: a JSON list of cameras (``SHOT_FIELDS``)."""

    from .viewer import Shot, focus_frame

    try:
        entries = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise _Usage(f"cannot read the camera file: {exc.strerror}") from None
    except ValueError as exc:
        raise _Usage(f"the camera file is not JSON: {exc}") from None
    if not isinstance(entries, list) or not entries:
        raise _Usage("the camera file is a non-empty JSON list of cameras")
    shots = []
    for k, entry in enumerate(entries, start=1):
        where = f"camera file entry {k}"
        if isinstance(entry, list):
            if not entry or not isinstance(entry[0], str):
                raise _Usage(f"{where}: [NAME, ANGLE, ELEVATION, ROLL?, DISTANCE?] starts with the image name")
            shots.append(_angle_shot(entry[0], entry[1:], where))
            continue
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            raise _Usage(f"{where}: a list [NAME, ANGLE, ELEVATION, ...] or an object with a \"name\"")
        unknown = sorted(set(entry) - set(SHOT_FIELDS))
        if unknown:
            raise _Usage(f"{where}: unknown field(s) {unknown}; the fields are {list(SHOT_FIELDS)}")
        if "focus" in entry and "frame" in entry:
            raise _Usage(f"{where}: give focus or frame, not both")
        extra = {}
        if "focus" in entry:
            try:
                extra["frame"] = focus_frame(scene, str(entry["focus"]))
            except ValueError as exc:
                raise _Usage(f"{where}: {exc}") from None
        elif "frame" in entry:
            frame = entry["frame"]
            try:
                extra["frame"] = _frame(frame if isinstance(frame, str) or frame is None else ",".join(str(v) for v in frame))
            except (TypeError, ValueError):
                raise _Usage(f"{where}: frame is optics, scene or a box [X0, Y0, Z0, X1, Y1, Z1] in mm") from None
        if "view" in entry:
            if any(f in entry for f in ("angle_to_axis_deg", "elevation_deg", "roll_deg", "distance")):
                raise _Usage(f"{where}: a preset view takes no angles")
            shots.append(Shot(entry["name"], view=str(entry["view"]), **extra))
            continue
        if "angle_to_axis_deg" not in entry:
            raise _Usage(f"{where}: give angle_to_axis_deg (and elevation_deg), or a preset view")
        shots.append(_angle_shot(entry["name"], [entry["angle_to_axis_deg"], entry.get("elevation_deg", 0.0), entry.get("roll_deg"),
                                                 entry.get("distance")], where, **extra))
    return shots


def _background(text: str | None):
    """``--background``: ``studio``, ``transparent`` or ``#rrggbb`` (an ``(r, g, b)`` colour in 0..1)."""

    if text is None or text in BACKGROUNDS:
        return text
    digits = text[1:] if text.startswith("#") else ""
    if len(digits) != HEX_COLOUR_DIGITS or any(c not in "0123456789abcdefABCDEF" for c in digits):
        raise _Usage(f"--background must be {' or '.join(BACKGROUNDS)} or a colour #rrggbb, got {text!r}")
    return tuple(int(digits[k:k + 2], HEX_BASE) / CHANNEL_MAX for k in range(0, HEX_COLOUR_DIGITS, 2))


def present(scene, args: argparse.Namespace, *, allow_html: bool = True) -> int:
    """Snapshots and / or HTML when asked for; otherwise serve the page (and open the browser unless --no-browser)."""

    from .viewer import serve_view

    docs = _outputs(scene, args, args.snapshot, allow_html=allow_html)
    if isinstance(docs, int):
        return docs
    for doc in docs:
        _json_out(doc)
    if docs:
        return 0
    try:
        server = serve_view(scene, open_browser=not args.no_browser, port=args.port)
    except OSError as exc:
        print(f"error: cannot start the local page: {exc.strerror}", file=sys.stderr)
        return USAGE_ERROR
    _json_out({"view_kind": scene.view_kind, "url": server.url, "stop": "press Ctrl+C to stop the local page"})
    sys.stdout.flush()
    server.wait()
    return 0


SNAPSHOT_ONLY = (("frame", "--frame applies to --snapshot and --section images"), ("focus", "--focus applies to --snapshot images"),
                 ("camera", "--camera applies to --snapshot images"), ("camera_file", "--camera-file applies to --snapshot images"),
                 ("background", "--background applies to --snapshot images"), ("size_mode", "--size-mode applies to --snapshot images"))


def _outputs(scene, args: argparse.Namespace, snapshot_dir: str | None, *, allow_html: bool) -> list[dict] | int:
    """Write what the arguments ask for (sections and snapshots into ``snapshot_dir``, an HTML file): the JSON documents
    to print (none: nothing was asked for, serve the page), or a usage-error exit code."""

    from .viewer import focus_frame, view_document, write_snapshots
    from .viewer.page import standalone_html

    docs: list[dict] = []
    section_ids = [v.strip() for group in (getattr(args, "section", None) or ()) for v in group.split(",") if v.strip()]
    try:
        frame = _frame(getattr(args, "frame", None))
    except ValueError:
        print(f"error: --frame must be {' or '.join(FRAME_NAMES)} or a box --frame=X0,Y0,Z0,X1,Y1,Z1 in mm (each lower corner value "
              "at most the upper one)", file=sys.stderr)
        return USAGE_ERROR
    for name, message in SNAPSHOT_ONLY:
        if getattr(args, name, None) is not None and not snapshot_dir:
            print(f"error: {message}; give --snapshot DIR", file=sys.stderr)
            return USAGE_ERROR
    size_text = getattr(args, "size", None)
    size = _size(size_text) if size_text is not None else None
    if size_text is not None and size is None:
        print("error: --size must be WIDTHxHEIGHT, e.g. 1280x960", file=sys.stderr)
        return USAGE_ERROR
    try:
        background = _background(getattr(args, "background", None))
        shots = [_camera(args.camera)] if getattr(args, "camera", None) else []
        if getattr(args, "camera_file", None):
            shots += _camera_file(args.camera_file, scene)
        focus = getattr(args, "focus", None)
        if focus is not None:
            if frame is not None:
                raise _Usage("give --frame or --focus, not both")
            snapshot_frame = focus_frame(scene, focus)
        else:
            snapshot_frame = frame
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return USAGE_ERROR
    if section_ids:
        if not snapshot_dir:
            print("error: --section writes its files into the --snapshot DIR; give --snapshot DIR", file=sys.stderr)
            return USAGE_ERROR
        written = _write_sections(scene, section_ids, args.section_plane, snapshot_dir, frame=frame or "optics", size=size)
        if isinstance(written, int):
            return written
        docs.append({"view_kind": scene.view_kind, "snapshot_dir": snapshot_dir, "sections": written})
    if snapshot_dir and (args.views is not None or shots or not section_ids):
        size = size or _size(DEFAULT_SNAPSHOT_SIZE)
        default_views = "" if shots else "iso,top,front,side"
        views = tuple(v.strip() for v in (args.views if args.views is not None else default_views).split(",") if v.strip())
        try:
            written = write_snapshots(scene, snapshot_dir, views, width=size[0], height=size[1], frame=snapshot_frame, shots=shots,
                                      background=background, size_mode=getattr(args, "size_mode", None) or "fit")
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return USAGE_ERROR
        except OSError as exc:
            print(f"error: cannot write the snapshots: {exc.strerror}", file=sys.stderr)
            return USAGE_ERROR
        docs.append({"view_kind": scene.view_kind, "snapshot_dir": snapshot_dir, "files": {k: v.as_posix() for k, v in written.items()}})
    if args.write_html:
        if not allow_html:
            print("error: --write-html is available for a local run directory only; use the browser view or --snapshot", file=sys.stderr)
            return USAGE_ERROR
        try:
            Path(args.write_html).write_bytes(standalone_html(view_document(scene)))
        except OSError as exc:
            print(f"error: cannot write the HTML file: {exc.strerror}", file=sys.stderr)
            return USAGE_ERROR
        docs.append({"view_kind": scene.view_kind, "html": args.write_html})
    return docs


def _sections_of(source, element_ids: list[str], plane: str) -> dict[str, dict]:
    """``{file key: section}``: ``ALL`` gives one key per axis of the system (``ALL``, ``ALL-2``, ...), an element its id."""

    from .optical_view.section import ALL_ELEMENTS, optical_section, scene_section_elements, system_sections

    elements = scene_section_elements(source)
    out: dict[str, dict] = {}
    for element_id in element_ids:
        if element_id == ALL_ELEMENTS:
            sections = system_sections(elements, plane, beam_paths=source.beam_paths, observation_planes=source.observation_planes)
            for k, section in enumerate(sections, start=1):
                out[ALL_ELEMENTS if k == 1 else f"{ALL_ELEMENTS}-{k}"] = section
        else:
            out[element_id] = optical_section(elements, element_id, plane, beam_paths=source.beam_paths, observation_planes=source.observation_planes)
    return out


def _write_sections(scene, element_ids: list[str], plane: str, out_dir: str, *, frame="optics",
                    size: tuple[int, int] | None = None) -> dict[str, dict] | int:
    """``section-<id>-<plane>.svg`` / ``.png`` / ``.json`` per element (or per axis of the whole system) into ``out_dir``
    (an Optical Simulation View)."""

    from .optical_view.section import SVG_HEIGHT_PX, SVG_WIDTH_PX, SectionError, section_layout, section_svg
    from .viewer.section_render import section_png
    from .viewer.snapshots import MAX_SIZE_PX, MIN_SIZE_PX

    source = getattr(scene, "source_scene", None)
    if source is None:
        print("error: --section needs a run directory's Optical Simulation View", file=sys.stderr)
        return USAGE_ERROR
    width, height = size or (SVG_WIDTH_PX, SVG_HEIGHT_PX)
    if not (MIN_SIZE_PX <= width <= MAX_SIZE_PX and MIN_SIZE_PX <= height <= MAX_SIZE_PX):
        print(f"error: image size must be {MIN_SIZE_PX}..{MAX_SIZE_PX} pixels per side", file=sys.stderr)
        return USAGE_ERROR
    directory = Path(out_dir)
    written: dict[str, dict] = {}
    try:
        directory.mkdir(parents=True, exist_ok=True)
        try:
            sections = _sections_of(source, element_ids, plane)
            drawings = {key: (section_svg(section, width=width, height=height, frame=frame), section_png(section, width=width, height=height, frame=frame),
                              section_layout(section, width, height, frame)) for key, section in sections.items()}
        except SectionError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return USAGE_ERROR
        for key, section in sections.items():
            svg, png, layout = drawings[key]
            stem = directory / f"section-{key.replace('/', '-')}-{plane}"
            files = {"svg": Path(f"{stem}.svg"), "png": Path(f"{stem}.png"), "json": Path(f"{stem}.json")}
            files["svg"].write_text(svg, encoding="utf-8")
            files["png"].write_bytes(png)
            files["json"].write_text(json.dumps(section, indent=1) + "\n", encoding="utf-8")
            written[key] = {**{k: v.as_posix() for k, v in files.items()}, "reference_element_id": section["reference_element_id"],
                            "elements": [e["element_id"] for e in section["elements"]],
                            "observation_planes": [p["observation_id"] for p in section["observation_planes"]],
                            "excluded": section["excluded"], "beyond_frame": section["bounds"]["beyond_frame"] if frame == "optics" else [],
                            "image_px": [layout.width, layout.height], "pixels_per_mm": round(layout.scale, 3), "scale_bar_mm": layout.bar_mm}
    except OSError as exc:
        print(f"error: cannot write the section files: {exc.strerror}", file=sys.stderr)
        return USAGE_ERROR
    return written


def target_kind(target: str) -> str:
    """What ``target`` is: ``TARGET_PACKAGE`` (a ``.zip`` file, or a directory with a package's manifest and scene),
    ``TARGET_PLAN`` (a directory with a plan's ``plan.json`` and ``history.jsonl``) or ``TARGET_RUN`` (anything else)."""

    path = Path(target)
    if path.is_file() and path.suffix.lower() == PACKAGE_SUFFIX:
        return TARGET_PACKAGE
    if path.is_dir():
        if all((path / name).is_file() for name in PLAN_FILES):
            return TARGET_PLAN
        if all((path / name).is_file() for name in PACKAGE_FILES):
            return TARGET_PACKAGE
    return TARGET_RUN


PLAN_ONLY = (("revision", "--revision"), ("mechanical", "--mechanical"), ("finding", "--finding"))


def _assembly_scene(target: str, kind: str, args: argparse.Namespace):
    """The assembly view of a package or a plan directory, or the exit code of a refusal (JSON error printed)."""

    from .viewer import AssemblyViewError, ViewerInputError, package_view_scene

    if kind == TARGET_PACKAGE:
        for name, flag in PLAN_ONLY:
            if getattr(args, name, None) is not None:
                print(f"error: {flag} applies to a plan directory", file=sys.stderr)
                return USAGE_ERROR
        try:
            return package_view_scene(target, checks=not args.no_checks)
        except (ViewerInputError, AssemblyViewError) as exc:
            _json_out({"status": "ERROR", "error": {"code": "VIEW_INPUT_INVALID", "message": str(exc)}})
            return VIEW_INPUT_ERROR
    if args.no_checks:
        print("error: --no-checks applies to a package; a plan is always shown with its checks", file=sys.stderr)
        return USAGE_ERROR
    return _plan_scene(target, args)


def _plan_scene(plan_dir: str, args: argparse.Namespace):
    """A plan directory's view: ``os_optics.assembly_plan.open_plan_view(plan_dir, revision=, mechanical=)`` gives the
    revision, its projection context and its findings."""

    from . import assembly_plan
    from .assembly_plan.contract import AssemblyPlanError, AssemblyPlanErrorCode
    from .assembly_plan.mechanical import MechanicalCatalogError
    from .viewer import AssemblyViewError, ViewerInputError, plan_view_scene

    opener = getattr(assembly_plan, "open_plan_view", None)
    if opener is None:
        _json_out({"status": "ERROR", "error": {"code": "VIEW_INPUT_INVALID", "message": (
            "this os-optics build cannot open a plan directory (it has no os_optics.assembly_plan.open_plan_view); view the "
            "plan's preview package instead: os-optics assembly plan preview PLAN --out preview.zip")}})
        return VIEW_INPUT_ERROR
    try:
        revision, context, findings = opener(plan_dir, revision=args.revision, mechanical=tuple(args.mechanical or ()))
        return plan_view_scene(revision, context, findings=findings, finding_id=args.finding)
    except AssemblyPlanError as exc:
        _json_out({"status": "ERROR", "error": exc.as_dict()})
        return PLAN_DIR_ERROR if exc.code is AssemblyPlanErrorCode.PLAN_DIR_INCONSISTENT else VIEW_INPUT_ERROR
    except MechanicalCatalogError as exc:
        _json_out({"status": "ERROR", "error": exc.as_dict()})
        return VIEW_INPUT_ERROR
    except (ViewerInputError, AssemblyViewError) as exc:
        _json_out({"status": "ERROR", "error": {"code": "VIEW_INPUT_INVALID", "message": str(exc)}})
        return VIEW_INPUT_ERROR


def command(args: argparse.Namespace) -> int:
    from .viewer import ViewerInputError, optical_view_scene

    run_dirs = list(args.run_dir or ())
    if not run_dirs:
        print("error: give a run directory (written by 'os-optics project run --out-dir'), an assembly preview package or a "
              "plan directory", file=sys.stderr)
        return USAGE_ERROR
    kinds = [target_kind(t) for t in run_dirs]
    if any(k != TARGET_RUN for k in kinds):
        if len(run_dirs) > 1:
            print("error: give one assembly preview package or plan directory at a time", file=sys.stderr)
            return USAGE_ERROR
        scene = _assembly_scene(run_dirs[0], kinds[0], args)
        if isinstance(scene, int):
            return scene
        # a commercial package stays in its delivery's terms (no self-contained copy); a local plan's package may be shared
        local = scene.identity.get("plan_id") is not None
        return present(scene, args, allow_html=local)
    for name, flag in (*PLAN_ONLY, ("no_checks", "--no-checks")):
        if getattr(args, name, None) not in (None, False):
            print(f"error: {flag} applies to an assembly preview package or a plan directory", file=sys.stderr)
            return USAGE_ERROR
    if len(run_dirs) > 1:
        return _batch(run_dirs, args)
    try:
        scene = optical_view_scene(run_dirs[0])
    except ViewerInputError as exc:
        _json_out({"status": "ERROR", "error": {"code": "VIEW_INPUT_INVALID", "message": str(exc)}})
        return VIEW_INPUT_ERROR
    return present(scene, args)


def _batch(run_dirs: list[str], args: argparse.Namespace) -> int:
    """Several run directories: each one's sections and snapshots into ``<--snapshot DIR>/<run directory name>``."""

    from .viewer import ViewerInputError, optical_view_scene

    if not args.snapshot or args.write_html:
        print("error: several run directories take --snapshot DIR (one subdirectory per run) and no --write-html", file=sys.stderr)
        return USAGE_ERROR
    names = [Path(r).resolve().name for r in run_dirs]
    twice = sorted({n for n in names if names.count(n) > 1})
    if twice:
        print(f"error: run directories with the same name {twice} would write into one subdirectory; rename or copy them", file=sys.stderr)
        return USAGE_ERROR
    runs: dict[str, dict] = {}
    for run_dir, name in zip(run_dirs, names, strict=True):
        try:
            scene = optical_view_scene(run_dir)
        except ViewerInputError as exc:
            _json_out({"status": "ERROR", "run_dir": run_dir, "error": {"code": "VIEW_INPUT_INVALID", "message": str(exc)}})
            return VIEW_INPUT_ERROR
        docs = _outputs(scene, args, (Path(args.snapshot) / name).as_posix(), allow_html=False)
        if isinstance(docs, int):
            return docs
        entry: dict = {"run_dir": run_dir}
        for doc in docs:
            entry.update({k: v for k, v in doc.items() if k != "view_kind"})
        runs[name] = entry
    _json_out({"snapshot_dir": args.snapshot, "runs": runs})
    return 0
