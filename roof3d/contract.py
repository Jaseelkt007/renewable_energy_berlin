"""roof3d JSON contract — frozen at M2.

This is the single source of truth for the JSON exchanged with the frontend
and the recommendation engine. Any change here must be coordinated with both
teammates. Additive changes only after M2; never rename or remove fields.

Schema version is bumped only on breaking changes.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

SCHEMA_VERSION = "1.0.0"

Vec3 = tuple[float, float, float]


class CoordinateSystem(BaseModel):
    units: Literal["meters", "millimeters", "scene-normalized", "unknown"] = "meters"
    up_axis: Literal["X", "Y", "Z"] = "Z"
    panels_in_original_model_coordinates: bool = True
    unit_scale_applied: float = 1.0


class BBox(BaseModel):
    min: Vec3
    max: Vec3


class ConfidenceReasons(BaseModel):
    area_large_enough: bool = True
    normal_stable: bool = True
    height_valid: bool = True
    polygon_clean: bool = True


class RoofPlane(BaseModel):
    id: str
    source: Literal["auto", "click_seeded", "manual_config"]
    confidence: float = Field(ge=0.0, le=1.0)
    confidence_reasons: ConfidenceReasons = Field(default_factory=ConfidenceReasons)
    centroid: Vec3
    normal: Vec3
    u_axis: Vec3
    v_axis: Vec3
    tilt_deg: float
    azimuth_deg: float
    area_m2: float
    usable_area_m2: float
    panel_count: int
    polygon_3d: list[Vec3]
    usable_polygon_3d: list[Vec3]


class Obstruction(BaseModel):
    id: str
    plane_id: str
    source: Literal["reserve", "detected_bump", "manual"]
    type: str
    area_m2: float
    polygon_3d: list[Vec3] = Field(default_factory=list)


class Panel(BaseModel):
    id: str
    plane_id: str
    center: Vec3
    normal: Vec3
    u_axis: Vec3
    v_axis: Vec3
    width_m: float
    height_m: float
    watt_peak: int
    corners_3d: list[Vec3]

    @field_validator("corners_3d")
    @classmethod
    def _four_corners(cls, v: list[Vec3]) -> list[Vec3]:
        if len(v) != 4:
            raise ValueError("panel.corners_3d must have exactly 4 entries")
        return v


class Summary(BaseModel):
    panel_count: int
    module_wp: int
    system_kwp: float
    best_plane_id: Optional[str] = None
    best_plane_azimuth: Optional[float] = None
    best_plane_tilt: Optional[float] = None
    panels_by_plane: dict[str, int] = Field(default_factory=dict)
    method: str = "mock"
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    warnings: list[str] = Field(default_factory=list)


class Quality(BaseModel):
    method: str
    confidence: float = Field(ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)


class RoofDesign(BaseModel):
    schema_version: str = SCHEMA_VERSION
    project_id: str
    model_file: str
    coordinate_system: CoordinateSystem
    bbox: BBox
    roof_planes: list[RoofPlane]
    obstructions: list[Obstruction] = Field(default_factory=list)
    panels: list[Panel]
    summary: Summary
    quality: Quality

    def to_json(self, **kwargs) -> str:
        kwargs.setdefault("indent", 2)
        return self.model_dump_json(**kwargs)

    @classmethod
    def from_json(cls, text: str) -> "RoofDesign":
        return cls.model_validate_json(text)
