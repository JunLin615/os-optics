"""OpenSci local 3D viewer: look at an Optical Simulation View or an Experimental Lab Assembly on this computer.

Shell::

    os-optics view run                          # opens the run's 3D view in your browser (Ctrl+C to stop)
    os-optics view run --no-browser             # prints the local address instead
    os-optics view run --write-html view.html   # one self-contained HTML file, no server
    os-optics view run --snapshot shots         # PNG images + scene_summary.json + legend.md, no browser needed
    os-optics view run --snapshot shots --camera 86,8 --focus element:L2 --background transparent
                                                # a camera stated by angles, framed on one element, an RGBA PNG

Python::

    from os_optics.viewer import view_run, view_assembly, render_snapshots, scene_summary

    server = view_run("run")                         # a running local page; server.url, server.close()
    render_snapshots("run", "shots")                 # iso / top / front / side PNGs, scene_summary.json, legend.md
    render_snapshots("run", "shots", views=(), shots=[Shot("side86", angle_to_axis_deg=86, elevation_deg=8)],
                     frame=focus_frame(load_view("run"), "element:L2"), background="transparent")
    scene_summary("run")                             # the same facts as a dict: parts by class, bounding boxes, beams
    view_assembly(preview)                           # a verified AssemblyPreviewPackageV1, served from memory only
    render_snapshots(preview, "assembly-shots")      # snapshots of the assembly (derived images; the package is not written)
    package_view_scene("preview.zip")                # a package on disk, verified, its parts coloured by its layout check
    plan_view_scene(revision, context, finding_id=...)   # a local assembly plan revision: projected, checked, coloured

What you see: every part exactly where the run or the package puts it (frame, millimetres, +Z up), coloured by class
(optics, mounts, stages, posts, post holders, bases, breadboard, your own hardware), with outlines; beams in their
display colour, main beams thick and weak branches (a leak through a coating, a ghost reflection) thin and dashed. The
browser view has camera presets (iso, top, front, side, along the beam), fit / fit all / reset, class and beam toggles,
part labels, hover and click details (id, name, SKU, size in mm), an axes triad, English / Chinese text, and for an
assembly an optional "Spaced" display that moves support chains apart for readability and says so on screen; exact
geometry is the default. The snapshot images are orthographic, carry a title, a legend, an axes triad and a scale bar.

The page is served from 127.0.0.1 on a free port under a random path, only to this computer; the server keeps every file
in memory and writes nothing. An assembly preview is shown from memory: nothing of the package is written to disk.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .assembly import AssemblyViewError, assembly_view_scene, part_check_status
from .model import (
    ASSEMBLY_VIEW,
    OPTICAL_VIEW,
    PRESET_ORDER,
    PartsFrame,
    ViewerScene,
    axis_frame,
    camera_angles,
    camera_from_angles,
    camera_presets,
    focus_frame,
    view_document,
)
from .model import scene_summary as _summary_of
from .optical import ViewerInputError, optical_view_scene
from .page import page_files, standalone_html
from .server import ViewerServer
from .snapshots import DEFAULT_HEIGHT, DEFAULT_VIEWS, DEFAULT_WIDTH, Shot, snapshot_images, write_snapshots

SceneSource = ViewerScene | str | Path | Any


def load_view(source: SceneSource) -> ViewerScene:
    """A ``ViewerScene`` from a run directory (``os-optics project run --out-dir``), a verified
    ``AssemblyPreviewPackageV1``, or an existing ``ViewerScene``."""

    if isinstance(source, ViewerScene):
        return source
    if isinstance(source, (str, Path)):
        return optical_view_scene(source)
    return assembly_view_scene(source)


def scene_summary(source: SceneSource) -> dict[str, Any]:
    """Parts by class (ids, names, SKUs, bounding boxes in mm), beam paths, markers, camera presets, units and axes."""

    return _summary_of(load_view(source))


def render_snapshots(source: SceneSource, out_dir: str | Path, *, views=DEFAULT_VIEWS, width: int = DEFAULT_WIDTH,
                     height: int = DEFAULT_HEIGHT, labels: bool = True, shots=(), frame=None, background=None,
                     size_mode: str = "fit") -> dict[str, Path]:
    """Write ``<view>.png`` (default iso, top, front, side; also path_horizontal, beam_axis, plane_xy, plane_zx, plane_yz),
    ``<name>.png`` per ``Shot`` (a camera stated by its angle to the axis, elevation and roll), ``scene_summary.json`` and
    ``legend.md`` into ``out_dir``. ``frame``: ``"optics"``, ``"scene"``, a box ``(x0, y0, z0, x1, y1, z1)`` in mm or a
    ``focus_frame``; ``background``: ``"studio"``, ``"transparent"`` (RGBA PNGs) or an ``(r, g, b)`` colour in 0..1;
    ``size_mode``: ``"fit"`` (a framed image takes the frame's aspect within the size) or ``"exact"``. Returns ``{name or
    "summary" / "legend": path}``. NumPy only; no browser or GPU needed."""

    return write_snapshots(load_view(source), out_dir, views, width=width, height=height, labels=labels, shots=shots, frame=frame,
                           background=background, size_mode=size_mode)


def package_view_scene(source: Any, *, checks: bool = True, focus_items: Any = None) -> ViewerScene:
    """The Experimental Lab Assembly of an Assembly Preview Package from any source ``os_optics.assembly`` accepts (a
    ``.zip`` archive, a package directory, bytes, a file map or a verified package), verified first; with ``checks`` its
    parts are coloured by the package's local layout check (``validate_assembly_layout``). No token, no network."""

    from ..assembly import AssemblyPreviewPackageError, load_assembly_preview, validate_assembly_layout

    try:
        package = load_assembly_preview(source)
    except AssemblyPreviewPackageError as exc:
        raise ViewerInputError("not a verifiable assembly preview package: " + "; ".join(exc.problems[:3])) from None
    validation = validate_assembly_layout(package) if checks else None
    return assembly_view_scene(package, validation=validation, focus_items=focus_items)


