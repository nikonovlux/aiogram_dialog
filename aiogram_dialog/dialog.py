from logging import getLogger
from typing import Dict, Callable, Awaitable, List, Union, Any, Optional, Type
from typing import Protocol

from aiogram import Dispatcher
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, Message, CallbackQuery, ContentTypes

from aiogram_dialog.manager.manager import DialogManager, DialogRegistryProto
from aiogram_dialog.manager.protocols import ManagedDialogProto, DialogManagerProto
from aiogram_dialog.widgets.action import Actionable

logger = getLogger(__name__)
DIALOG_CONTEXT = "DIALOG_CONTEXT"
DataGetter = Callable[..., Awaitable[Dict]]

ChatEvent = Union[CallbackQuery, Message]
OnProcessResult = Callable[[Any, DialogManager], Awaitable]


class DialogWindowProto(Protocol):
    async def render_text(self, data: Dict, manager: DialogManager) -> str:
        raise NotImplementedError

    async def render_kbd(self, data: Dict, manager: DialogManager) -> InlineKeyboardMarkup:
        raise NotImplementedError

    async def load_data(self, dialog: "Dialog", manager: DialogManager) -> Dict:
        raise NotImplementedError

    async def process_message(self, m: Message, dialog: "Dialog", manager: DialogManager):
        raise NotImplementedError

    async def process_callback(self, c: CallbackQuery, dialog: "Dialog", manager: DialogManager):
        raise NotImplementedError

    async def show(self, dialog: "Dialog", manager: DialogManager) -> Message:
        raise NotImplementedError

    def get_state(self) -> State:
        raise NotImplementedError

    def find(self, widget_id) -> Optional[Actionable]:
        raise NotImplementedError


class Dialog(ManagedDialogProto):
    def __init__(self, *windows: DialogWindowProto, on_process_result: Optional[OnProcessResult] = None):
        self._states_group = windows[0].get_state().group
        self.states: List[State] = []
        for w in windows:
            if w.get_state().group != self._states_group:
                raise ValueError("All windows must be attached to same StatesGroup")
            state = w.get_state()
            if state in self.states:
                raise ValueError(f"Multiple windows with state {state}")
            self.states.append(state)
        self.windows: Dict[State, DialogWindowProto] = dict(zip(self.states, windows))
        self.on_process_result = on_process_result

    async def next(self, manager: DialogManager):
        new_state = self.states[self.states.index(manager.context.state) + 1]
        await self.switch_to(new_state, manager)

    async def back(self, manager: DialogManager):
        new_state = self.states[self.states.index(manager.context.state) - 1]
        await self.switch_to(new_state, manager)

    async def start(self, manager: DialogManagerProto, state: Optional[State] = None) -> None:
        if state is None:
            state = self.states[0]
        logger.debug("Dialog start: %s (%s)", state, self)
        await self.switch_to(state, manager)
        await self.show(manager)

    async def switch_to(self, state: State, manager: DialogManagerProto):
        if state.group != self.states_group():
            raise ValueError("Cannot switch from %s to another states group %s" % (state.group, self.states_group()))
        await manager.switch_to(state)

    async def _current_window(self, manager: DialogManagerProto) -> DialogWindowProto:
        return self.windows[manager.context.state]

    async def show(self, manager: DialogManagerProto) -> None:
        logger.debug("Dialog show (%s)", self)
        window = await self._current_window(manager)
        message = await window.show(self, manager)
        manager.context.last_message_id = message.message_id

    async def _message_handler(self, m: Message, dialog_manager: DialogManager):
        intent = dialog_manager.current_intent()
        window = await self._current_window(dialog_manager)
        await window.process_message(m, self, dialog_manager)
        if dialog_manager.current_intent() == intent:  # no new dialog started
            await self.show(dialog_manager)

    async def _callback_handler(self, c: CallbackQuery, dialog_manager: DialogManager):
        intent = dialog_manager.current_intent()
        window = await self._current_window(dialog_manager)
        await window.process_callback(c, self, dialog_manager)
        if dialog_manager.current_intent() == intent:  # no new dialog started
            await self.show(dialog_manager)
        await c.answer()

    async def _update_handler(self, event: ChatEvent, dialog_manager: DialogManager):
        await self.show(dialog_manager)

    def register(self, registry: DialogRegistryProto, dp: Dispatcher, **filters) -> None:
        dp.register_callback_query_handler(self._callback_handler, state=self.states, **filters)
        if "content_types" not in filters:
            filters["content_types"] = ContentTypes.ANY
        dp.register_message_handler(self._message_handler, state=self.states, **filters)

    def states_group(self) -> Type[StatesGroup]:
        return self._states_group

    def states_group_name(self) -> str:
        return self._states_group.__full_group_name__

    async def process_result(self, result: Any, manager: DialogManagerProto):
        if self.on_process_result:
            await self.on_process_result(result, manager)

    def find(self, widget_id) -> Optional[Actionable]:
        for w in self.windows.values():
            widget = w.find(widget_id)
            if widget:
                return widget

    def __repr__(self):
        return f"<{self.__class__.__qualname__}({self.states_group()})>"
