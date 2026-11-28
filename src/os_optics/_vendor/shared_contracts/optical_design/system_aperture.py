"""The declared system aperture of a centered sequential design in its initial +Z frame."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from ..base import StrictContractModel


class SystemApertureV1(StrictContractModel):
    """Object-side pupil used to launch geometric analysis rays, with the primary vacuum wavelength for pupil aiming.

    ``value`` is a diameter in mm, a finite object's numerical aperture, or a paraxial image-space f-number. For
    ``FLOAT_BY_STOP`` the physical stop sets the pupil and ``entrance_pupil_radius_mm`` gives its entrance image radius.
    The entrance pupil plane is perpendicular to the design Z axis and centered on it. Its Z coordinate may be virtual.
    Component surfaces, holes and stops continue to clip the rays in the geometric trace.
    """

    kind: Literal["ENTRANCE_PUPIL_DIAMETER", "OBJECT_NA", "IMAGE_F_NUMBER", "FLOAT_BY_STOP"]
    value: float | None = Field(None, gt=0, allow_inf_nan=False)
    object_conjugate: Literal["FINITE", "INFINITE"]
    entrance_pupil_z_mm: float = Field(allow_inf_nan=False)
    primary_wavelength_nm: float = Field(gt=0, allow_inf_nan=False)
    entrance_pupil_radius_mm: float | None = Field(None, gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _definition(self) -> SystemApertureV1:
        if self.kind == "FLOAT_BY_STOP":
            if self.value is not None or self.entrance_pupil_radius_mm is None:
                raise ValueError("FLOAT_BY_STOP requires the projected stop radius and takes no numeric aperture value")
        elif self.value is None or self.entrance_pupil_radius_mm is not None:
            raise ValueError(f"{self.kind} requires its declared value and derives its entrance pupil radius")
        if self.kind == "OBJECT_NA" and self.object_conjugate != "FINITE":
            raise ValueError("OBJECT_NA requires a finite object")
        return self
