"""weReci lists as Home Assistant to-do lists.

Two of them: the synced shopping list (List It), and the ingredients of what
is cooking on the cook display — ticking one there is the same check-off as a
tap on the kitchen screen.
"""

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
from .cook_display import STATUS_COOKING, CookDisplay
from .coordinator import ListItem, WereciConfigEntry, WereciListCoordinator
from .entity import CookDisplayEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WereciConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the ingredients list, and the shopping list unless the options turn it off."""
    entities: list[TodoListEntity] = [
        CookIngredients(entry.runtime_data.cook_display, entry, "ingredients")
    ]
    if entry.options.get(CONF_SHOPPING_LIST, True):
        entities.append(WereciShoppingList(entry.runtime_data.list_coordinator, entry))
    else:
        reg = er.async_get(hass)
        if entity_id := reg.async_get_entity_id(
            "todo", DOMAIN, f"{entry.unique_id}_shopping_list"
        ):
            reg.async_remove(entity_id)
    async_add_entities(entities)


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


class CookIngredients(CookDisplayEntity, TodoListEntity):
    """The ingredients of what is cooking, ticked off the same way the screen is.

    Read from the snapshot the sender pushes; a tick sends the toggle intent
    round the relay, exactly as a tap on the receiver's rail does, and the
    repainted snapshot moves the box. Lines cannot be added, renamed or
    removed here — the recipe owns them.
    """

    _attr_supported_features = TodoListEntityFeature.UPDATE_TODO_ITEM

    def __init__(self, display: CookDisplay, entry: WereciConfigEntry, key: str) -> None:
        """Initialize."""
        super().__init__(display, entry, key)

    @property
    def available(self) -> bool:
        """Only while something is cooking."""
        return self._display.status == STATUS_COOKING

    def _lines(self) -> list[tuple[int, dict[str, Any]]]:
        session = self._display.session
        snap = session.snapshot if session else None
        if not snap:
            return []
        out: list[tuple[int, dict[str, Any]]] = []
        for pos, line in enumerate(snap.get("allIngredients") or []):
            if not isinstance(line, dict):
                continue
            # `i` names the line in the recipe's own order — what a check-off
            # has to say. Older senders leave it out; then position is it.
            i = line.get("i")
            out.append((i if isinstance(i, int) else pos, line))
        return out

    @property
    def todo_items(self) -> list[TodoItem] | None:
        """The rail, in the recipe's order."""
        if self._display.status != STATUS_COOKING:
            return None
        return [
            TodoItem(
                uid=str(i),
                summary=str(line.get("text") or ""),
                status=(
                    TodoItemStatus.COMPLETED
                    if line.get("checked")
                    else TodoItemStatus.NEEDS_ACTION
                ),
                description=line.get("note") or None,
            )
            for i, line in self._lines()
        ]

    async def async_update_todo_item(self, item: TodoItem) -> None:
        """A tick or untick is one toggle; anything else is refused."""
        current = next(
            (line for i, line in self._lines() if str(i) == item.uid), None
        )
        if current is None or item.uid is None:
            raise ServiceValidationError("That ingredient is not on the screen")
        if item.summary and item.summary != current.get("text"):
            raise ServiceValidationError(
                "Ingredients come from the recipe and cannot be renamed here"
            )
        checked = item.status == TodoItemStatus.COMPLETED
        if checked != bool(current.get("checked")):
            await self._display.async_command({"do": "toggle", "i": int(item.uid)})