def plan_view_scene(revision: Any, context: Any, *, findings: Any = None, validation: Any = None, finding_id: str | None = None,
                    focus_items: Any = None) -> ViewerScene:
    """The Experimental Lab Assembly of one local assembly plan revision (``os_optics.assembly_plan``): its LOCAL_PLAN
    preview package (projected here, in memory), coloured by the package's layout check and the plan's findings
    (``findings``, default the revision's recorded ones; each names its part or pair by item id = part ref).
    ``finding_id`` highlights and frames that finding's parts; ``focus_items`` names parts directly. Nothing is
    written; no token, no network."""

    from ..assembly import validate_assembly_layout
    from ..assembly_plan.projection import project_plan, design_bom_lines

    projection = project_plan(revision, context)
    package = projection.package
    if validation is None or validation.package.package_content_hash != package.content_hash:
        validation = validate_assembly_layout(package)
    plan_findings = tuple(revision.findings if findings is None else findings)
    focus = set(focus_items or ())
    if finding_id is not None:
        finding = next((f for f in plan_findings if f.finding_id == finding_id), None)
        if finding is None:
            raise ViewerInputError(f"the plan has no finding {finding_id}")
        parts = set(projection.parts)
        focus |= {ref for ref in finding.item_ids if ref in parts}
        if not focus:
            raise ViewerInputError(f"finding {finding_id} names no part of the assembly view ({', '.join(finding.item_ids) or 'the whole plan'})")
    _, supply, _ = design_bom_lines(context.design_bom)
    return assembly_view_scene(package, validation=validation, findings=plan_findings, focus_items=focus,
                               supply={ref: supply[ref] for ref in projection.parts if ref in supply})


def view_plan(revision: Any, context: Any, *, open_browser: bool = True, port: int = 0, **options: Any) -> ViewerServer:
    """Open a local assembly plan revision's assembly in a local browser page (``plan_view_scene`` options)."""

    return serve_view(plan_view_scene(revision, context, **options), open_browser=open_browser, port=port)


def serve_view(scene: ViewerScene, *, open_browser: bool = True, port: int = 0) -> ViewerServer:
    """Serve one scene's page from memory on 127.0.0.1; optionally open it in the default browser."""

    server = ViewerServer(page_files(view_document(scene)), port=port)
    if open_browser:
        server.open_browser()
    return server


def view_run(run_dir: str | Path, *, open_browser: bool = True, port: int = 0) -> ViewerServer:
    """Open the Optical Simulation View of a run directory in a local browser page. Returns the running server
    (``.url``, ``.close()``, ``.wait()``); it stops when closed or when this Python process ends."""

    return serve_view(optical_view_scene(run_dir), open_browser=open_browser, port=port)


def view_assembly(preview: Any, *, open_browser: bool = True, port: int = 0) -> ViewerServer:
    """Open the Experimental Lab Assembly of a verified ``AssemblyPreviewPackageV1`` (for example the package the
    OpenSci client opened in memory) in a local browser page. The package stays in memory; nothing is written."""

    return serve_view(assembly_view_scene(preview), open_browser=open_browser, port=port)


def write_view_html(run_dir: str | Path, out_path: str | Path) -> Path:
    """Write the run's Optical Simulation View as one self-contained HTML file (three.js included; no server)."""

    path = Path(out_path)
    path.write_bytes(standalone_html(view_document(optical_view_scene(run_dir))))
    return path


__all__ = [
    "ASSEMBLY_VIEW",
    "OPTICAL_VIEW",
    "PRESET_ORDER",
    "AssemblyViewError",
    "PartsFrame",
    "Shot",
    "ViewerInputError",
    "ViewerScene",
    "ViewerServer",
    "assembly_view_scene",
    "axis_frame",
    "camera_angles",
    "camera_from_angles",
    "camera_presets",
    "focus_frame",
    "load_view",
    "optical_view_scene",
    "package_view_scene",
    "part_check_status",
    "plan_view_scene",
    "render_snapshots",
    "scene_summary",
    "serve_view",
    "snapshot_images",
    "view_assembly",
    "view_document",
    "view_plan",
    "view_run",
    "write_view_html",
]
