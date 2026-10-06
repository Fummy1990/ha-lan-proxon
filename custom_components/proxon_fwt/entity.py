"""Base entities for Proxon FWT."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_DEVICE_ID, DOMAIN
from .coordinator import ProxonCoordinator, Room


class ProxonEntity(CoordinatorEntity[ProxonCoordinator]):
    """Entity attached to the main device."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: ProxonCoordinator, key: str, name: str | None = None, *,
        translation_key: str | None = None, placeholders: dict[str, str] | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._device_id: str = coordinator.config_entry.data[CONF_DEVICE_ID]
        self._attr_unique_id = f"{self._device_id}:{key}"
        # Names come from translations (strings.json / translations/*.json); `name` is kept
        # only as documentation of the German default.
        self._attr_translation_key = translation_key or key
        if placeholders:
            self._attr_translation_placeholders = placeholders

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, self._device_id)})


class ProxonRoomEntity(ProxonEntity):
    """Entity attached to a room device."""

    def __init__(self, coordinator: ProxonCoordinator, room_index: int, key: str, name: str | None = None) -> None:
        super().__init__(coordinator, f"room{room_index}_{key}", name, translation_key=f"room_{key}")
        self.room_index = room_index

    @property
    def room(self) -> Room:
        return self.coordinator.data.rooms[self.room_index]

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, f"{self._device_id}_room_{self.room_index}")})

    @property
    def available(self) -> bool:
        return super().available and self.room.connected
