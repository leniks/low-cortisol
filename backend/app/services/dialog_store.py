from dataclasses import dataclass, field
from uuid import uuid4

from app.schemas.invoke import DialogMessage


@dataclass
class DialogStore:
    _dialogs: dict[str, list[DialogMessage]] = field(default_factory=dict)

    def ensure_conversation_id(self, conversation_id: str | None) -> str:
        if conversation_id:
            return conversation_id
        return str(uuid4())

    def get(self, conversation_id: str) -> list[DialogMessage]:
        return list(self._dialogs.get(conversation_id, []))

    def set(self, conversation_id: str, dialog: list[DialogMessage]) -> None:
        self._dialogs[conversation_id] = list(dialog)

    def append_pair(self, conversation_id: str, user_text: str, assistant_text: str) -> list[DialogMessage]:
        dialog = self.get(conversation_id)
        dialog.append(DialogMessage(role="user", content=user_text))
        dialog.append(DialogMessage(role="assistant", content=assistant_text))
        self.set(conversation_id, dialog)
        return dialog

    def replace_last_pair(self, conversation_id: str, user_text: str, assistant_text: str) -> list[DialogMessage]:
        dialog = self.get(conversation_id)

        # Expecting ... user, assistant at the end. If not, fall back to append.
        if len(dialog) >= 2 and dialog[-2].role == "user" and dialog[-1].role == "assistant":
            dialog[-2] = DialogMessage(role="user", content=user_text)
            dialog[-1] = DialogMessage(role="assistant", content=assistant_text)
            self.set(conversation_id, dialog)
            return dialog

        return self.append_pair(conversation_id, user_text, assistant_text)
