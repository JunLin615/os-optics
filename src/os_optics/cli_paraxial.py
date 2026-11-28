"""``os-optics project paraxial PROJECT --components A [B ...] --wavelength-nm W [--solve afocal|collimate]``: thick-lens and
mirror paraxial analysis of placed components (``os_optics.local.paraxial``); ``os-optics project cavity PROJECT --components M1 [...] M2
--wavelength-nm W``: stability, eigenmode, resonances and injected build-up of a linear cavity (``os_optics.local.cavity``)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

USAGE_ERROR = 64
REFUSED = 65
PARAXIAL_DESCRIPTION = (
    "Paraxial analysis of components of a Project that lie on one optical axis, with each element's real prescription and "
    "material dispersion at the given wavelength (the Engine's own thick-lens matrices): per element and for the system the "
    "reduced ABCD matrix, effective focal length, back focal distance, front focal distance and principal planes, and the "
    "focal points in the design frame. Curved mirrors at normal incidence and plane fold mirrors at any tilt are analysed on "
    "the unfolded axis: distances run along the light's path, design-frame points lie on the folded path ('axis_path' lists "
    "its stretches, 'conventions' states the rules). A COMPOUND unit is composed from its members' poses: lenses in pose "
    "order, a unit with a mirror in the order it declares its members. '--solve afocal' (two components) gives the vertex gap that makes the pair afocal and "
    "where to move the second component; '--solve collimate' gives the distance from a point source or Gaussian waist to the "
    "first entrance vertex at the front focal point and, with '--source', where to move the components and each source "
    "axis's output beam: waist, far-field divergence and whether it leaves collimated or focused (a source whose Rayleigh "
    "range exceeds the focal length is focused). '--axis X|Y' analyses one section of a system with cylindrical surfaces; "
    "the sections are the source's x / y with '--source', else the first component's local X / Y. Components are listed in "
    "the order light meets them, a lens the light returns through after a mirror twice ('--components L1 M1 L1'), a mirror once "
    "(light that returns to a mirror circulates: 'project cavity'); light travels in air between them. The light path decides "
    "the light's direction at the first element, with or without '--source': the named source's emission direction, else that "
    "of the Project's source whose emission line passes through the first element, so a list against that light is refused "
    "the same way both times. With no source on that line, or with sources there that disagree (light_direction.notes names them), "
    "the direction is assumed and the reply says so (light_direction.rule "
    "ASSUMED): from the first element to the last, a curved first mirror met on its reflecting side along its axis, a plane first "
    "mirror from the direction that reflects towards the next element; a reversed list is then the same elements met by light "
    "travelling the other way. Run the Project afterwards ('project run') for the traced beams."
)
PARAXIAL_EPILOG = ("exit codes: 0 = answered (JSON on stdout), 64 = unreadable file, 65 = a request or Project the analysis cannot "
                   "answer (JSON with status ERROR and structured problems)")
CAVITY_DESCRIPTION = (
    "Analysis of a linear (standing-wave) cavity of a Project: its elements listed once each from one end mirror to the other "
    "('--components M1 M2', or 'M1 FM L1 M2' with fold mirrors and lenses between). The elements are placed as 'project paraxial' "
    "places them (real prescriptions at the wavelength, the unfolded axis); the end mirrors meet the light at normal incidence. "
    "From the round-trip ray-transfer matrix: the stability half trace (A + D) / 2 and g1 g2 (g1, g2 of two bare mirrors), the "
    "fundamental Gaussian eigenmode (its radius at every element, its waist in every gap), the round-trip Gouy phase, the free "
    "spectral range c / round-trip group path and the transverse-mode spacing; from the mirrors' reflectances (their coatings', "
    "or '--reflectance ID=R'): the round-trip power fraction, the finesse and the Airy linewidth. '--input-waist-mm' with "
    "'--input-waist-after-mm' states a TEM00 beam at the first mirror, travelling into the cavity, and adds its mode-matching "
    "efficiency. An unstable or marginally stable cavity has no Gaussian eigenmode: those quantities are OUT_OF_MODEL_SCOPE with "
    "the reason, the others keep their values; where losses besides the mirrors' are not modelled the finesse is an upper "
    "bound ('--round-trip-loss' states them). Injection ('--inject-power-w' or '--inject-source', with '--inject-beam'): light fed "
    "through the first listed mirror, its transmittance the coating's or '--transmittance ID=T'; the steady-state injected, "
    "circulating, transmitted (through the last mirror), reflected and lost powers, the build-up, transmission and reflection, on "
    "resonance and at the detuning ('--detuning-hz' from the nearest TEM00 resonance, or '--length-offset-nm', the cavity's length "
    "less that resonance's), with the power budget. A Project's positions do not fix the detuning (the round-trip phase turns by "
    "2 pi per half wavelength of length): without either option the powers are bounded over every detuning and the values with "
    "TEM00 on resonance are listed apart. EIGENMODE injects '--inject-power-w' matched to the fundamental mode; INPUT_BEAM the "
    "stated input beam; SOURCE the beam of '--inject-source', traced to the first mirror (its arriving power; with anything else on "
    "its way the powers are bounded by that power and the mode matching is outside the model) and carried through the mirror's substrate, with "
    "its mode matching. A beam that is not the eigenmode is split "
    "into the cavity's Hermite-Gauss modes up to '--mode-order-max', each at its Gouy-shifted resonance; the light in higher modes "
    "is bounded. A source with M^2 > 1 (a higher-order or multimode beam) or a beam the input mirror's aperture cuts is bounded as light not "
    "resolved; a pulsed source or one broader than '--source-linewidth-fraction-max' of the cavity's linewidth is bounded over every detuning; "
    "the resonant quantities stand on the mirrors alone. '--request FILE' gives the whole request as JSON (also a detuning scan and targets on the quantities, assessed "
    "with the Project's targets of a cavity metric such as cavity_transmission, cavity_circulating_power_W or cavity_power_build_up). "
    "Output: an analysis document (JSON; "
    "'json_schema(\"analysis-result\")')."
)
CAVITY_EPILOG = ("exit codes: 0 = the analysis was answered (each quantity states its own state: one outside its model or not known "
                 "carries the reason in the JSON), 1 = the request was refused (JSON with status ERROR and structured problems), "
                 "64 = unreadable file or usage error")
#: Exit code of a refused 'project cavity' request.
ANALYSIS_REFUSED = 1
#: The one-line pointer 'project example --help' shows.
EXAMPLE_POINTER = ("To space lenses of your own choice: 'os-optics project paraxial PROJECT --components L1 L2 --wavelength-nm W "
                   "--solve afocal' gives the thick-lens afocal spacing, '--solve collimate --source SRC' the collimating distance.")


def register_project(project_commands: argparse._SubParsersAction) -> None:
    """Add ``os-optics project paraxial``."""

    paraxial = project_commands.add_parser("paraxial", help="thick-lens and mirror ABCD, focal distances, principal planes, afocal and collimation "
                                                            "spacing of placed components", description=PARAXIAL_DESCRIPTION,
                                           epilog=PARAXIAL_EPILOG)
    paraxial.add_argument("project", help="path to a Project JSON file")
    paraxial.add_argument("--components", nargs="+", required=True, metavar="INSTANCE_ID",
                          help="the components, in the order light meets them; one the light returns through after a mirror is listed again")
    paraxial.add_argument("--wavelength-nm", required=True, help="vacuum wavelength (nm)")
    paraxial.add_argument("--solve", choices=["afocal", "collimate"], help="afocal: the vertex gap of two elements for an afocal pair; "
                                                                          "collimate: the collimating object distance")
    paraxial.add_argument("--source", dest="source_id", help="a source of the Project: its emission direction is the light's at the first element, and its "
                                                            "waist the object of --solve collimate (without it, the Project's source on the line through "
                                                            "the first element decides the direction)")
    paraxial.add_argument("--axis", choices=["X", "Y"], help="the section of the system to analyse (cylindrical surfaces): the source's x / y with --source, else the first "
                               "component's local X / Y")
    paraxial.add_argument("--configuration", dest="configuration_id", metavar="ID", help="the configuration to analyse, of a Project that declares configurations (its component and plane poses, source models, budget and settings)")
    paraxial.add_argument("--lateral-tolerance-mm", help="how far an element may lie off the optical axis and be analysed on it (default 1e-6 mm); "
                                                         "the reply states each element's measured decentre_mm")
    paraxial.add_argument("--tilt-tolerance-rad", help="how far an element's axis may be tilted to the optical axis and be analysed on it (default "
                                                       "about 1.4e-6 rad; a curved mirror only at normal incidence, whatever this says); the reply states "
                                                       "each element's measured tilt_rad")
    paraxial.add_argument("--catalog", help="use this public optical catalog JSON instead of the release the Project pins / the active one")
    paraxial.set_defaults(handler=_paraxial)
    pupil = project_commands.add_parser("pupil", help="trace geometric field rays across the declared system aperture")
    pupil.add_argument("project", help="path to a Project JSON file")
    pupil.add_argument("--source", required=True, help="field source instance id")
    pupil.add_argument("--sample", type=float, nargs=2, action="append", metavar=("X", "Y"),
                       help="normalized pupil coordinate, repeatable (default center and cardinal rim points)")
    pupil.add_argument("--wavelength-nm", type=float, help="select one emitted vacuum wavelength or the primary reference")
    pupil.add_argument("--configuration", help="configuration id of a Project with multiple states")
    pupil.add_argument("--catalog", help="public optical catalog JSON")
    pupil.set_defaults(handler=_pupil)
    cavity = project_commands.add_parser("cavity", help="stability, Gaussian eigenmode, Gouy phase, free spectral range, transverse-mode spacing, finesse, "
                                                        "mode matching and the resonant build-up of injected light of a linear cavity of placed mirrors",
                                         description=CAVITY_DESCRIPTION, epilog=CAVITY_EPILOG)
    cavity.add_argument("project", help="path to a Project JSON file")
    cavity.add_argument("--request", help="a JSON file with the whole request (json_api cavity_analysis); the options below override its fields")
    cavity.add_argument("--components", nargs="+", metavar="INSTANCE_ID",
                        help="the cavity's elements once each, from one end mirror to the other (the input mirror first when light is injected)")
    cavity.add_argument("--wavelength-nm", help="vacuum wavelength (nm)")
    cavity.add_argument("--axis", choices=["X", "Y"], help="the section of a cavity with cylindrical surfaces (the first mirror's local X / Y)")
    cavity.add_argument("--reflectance", action="append", default=[], metavar="ID=R",
                        help="a mirror's power reflectance at this wavelength, instead of its coating's (repeat for each mirror)")
    cavity.add_argument("--input-waist-mm", help="waist radius (mm) of a TEM00 beam entering the cavity at the first mirror")
    cavity.add_argument("--input-waist-y-mm", help="its waist radius along the second section axis, when it differs")
    cavity.add_argument("--input-waist-after-mm", help="the beam's waist distance (mm) from the first mirror's vertex along the light, positive inside the cavity")
    cavity.add_argument("--polarization", choices=["ORDINARY", "EXTRAORDINARY", "S", "P"],
                        help="with a crystal in the cavity: the eigen-polarization to analyse (across or along its optic axis); S / P: "
                             "perpendicular to / in the common plane of incidence of the cavity's tilted mirrors, where their coatings "
                             "reflect the two differently")
    cavity.add_argument("--transmittance", action="append", default=[], metavar="ID=T",
                        help="an end mirror's power transmittance at this wavelength, instead of its coating's (with --reflectance for it)")
    cavity.add_argument("--round-trip-loss", help="power fraction lost per round trip besides the mirrors' reflectances (intracavity elements)")
    cavity.add_argument("--aperture-over-mode-min", help="the least clear aperture radius over the mode radius at which an aperture counts as "
                                                         "not reaching the mode (default 3)")
    cavity.add_argument("--inject-power-w", help="inject this power (W) through the first mirror")
    cavity.add_argument("--inject-source", help="inject the light of this source of the Project (on the axis behind the first mirror, nothing else "
                                                "on its way): the power the trace delivers to the mirror and its beam (--inject-beam SOURCE)")
    cavity.add_argument("--inject-beam", choices=["EIGENMODE", "INPUT_BEAM", "SOURCE"],
                        help="the injected beam: the cavity's fundamental mode (with --inject-power-w), the --input-waist beam, or the beam of "
                             "--inject-source; the last two are split into the cavity's modes")
    cavity.add_argument("--detuning-hz", help="the light's frequency less that of the nearest TEM00 resonance (Hz). Without it (or "
                                              "--length-offset-nm) the detuning is not known: a Project's positions do not fix it, and the "
                                              "powers are bounded over every detuning")
    cavity.add_argument("--length-offset-nm", help="the cavity's length less the length of the nearest TEM00 resonance (nm): a round-trip "
                                                   "phase of 2 pi x 2 offset / wavelength")
    cavity.add_argument("--mode-order-max", type=int, help="the highest transverse order m + n resolved (default 10)")
    cavity.add_argument("--configuration", dest="configuration_id", metavar="ID", help="the configuration to analyse, of a Project that declares configurations (its component and plane poses, source models, budget and settings)")
    cavity.add_argument("--source-linewidth-fraction-max", help="the largest share of the cavity's linewidth the source's linewidth may have and "
                                                                "count as one frequency (default 0.01); above it the powers are bounded over "
                                                                "every detuning")
    cavity.add_argument("--lateral-tolerance-mm", help="how far an element may lie off the cavity's axis and be analysed on it (default 1e-6 mm)")
    cavity.add_argument("--tilt-tolerance-rad", help="how far an element's axis may be tilted to the cavity's axis and be analysed on it (default "
                                                     "about 1.4e-6 rad; a curved mirror only at normal incidence, whatever this says)")
    cavity.add_argument("--catalog", help="use this public optical catalog JSON instead of the release the Project pins / the active one")
    cavity.set_defaults(handler=_cavity)


def _coaxial_tolerance(args: argparse.Namespace) -> dict:
    """The request's ``coaxial_tolerance`` from ``--lateral-tolerance-mm`` / ``--tilt-tolerance-rad`` (empty: none stated)."""

    return {key: value for key, value in (("lateral_mm", args.lateral_tolerance_mm), ("tilt_rad", args.tilt_tolerance_rad)) if value is not None}


