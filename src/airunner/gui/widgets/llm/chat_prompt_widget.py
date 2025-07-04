import json
import time

from typing import Dict, Optional

from PySide6.QtCore import Slot, QPropertyAnimation, QTimer, Qt
from PySide6.QtWidgets import QSpacerItem, QSizePolicy, QApplication

from airunner.enums import (
    SignalCode,
    LLMActionType,
    ModelType,
    ModelStatus,
)
from airunner.gui.widgets.base_widget import BaseWidget
from airunner.gui.widgets.llm.templates.chat_prompt_ui import Ui_chat_prompt
from airunner.gui.widgets.llm.message_widget import MessageWidget
from airunner.data.models import Conversation
from airunner.utils.llm.strip_names_from_message import (
    strip_names_from_message,
)
from airunner.utils.application import create_worker
from airunner.utils.widgets import load_splitter_settings
from airunner.handlers.llm.llm_request import LLMRequest
from airunner.workers.llm_chat_prompt_worker import LLMChatPromptWorker
from airunner.workers.llm_response_worker import LLMResponseWorker
from airunner.settings import AIRUNNER_ART_ENABLED


class ChatPromptWidget(BaseWidget):
    widget_class_ = Ui_chat_prompt
    icons = [
        ("chevron-up", "send_button"),
        ("plus", "clear_conversation_button"),
        ("x", "pushButton"),
    ]

    def __init__(self, *args, **kwargs):
        self.signal_handlers = {
            SignalCode.AUDIO_PROCESSOR_RESPONSE_SIGNAL: self.on_hear_signal,
            SignalCode.MODEL_STATUS_CHANGED_SIGNAL: self.on_model_status_changed_signal,
            SignalCode.CHATBOT_CHANGED: self.on_chatbot_changed,
            SignalCode.CONVERSATION_DELETED: self.on_delete_conversation,
            SignalCode.LLM_CLEAR_HISTORY_SIGNAL: self.on_clear_conversation,
            SignalCode.QUEUE_LOAD_CONVERSATION: self.on_queue_load_conversation,
            SignalCode.LOAD_CONVERSATION: self.on_load_conversation,
            SignalCode.LLM_TOKEN_SIGNAL: self.on_token_signal,
            SignalCode.LLM_TEXT_STREAMED_SIGNAL: self.on_add_bot_message_to_conversation,
        }
        self._splitters = ["chat_prompt_splitter"]
        self._default_splitter_settings_applied = False
        super().__init__()
        self.llm_chat_prompt_worker = create_worker(LLMChatPromptWorker)
        self.token_buffer = []
        self.ui_update_timer = QTimer(self)
        self.ui_update_timer.setInterval(50)
        self.ui_update_timer.timeout.connect(self.flush_token_buffer)
        self.ui_update_timer.start()
        self.registered: bool = False
        self.scroll_bar = None
        self.is_modal = True
        self.generating = False
        self.prefix = ""
        self.prompt = ""
        self.suffix = ""
        self.spacer = None
        self.promptKeyPressEvent = None
        self.originalKeyPressEvent = None
        self.action_menu_displayed = None
        self.action_menu_displayed = None
        self.messages_spacer = None
        self.chat_loaded = False
        self._conversation: Optional[Conversation] = None
        self._conversation_id: Optional[int] = None
        self.conversation_history = []

        # Initialize action menu cleanly
        self.ui.action.blockSignals(True)
        self.ui.action.clear()
        action_map = [
            ("Auto", LLMActionType.APPLICATION_COMMAND),
            ("Chat", LLMActionType.CHAT),
            ("RAG", LLMActionType.PERFORM_RAG_SEARCH),
        ]
        if AIRUNNER_ART_ENABLED:
            action_map.append(("Image", LLMActionType.GENERATE_IMAGE))
        for label, _ in action_map:
            self.ui.action.addItem(label)
        # Set current index based on self.action
        current_action = self.action
        for idx, (_, action_type) in enumerate(action_map):
            if current_action is action_type:
                self.ui.action.setCurrentIndex(idx)
                break
        self.ui.action.blockSignals(False)
        self.originalKeyPressEvent = None
        self.originalKeyPressEvent = self.ui.prompt.keyPressEvent
        self.held_message = None
        self._disabled = False
        self.scroll_animation = None
        self._llm_response_worker = create_worker(
            LLMResponseWorker, sleep_time_in_ms=1
        )
        self.loading = True

    def _apply_default_splitter_settings(self):
        """
        Applies default splitter sizes. Called via QTimer.singleShot to ensure
        widget geometry is more likely to be initialized.
        """
        if hasattr(self, "ui") and self.ui is not None:
            QApplication.processEvents()  # Ensure pending layout events are processed
            # For a vertical splitter where the bottom panel (prompt input) should be minimized,
            # we maximize the top panel (index 0).
            default_chat_splitter_config = {
                "chat_prompt_splitter": {
                    "index_to_maximize": 0,
                    "min_other_size": 50,
                }  # Assuming 50px is a good min for prompt
            }
            load_splitter_settings(
                self.ui,
                self._splitters,
                orientations={
                    "chat_prompt_splitter": Qt.Orientation.Vertical
                },  # Explicitly set orientation
                default_maximize_config=default_chat_splitter_config,
            )
        else:
            self.logger.warning(
                "ChatPromptWidget: UI not available when attempting to apply default splitter settings."
            )

    @property
    def conversation(self) -> Optional[Conversation]:
        return self._conversation

    @conversation.setter
    def conversation(self, val: Optional[Conversation]):
        self._conversation = val
        self._conversation_id = val.id if val else None

    @property
    def conversation_id(self) -> Optional[int]:
        return self._conversation_id

    @conversation_id.setter
    def conversation_id(self, val: Optional[int]):
        self._conversation_id = val
        if val:
            self._conversation = Conversation.objects.filter_by_first(id=val)
        else:
            self._conversation = None

    def on_load_conversation(self, data):
        conversation_id = data.get("conversation_id")
        messages = data.get("messages", [])
        if conversation_id is not None:
            self.conversation_id = conversation_id
            self.api.llm.clear_history(conversation_id=conversation_id)
            self._clear_conversation(skip_update=True)
            self._set_conversation_widgets(messages, skip_scroll=True)
        else:
            self._clear_conversation(skip_update=True)
            self.conversation = None
        QTimer.singleShot(100, self.scroll_to_bottom)

    def on_delete_conversation(self, data):
        if (
            self.conversation_id == data["conversation_id"]
            or self.conversation_id is None
        ):
            self._clear_conversation_widgets()
        self.conversation = None

    def on_queue_load_conversation(self, data):
        self.llm_chat_prompt_worker.add_to_queue(data)

    def load_conversation(self, index: Optional[int] = None):
        self.on_queue_load_conversation(
            {
                "action": "load_conversation",
                "index": index,
            }
        )

    @Slot(str)
    def handle_token_signal(self, val: str):
        if val != "[END]":
            text = self.ui.conversation.toPlainText()
            text += val
            self.ui.conversation.setPlainText(text)
        else:
            self.stop_progress_bar()
            self.generating = False
            self.enable_send_button()

    def on_model_status_changed_signal(self, data):
        if data["model"] == ModelType.LLM:
            self.chat_loaded = data["status"] is ModelStatus.LOADED

        if not self.chat_loaded:
            self.disable_send_button()
        else:
            self.enable_send_button()

    def on_chatbot_changed(self):
        self.api.llm.clear_history()
        self._clear_conversation()

    def _set_conversation_widgets(self, messages, skip_scroll: bool = False):
        start = time.perf_counter()
        for message in messages:
            self.add_message_to_conversation(
                name=message["name"],
                message=message["content"],
                is_bot=message["is_bot"],
                first_message=True,
                _profile_widget=True,
            )
        if not skip_scroll:
            QTimer.singleShot(100, self.scroll_to_bottom)
        end = time.perf_counter()

    def on_hear_signal(self, data: Dict):
        transcription = data["transcription"]
        self.prompt = transcription
        self.do_generate()

    def on_add_bot_message_to_conversation(self, data: Dict):
        llm_response = data.get("response", None)
        if not llm_response:
            raise ValueError("No LLMResponse object found in data")

        if llm_response.node_id is not None:
            return

        self.token_buffer.append(llm_response.message)

        if llm_response.is_first_message:
            self.stop_progress_bar()

        if llm_response.is_end_of_message:
            self.enable_generate()

    def flush_token_buffer(self):
        """
        Flush the token buffer and update the UI.
        """
        combined_message = "".join(self.token_buffer)
        self.token_buffer.clear()

        if combined_message != "":
            # Prevent duplicate bot messages: only add if not already last bot message
            if (
                len(self.conversation_history) == 0
                or not self.conversation_history[-1]["is_bot"]
                or self.conversation_history[-1]["content"] != combined_message
            ):
                self.add_message_to_conversation(
                    name=self.chatbot.botname,
                    message=combined_message,
                    is_bot=True,
                    first_message=False,
                )

    def enable_generate(self):
        self.generating = False
        if self.held_message is not None:
            self.do_generate(prompt_override=self.held_message)
            self.held_message = None
        self.enable_send_button()

    @Slot()
    def action_button_clicked_clear_conversation(self):
        self.api.llm.clear_history()

    def on_clear_conversation(self):
        self._clear_conversation()

    def _clear_conversation(self, skip_update: bool = False):
        start = time.perf_counter()
        self.conversation_history = []
        self._clear_conversation_widgets(skip_update=skip_update)
        end = time.perf_counter()

    def _clear_conversation_widgets(self, skip_update: bool = False):
        start = time.perf_counter()
        layout = self.ui.scrollAreaWidgetContents.layout()
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
            else:
                del item
        if not skip_update:
            layout.update()
        end = time.perf_counter()

    @Slot(bool)
    def action_button_clicked_send(self):
        self.do_generate()

    def interrupt_button_clicked(self):
        self.api.llm.interrupt()
        self.stop_progress_bar()
        self.generating = False
        self.enable_send_button()

    @property
    def action(self) -> LLMActionType:
        return LLMActionType[self.llm_generator_settings.action]

    def do_generate(self, prompt_override=None):
        prompt = (
            self.prompt
            if (prompt_override is None or prompt_override == "")
            else prompt_override
        )
        if prompt is None or prompt == "":
            self.logger.warning("Prompt is empty")
            return

        # Unload art model if art model is loaded and LLM is not loaded
        model_load_balancer = getattr(self.api, "model_load_balancer", None)
        art_model_loaded = (
            model_load_balancer
            and ModelType.SD in model_load_balancer.get_loaded_models()
        )
        llm_loaded = (
            model_load_balancer
            and ModelType.LLM in model_load_balancer.get_loaded_models()
        )
        if art_model_loaded and not llm_loaded:
            model_load_balancer.switch_to_non_art_mode()

        if self.generating:
            if self.held_message is None:
                self.held_message = prompt
                self.disable_send_button()
                self.interrupt_button_clicked()
            return
        self.generating = True

        widget = self.add_message_to_conversation(
            name=self.user.username, message=self.prompt, is_bot=False
        )
        if widget is not None:
            QTimer.singleShot(100, widget.set_content_size)
            QTimer.singleShot(100, self.scroll_to_bottom)

        self.clear_prompt()
        self.start_progress_bar()
        self.api.llm.send_request(
            prompt=prompt,
            llm_request=LLMRequest.from_default(),
            action=self.action,
            do_tts_reply=False,
        )

    def on_token_signal(self, val):
        self.handle_token_signal(val)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._default_splitter_settings_applied and self.isVisible():
            self._apply_default_splitter_settings()
            self._default_splitter_settings_applied = True

        if not self.registered:
            self.registered = True
            self.load_conversation()

        # handle return pressed on QPlainTextEdit
        # there is no returnPressed signal for QPlainTextEdit
        # so we have to use the keyPressEvent
        self.promptKeyPressEvent = self.ui.prompt.keyPressEvent

        # Override the method
        self.ui.prompt.keyPressEvent = self.handle_key_press

        if not self.chat_loaded:
            self.disable_send_button()

        try:
            self.ui.conversation.hide()
        except RuntimeError as e:
            self.logger.warning(f"Error hiding conversation: {e}")

        try:
            self.ui.chat_container.show()
        except RuntimeError as e:
            if AIRUNNER_ART_ENABLED:
                self.logger.warning(f"Error setting SD status text: {e}")
        self.loading = False

    def llm_action_changed(self, val: str):
        if val == "Chat":
            llm_action_value = LLMActionType.CHAT
        elif val == "Image":
            llm_action_value = LLMActionType.GENERATE_IMAGE
        elif val == "RAG":
            llm_action_value = LLMActionType.PERFORM_RAG_SEARCH
        elif val == "Auto":
            llm_action_value = LLMActionType.APPLICATION_COMMAND
        else:
            llm_action_value = LLMActionType.APPLICATION_COMMAND
        self.update_llm_generator_settings("action", llm_action_value.name)

    def prompt_text_changed(self):
        self.prompt = self.ui.prompt.toPlainText()

    def clear_prompt(self):
        self.ui.prompt.setPlainText("")

    def start_progress_bar(self):
        self.ui.progressBar.setRange(0, 0)
        self.ui.progressBar.setValue(0)

    def stop_progress_bar(self):
        self.ui.progressBar.setRange(0, 1)
        self.ui.progressBar.setValue(1)
        self.ui.progressBar.reset()

    def disable_send_button(self):
        # self.ui.send_button.setEnabled(False)
        # self._disabled = True
        pass

    def enable_send_button(self):
        self.ui.send_button.setEnabled(True)
        self._disabled = False

    def response_text_changed(self):
        pass

    def handle_key_press(self, event):
        if event.key() == Qt.Key.Key_Return:
            if (
                not self._disabled
                and event.modifiers() != Qt.KeyboardModifier.ShiftModifier
            ):
                self.do_generate()
                return
        # Call the original method
        if (
            self.originalKeyPressEvent is not None
            and self.originalKeyPressEvent != self.handle_key_press
        ):
            self.originalKeyPressEvent(event)

    def hide_action_menu(self):
        self.action_menu_displayed = False
        self.ui.action_menu.hide()

    def display_action_menu(self):
        self.action_menu_displayed = True
        self.ui.action_menu.show()

    def insert_newline(self):
        self.ui.prompt.insertPlainText("\n")

    def add_message_to_conversation(
        self,
        name: str,
        message: str,
        is_bot: bool,
        first_message: bool = True,
        _message_id: Optional[int] = None,
        _profile_widget: bool = False,
        mood: str = None,
        mood_emoji: str = None,
        user_mood: str = None,
    ):
        start = time.perf_counter() if _profile_widget else None
        message = strip_names_from_message(
            message.lstrip() if first_message else message,
            self.user.username,
            self.chatbot.botname,
        )
        if not first_message:
            for i in range(
                self.ui.scrollAreaWidgetContents.layout().count() - 1, -1, -1
            ):
                item = self.ui.scrollAreaWidgetContents.layout().itemAt(i)
                if item:
                    current_widget = item.widget()
                    if isinstance(current_widget, MessageWidget):
                        if current_widget.is_bot:
                            if message != "":
                                current_widget.update_message(message)
                                QTimer.singleShot(0, self.scroll_to_bottom)
                            return
                        break

        self.remove_spacer()
        widget = None
        if message != "":
            total_widgets = (
                self.ui.scrollAreaWidgetContents.layout().count() - 1
            )
            if total_widgets < 0:
                total_widgets = 0
            widget_start = time.perf_counter() if _profile_widget else None
            # Pass mood and emoji for bot messages
            mood = None
            mood_emoji = None
            if (
                is_bot
                and hasattr(self, "chatbot")
                and self.chatbot is not None
            ):
                mood = getattr(self.chatbot, "bot_mood", None)
                mood_emoji = getattr(self.chatbot, "bot_mood_emoji", None)
            widget = MessageWidget(
                name=name,
                message=message,
                is_bot=is_bot,
                message_id=total_widgets,
                conversation_id=self.conversation_id,
                mood=mood if is_bot else None,
                mood_emoji=mood_emoji if is_bot else None,
            )
            widget_end = time.perf_counter() if _profile_widget else None

            widget.messageResized.connect(self.scroll_to_bottom)

            self.ui.scrollAreaWidgetContents.layout().addWidget(widget)
            QTimer.singleShot(0, self.scroll_to_bottom)

        self.add_spacer()
        return widget

    def remove_spacer(self):
        if self.spacer is not None:
            self.ui.scrollAreaWidgetContents.layout().removeItem(self.spacer)

    def add_spacer(self):
        if self.spacer is None:
            self.spacer = QSpacerItem(
                20,
                0,
                QSizePolicy.Policy.Minimum,
                QSizePolicy.Policy.Expanding,
            )
        self.ui.scrollAreaWidgetContents.layout().addItem(self.spacer)

    def message_type_text_changed(self, val):
        self.update_llm_generator_settings("message_type", val)

    def action_button_clicked_generate_characters(self):
        pass

    def scroll_to_bottom(self):
        if self.loading:
            return
        if self.scroll_bar is None:
            self.scroll_bar = self.ui.chat_container.verticalScrollBar()

        if self.scroll_animation is None:
            self.scroll_animation = QPropertyAnimation(
                self.scroll_bar, b"value"
            )
            self.scroll_animation.setDuration(500)
            self.scroll_animation.finished.connect(
                self._force_scroll_to_bottom
            )

        # Stop any ongoing animation
        if (
            self.scroll_animation
            and self.scroll_animation.state()
            == QPropertyAnimation.State.Running
        ):
            self.scroll_animation.stop()

        self.scroll_animation.setStartValue(self.scroll_bar.value())
        self.scroll_animation.setEndValue(self.scroll_bar.maximum())
        self.scroll_animation.start()

    def _force_scroll_to_bottom(self):
        if self.scroll_bar is not None:
            self.scroll_bar.setValue(self.scroll_bar.maximum())

    def resizeEvent(self, event):
        """
        Resize event handler to adjust the width of the message widgets only, avoiding horizontal scrollbars.
        """
        super().resizeEvent(event)
        # Only set maximum width for message widgets, based on the scrollAreaWidgetContents actual width
        if hasattr(self.ui, "scrollAreaWidgetContents"):
            layout = self.ui.scrollAreaWidgetContents.layout()
            content_width = self.ui.scrollAreaWidgetContents.width()
            margin = 12  # Leave room for scrollbar and padding
            max_msg_width = max(0, content_width - margin)
            if layout is not None:
                for i in range(layout.count()):
                    item = layout.itemAt(i)
                    widget = item.widget()
                    if widget is not None and hasattr(
                        widget, "setMaximumWidth"
                    ):
                        widget.setMaximumWidth(max_msg_width)
