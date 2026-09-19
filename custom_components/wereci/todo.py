"""The synced weReci shopping list as a Home Assistant to-do list."""

from __future__ import annotations

from typing import Any

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_SHOPPING_LIST, DOMAIN
from .coordinator import ListItem, WereciConfigEntry, WereciListCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WereciConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the list entity, unless the account's options turn it off."""
    if not entry.options.get(CONF_SHOPPING_LIST, True):
        reg = er.async_get(hass)
        if entity_id := reg.async_get_entity_id(
            "todo", DOMAIN, f"{entry.unique_id}_shopping_list"
        ):
            reg.async_remove(entity_id)
        return
    async_add_entities([WereciShoppingList(entry.runtime_data.list_coordinator, entry)])


def _description(item: ListItem) -> str | None:
    parts = [item.aisle] if item.aisle else []
    if item.recipes:
        parts.append(", ".join(item.recipes))
    return " · ".join(parts) or None


class WereciShoppingList(CoordinatorEntity[WereciListCoordinator], TodoListEntity):
    """The list a weReci cookbook pair shares.

    Only a list weReci holds server-side can appear here. A list kept on one
    phone never does — then this entity is unavailable, and `reason` says why.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "shopping_list"
    _attr_supported_features = (
        TodoListEntityFeature.CREATE_TODO_ITEM
        | TodoListEntityFeature.UPDATE_TODO_ITEM
        | TodoListEntityFeature.DELETE_TODO_ITEM
    )

    def __init__(
        self, coordinator: WereciListCoordinator, entry: WereciConfigEntry
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.unique_id}_shopping_list"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(entry.unique_id))},
            name=f"weReci ({entry.title})",
            manufacturer="weReci",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://wereci.xyz",
        )

    @property
    def available(self) -> bool:
        """Unavailable when there is no synced list to show."""
        return super().available and self.coordinator.data.available

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Why there is no list, when there isn't."""
        reason = self.coordinator.data.reason
        return {"reason": reason} if reason else None

    @property
    def todo_items(self) -> list[TodoItem] | None:
        """The list, to-get first then crossed off, in weReci's own order."""
        if not self.coordinator.data.available:
            return None
        return [
            TodoItem(
                uid=item.key,
                summary=item.text,
                status=(
                    TodoItemStatus.COMPLETED
                    if item.checked
                    else TodoItemStatus.NEEDS_ACTION
                ),
                description=_description(item),
            )
            for item in self.coordinator.data.items
        ]

    async def async_create_todo_item(self, item: TodoItem) -> None:
        """Add a plain line."""
        if not item.summary:
            raise ServiceValidationError("A line needs some text")
        await self.coordinator.async_change(add=[item.summary])

    async def async_update_todo_item(self, item: TodoItem) -> None:
        """Tick / untick; a rename is the old line off and the new one on."""
        current = next(
            (i for i in self.coordinator.data.items if i.key == item.uid), None
        )
        if current is None or item.uid is None:
            raise ServiceValidationError("That line is no longer on the list")
        if item.summary and item.summary != current.text:
            # weReci keys a line on its ingredient, so new text is a new line.
            await self.coordinator.async_change(
                remove=[item.uid], add=[item.summary]
            )
            return
        checked = item.status == TodoItemStatus.COMPLETED
        if checked != current.checked:
            await self.coordinator.async_change(
                **{"check" if checked else "uncheck": [item.uid]}
            )

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        """Take lines off the list (weReci keeps an undoable tombstone)."""
        await self.coordinator.async_change(remove=uids)
