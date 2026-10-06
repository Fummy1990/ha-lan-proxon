"""Number entities: boost duration, hot water and heater targets."""
from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfRatio, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CO2_LIMIT_MAX,
    CO2_LIMIT_MIN,
    FUNC_CO2_LIMIT,
    FUNC_HUMIDITY_LIMIT,
    FUNC_ZONE2_TARGET,
    HUMIDITY_LIMIT_MAX,
    HUMIDITY_LIMIT_MIN,
    ZONE2_TARGET_MAX,
    ZONE2_TARGET_MIN,
    BOOST_DURATION_MAX,
    BOOST_DURATION_MIN,
    FUNC_BOOST_DURATION,
    FUNC_HEATER_TARGET,
    FUNC_WATER_TARGET,
    HEATER_TARGET_MAX,
    HEATER_TARGET_MIN,
    WATER_TARGET_MAX,
    WATER_TARGET_MIN,
    WriteFunc,
)
from .coordinator import ProxonCoordinator, ProxonWriteError
from .entity import ProxonEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: ProxonCoordinator = entry.runtime_data
    async_add_entities(
        [
            ProxonNumber(
                coordinator, FUNC_BOOST_DURATION, "Intensivlüftung Dauer", scale=1,
                minimum=BOOST_DURATION_MIN, maximum=BOOST_DURATION_MAX, step=1,
                unit=UnitOfTime.MINUTES, icon="mdi:timer", category=EntityCategory.CONFIG,
            ),
            ProxonNumber(
                coordinator, FUNC_WATER_TARGET, "Warmwasser Solltemperatur", scale=10,
                minimum=WATER_TARGET_MIN, maximum=WATER_TARGET_MAX, step=1,
                unit=UnitOfTemperature.CELSIUS, icon="mdi:water-thermometer",
                device_class=NumberDeviceClass.TEMPERATURE, category=EntityCategory.CONFIG,
            ),
            ProxonNumber(
                coordinator, FUNC_ZONE2_TARGET, "Zone 2 Solltemperatur", scale=100,
                minimum=ZONE2_TARGET_MIN, maximum=ZONE2_TARGET_MAX, step=0.5,
                unit=UnitOfTemperature.CELSIUS, icon="mdi:thermostat",
                device_class=NumberDeviceClass.TEMPERATURE,
            ),
            ProxonNumber(
                coordinator, FUNC_CO2_LIMIT, "Grenzwert CO₂", scale=1,
                minimum=CO2_LIMIT_MIN, maximum=CO2_LIMIT_MAX, step=50,
                unit=UnitOfRatio.PARTS_PER_MILLION, icon="mdi:molecule-co2",
            ),
            ProxonNumber(
                coordinator, FUNC_HUMIDITY_LIMIT, "Grenzwert Feuchte", scale=1,
                minimum=HUMIDITY_LIMIT_MIN, maximum=HUMIDITY_LIMIT_MAX, step=1,
                unit=PERCENTAGE, icon="mdi:water-percent",
            ),
            ProxonNumber(
                coordinator, FUNC_HEATER_TARGET, "E-Heizstab Solltemperatur", scale=10,
                minimum=HEATER_TARGET_MIN, maximum=HEATER_TARGET_MAX, step=1,
                unit=UnitOfTemperature.CELSIUS, icon="mdi:water-boiler",
                device_class=NumberDeviceClass.TEMPERATURE, category=EntityCategory.CONFIG,
            ),
        ]
    )


class ProxonNumber(ProxonEntity, NumberEntity):
    _attr_mode = NumberMode.BOX

    def __init__(
        self, coordinator: ProxonCoordinator, func: WriteFunc, name: str, *, scale: int,
        minimum: float, maximum: float, step: float, unit: str, icon: str,
        device_class: NumberDeviceClass | None = None, category: EntityCategory | None = None,
    ) -> None:
        super().__init__(coordinator, func.key, name)
        self._func = func
        self._scale = scale
        self._attr_native_min_value = minimum
        self._attr_native_max_value = maximum
        self._attr_native_step = step
        self._attr_native_unit_of_measurement = unit
        self._attr_icon = icon
        self._attr_device_class = device_class
        self._attr_entity_category = category

    @property
    def native_value(self) -> float | None:
        v = self.coordinator.data.sp.get(self._func.read)
        return None if v is None else v / self._scale

    async def async_set_native_value(self, value: float) -> None:
        if not self._attr_native_min_value <= value <= self._attr_native_max_value:
            raise ProxonWriteError(f"{value} außerhalb des erlaubten Bereichs")
        await self.coordinator.async_write(self._func, int(round(value * self._scale)))
