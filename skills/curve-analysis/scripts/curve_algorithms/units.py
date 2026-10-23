"""Small, explicit unit contract for deterministic scientific thresholds."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


UnitDimension = Literal[
    "dimensionless",
    "log10_ratio",
    "voltage",
    "length",
    "time",
    "current",
    "current_density",
    "temperature",
    "diffusivity",
    "concentration",
    "electric_field",
    "energy",
    "mobility",
    "velocity",
    "permittivity",
    "mass_density",
    "thermal_conductivity",
    "resistance",
    "resistivity",
    "capacitance",
    "charge",
    "frequency",
]


@dataclass(frozen=True)
class UnitDefinition:
    dimension: UnitDimension
    scale_to_si: float
    offset_to_si: float = 0.0


_CANONICAL_UNITS: dict[str, UnitDefinition] = {
    "1": UnitDefinition("dimensionless", 1.0),
    "decade": UnitDefinition("log10_ratio", 1.0),
    "V": UnitDefinition("voltage", 1.0),
    "mV": UnitDefinition("voltage", 1e-3),
    "uV": UnitDefinition("voltage", 1e-6),
    "kV": UnitDefinition("voltage", 1e3),
    "m": UnitDefinition("length", 1.0),
    "cm": UnitDefinition("length", 1e-2),
    "mm": UnitDefinition("length", 1e-3),
    "um": UnitDefinition("length", 1e-6),
    "nm": UnitDefinition("length", 1e-9),
    "s": UnitDefinition("time", 1.0),
    "ms": UnitDefinition("time", 1e-3),
    "us": UnitDefinition("time", 1e-6),
    "ns": UnitDefinition("time", 1e-9),
    "min": UnitDefinition("time", 60.0),
    "h": UnitDefinition("time", 3600.0),
    "A": UnitDefinition("current", 1.0),
    "mA": UnitDefinition("current", 1e-3),
    "uA": UnitDefinition("current", 1e-6),
    "nA": UnitDefinition("current", 1e-9),
    "pA": UnitDefinition("current", 1e-12),
    "A/m^2": UnitDefinition("current_density", 1.0),
    "A/cm^2": UnitDefinition("current_density", 1e4),
    "mA/cm^2": UnitDefinition("current_density", 10.0),
    "uA/cm^2": UnitDefinition("current_density", 1e-2),
    "nA/cm^2": UnitDefinition("current_density", 1e-5),
    "K": UnitDefinition("temperature", 1.0),
    "degC": UnitDefinition("temperature", 1.0, 273.15),
    "m^2/s": UnitDefinition("diffusivity", 1.0),
    "cm^2/s": UnitDefinition("diffusivity", 1e-4),
    "m^-3": UnitDefinition("concentration", 1.0),
    "cm^-3": UnitDefinition("concentration", 1e6),
    "V/m": UnitDefinition("electric_field", 1.0),
    "V/cm": UnitDefinition("electric_field", 1e2),
    "J": UnitDefinition("energy", 1.0),
    "eV": UnitDefinition("energy", 1.602176634e-19),
    "meV": UnitDefinition("energy", 1.602176634e-22),
    "m^2/(V*s)": UnitDefinition("mobility", 1.0),
    "cm^2/(V*s)": UnitDefinition("mobility", 1e-4),
    "m/s": UnitDefinition("velocity", 1.0),
    "cm/s": UnitDefinition("velocity", 1e-2),
    "F/m": UnitDefinition("permittivity", 1.0),
    "F/cm": UnitDefinition("permittivity", 1e2),
    "kg/m^3": UnitDefinition("mass_density", 1.0),
    "g/cm^3": UnitDefinition("mass_density", 1e3),
    "W/(m*K)": UnitDefinition("thermal_conductivity", 1.0),
    "W/(cm*K)": UnitDefinition("thermal_conductivity", 1e2),
    "ohm": UnitDefinition("resistance", 1.0),
    "ohm*m": UnitDefinition("resistivity", 1.0),
    "ohm*cm": UnitDefinition("resistivity", 1e-2),
    "F": UnitDefinition("capacitance", 1.0),
    "pF": UnitDefinition("capacitance", 1e-12),
    "C": UnitDefinition("charge", 1.0),
    "Hz": UnitDefinition("frequency", 1.0),
    "kHz": UnitDefinition("frequency", 1e3),
    "MHz": UnitDefinition("frequency", 1e6),
    "GHz": UnitDefinition("frequency", 1e9),
}

_ALIASES = {
    "dimensionless": "1",
    "minute": "min",
    "minutes": "min",
    "hr": "h",
    "µV": "uV",
    "μV": "uV",
    "µm": "um",
    "μm": "um",
    "µs": "us",
    "μs": "us",
    "µA": "uA",
    "μA": "uA",
    "A/m2": "A/m^2",
    "A/cm2": "A/cm^2",
    "mA/cm2": "mA/cm^2",
    "uA/cm2": "uA/cm^2",
    "µA/cm^2": "uA/cm^2",
    "μA/cm^2": "uA/cm^2",
    "µA/cm2": "uA/cm^2",
    "μA/cm2": "uA/cm^2",
    "nA/cm2": "nA/cm^2",
    "°C": "degC",
    "Celsius": "degC",
    "m2/s": "m^2/s",
    "cm2/s": "cm^2/s",
    "1/m3": "m^-3",
    "1/cm3": "cm^-3",
    "cm2/(V*s)": "cm^2/(V*s)",
    "cm^2/V/s": "cm^2/(V*s)",
    "m2/(V*s)": "m^2/(V*s)",
    "m^2/V/s": "m^2/(V*s)",
    "Ω": "ohm",
    "Ohm": "ohm",
    "Ω*m": "ohm*m",
    "Ω*cm": "ohm*cm",
}


def unit_definition(value: str, *, label: str) -> UnitDefinition:
    canonical = _ALIASES.get(value, value)
    try:
        return _CANONICAL_UNITS[canonical]
    except KeyError as error:
        raise ValueError(f"unsupported {label}: {value}") from error


def supported_unit_spellings() -> tuple[str, ...]:
    """Return the exact finite vocabulary accepted by ``unit_definition``."""

    return tuple(sorted((*_CANONICAL_UNITS, *_ALIASES)))


def convert_value(value: float, *, source_unit: str, target_unit: str) -> float:
    source = unit_definition(source_unit, label="observed unit")
    target = unit_definition(target_unit, label="threshold unit")
    if source.dimension != target.dimension:
        raise ValueError(
            "incompatible units: "
            f"observed {source_unit} is {source.dimension}, "
            f"threshold {target_unit} is {target.dimension}"
        )
    si_value = value * source.scale_to_si + source.offset_to_si
    return (si_value - target.offset_to_si) / target.scale_to_si


def units_equivalent(left: str, right: str) -> bool:
    """Return whether two unit spellings have identical numeric semantics."""

    return unit_definition(left, label="unit") == unit_definition(
        right, label="unit"
    )


__all__ = [
    "UnitDefinition",
    "convert_value",
    "unit_definition",
    "supported_unit_spellings",
    "units_equivalent",
]