def _pupil(args: argparse.Namespace) -> int:
    from .local import LocalRunError, ParaxialError, trace_system_pupil

    try:
        document = json.loads(Path(args.project).read_text(encoding="utf-8"))
        catalog = Path(args.catalog).read_bytes() if args.catalog else None
    except (OSError, ValueError) as exc:
        print(f"error: cannot read the input: {exc}")
        return USAGE_ERROR
    try:
        samples = {} if args.sample is None else {"pupil_coordinates": args.sample}
        result = trace_system_pupil(document, source_id=args.source, wavelength_nm=args.wavelength_nm,
                                    configuration_id=args.configuration, catalog=catalog, **samples)
    except (LocalRunError, ParaxialError) as exc:
        print(json.dumps({"status": "ERROR", **exc.as_dict()}, indent=2))
        return REFUSED
    except ValueError as exc:
        print(json.dumps({"status": "ERROR", "message": str(exc)}, indent=2))
        return REFUSED
    print(json.dumps(result, indent=2, ensure_ascii=True))
    return 0


def _paraxial(args: argparse.Namespace) -> int:
    from .local.json_api import paraxial_analysis

    try:
        document = json.loads(Path(args.project).read_text(encoding="utf-8"))
        catalog = Path(args.catalog).read_bytes() if args.catalog else None
    except (OSError, ValueError) as exc:
        print(f"error: cannot read the input: {exc}")
        return USAGE_ERROR
    request = {key: value for key, value in (("components", args.components), ("wavelength_nm", args.wavelength_nm),
                                             ("solve", args.solve), ("source_id", args.source_id), ("axis", args.axis),
                                             ("configuration_id", args.configuration_id)) if value is not None}
    tolerance = _coaxial_tolerance(args)
    if tolerance:
        request["coaxial_tolerance"] = tolerance
    reply = paraxial_analysis(document, request, catalog=catalog)
    if not reply["ok"]:
        print(json.dumps({"status": "ERROR", "error": reply["error"]}, indent=2, ensure_ascii=True))
        return REFUSED
    print(json.dumps({key: value for key, value in reply.items() if key != "ok"}, indent=2, ensure_ascii=True))
    return 0


