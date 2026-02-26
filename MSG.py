from pydantic import BaseModel
from typing import Optional, Literal

class Air(BaseModel):
    t_c: Optional[float]
    rh_pct: Optional[float]
    p_hpa: Optional[float]

class Water(BaseModel):
    t_c: Optional[float]
    ph: Optional[float]
    ec_ms_cm: Optional[float]

class Light(BaseModel):
    lux: Optional[int]

class Level(BaseModel):
    float: Optional[int]

class SensorEvent(BaseModel):
    type: Literal["sensor"]
    ts: str
    device: str
    seq: int
    air: Optional[Air] = None
    water: Optional[Water] = None
    light: Optional[Light] = None
    level: Optional[Level] = None


def validate_event(data) -> SensorEvent:
    """Validate and return a SensorEvent pydantic model."""
    return SensorEvent.parse_obj(data)