def _cavity(args: argparse.Namespace) -> int:
    from .local.json_api import cavity_analysis

    try:
        document = json.loads(Path(args.project).read_text(encoding="utf-8"))
        catalog = Path(args.catalog).read_bytes() if args.catalog else None
    except (OSError, ValueError) as exc:
        print(f"error: cannot read the input: {exc}")
        return USAGE_ERROR
    try:
        request: dict = {} if args.request is None else json.loads(Path(args.request).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"error: cannot read the request: {exc}")
        return USAGE_ERROR
    if not isinstance(request, dict):
        print("error: the request file holds a JSON object")
        return USAGE_ERROR
    for flag, name in (("reflectance", "reflectances"), ("transmittance", "transmittances")):
        for item in getattr(args, flag):
            key, sep, value = item.partition("=")
            if not sep or not key:
                print(f"error: --{flag} takes ID=value, not {item!r}")
                return USAGE_ERROR
            request.setdefault(name, {})[key] = value
    for key, value in (("components", args.components), ("wavelength_nm", args.wavelength_nm), ("axis", args.axis),
                       ("polarization", args.polarization), ("round_trip_loss", args.round_trip_loss),
                       ("configuration_id", args.configuration_id), ("aperture_over_mode_min", args.aperture_over_mode_min)):
        if value is not None:
            request[key] = value
    if "components" not in request or "wavelength_nm" not in request:
        print("error: state --components and --wavelength-nm (or give them in --request)")
        return USAGE_ERROR
    tolerance = _coaxial_tolerance(args)
    if tolerance:
        request["coaxial_tolerance"] = {**(request.get("coaxial_tolerance") or {}), **tolerance}
    injection = {key: value for key, value in (("power_w", args.inject_power_w), ("source_id", args.inject_source), ("beam", args.inject_beam),
                                               ("detuning_Hz", args.detuning_hz), ("length_offset_nm", args.length_offset_nm),
                                               ("mode_order_max", args.mode_order_max),
                                               ("source_linewidth_fraction_max", args.source_linewidth_fraction_max)) if value is not None}
    if injection:
        request["injection"] = {**(request.get("injection") or {}), **injection}
    if args.input_waist_mm is not None or args.input_waist_after_mm is not None or args.input_waist_y_mm is not None:
        if args.input_waist_mm is None or args.input_waist_after_mm is None:
            print("error: an input beam needs both --input-waist-mm and --input-waist-after-mm")
            return USAGE_ERROR
        request["input_beam"] = {"waist_radius_mm": args.input_waist_mm, "waist_after_reference_mm": args.input_waist_after_mm,
                                 **({"waist_radius_y_mm": args.input_waist_y_mm} if args.input_waist_y_mm is not None else {})}
    reply = cavity_analysis(document, request, catalog=catalog)
    if not reply["ok"]:
        print(json.dumps({"status": "ERROR", "error": reply["error"]}, indent=2, ensure_ascii=True))
        return ANALYSIS_REFUSED
    doc = {key: value for key, value in reply.items() if key != "ok"}
    print(json.dumps(doc, indent=2, ensure_ascii=True))
    return 0
