"""A tiny fake Telegram for running the aiogram handlers offline (used by tests/test_handlers.py).

FakeSession answers every Bot API call and keeps the bot's messages, so a test can type text,
press inline buttons and read what the bot shows. Like real Telegram, editing a message with the
same content raises "message is not modified", and old messages can arrive as InaccessibleMessage.
"""
from __future__ import annotations

import datetime as dt
import itertools
from typing import Any, Dict, List

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import (
    AnswerCallbackQuery, EditMessageReplyMarkup, EditMessageText, GetMe, SendDocument, SendMessage,
)
from aiogram.types import CallbackQuery, Chat, InaccessibleMessage, Message, Update, User

UID = 111
GUEST = 222
BOT_USER = User(id=999, is_bot=True, first_name="bot", username="testbot")


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls: List[Any] = []
        self.msgs: Dict[int, dict] = {}
        self.ids = itertools.count(1000)
        self.alerts: List[str] = []
        self.docs: List[str] = []

    async def close(self):
        pass

    async def stream_content(self, *a, **k):  # pragma: no cover
        raise NotImplementedError

    def _msg(self, chat_id: int, mid: int, text: str, markup) -> Message:
        inline = markup if markup is not None and hasattr(markup, "inline_keyboard") else None
        return Message(message_id=mid, date=dt.datetime.now(), chat=Chat(id=chat_id, type="private"),
                       from_user=BOT_USER, text=text, reply_markup=inline)

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, GetMe):
            return BOT_USER
        if isinstance(method, AnswerCallbackQuery):
            if method.text:
                self.alerts.append(method.text)
            return True
        if isinstance(method, SendMessage):
            mid = next(self.ids)
            self.msgs[mid] = {"chat": method.chat_id, "text": method.text, "markup": method.reply_markup}
            return self._msg(method.chat_id, mid, method.text, method.reply_markup)
        if isinstance(method, SendDocument):
            mid = next(self.ids)
            self.docs.append(method.document.filename)
            self.msgs[mid] = {"chat": method.chat_id, "text": "<document>", "markup": None}
            return Message(message_id=mid, date=dt.datetime.now(), chat=Chat(id=method.chat_id, type="private"),
                           from_user=BOT_USER)
        if isinstance(method, (EditMessageText, EditMessageReplyMarkup)):
            m = self.msgs.get(method.message_id)
            if m is None:
                raise TelegramBadRequest(method, "Bad Request: message to edit not found")
            new_text = method.text if isinstance(method, EditMessageText) else m["text"]
            if m["text"] == new_text and _same(m["markup"], method.reply_markup):
                raise TelegramBadRequest(method, "Bad Request: message is not modified")
            m["text"], m["markup"] = new_text, method.reply_markup
            return self._msg(m["chat"], method.message_id, new_text, method.reply_markup)
        return True  # set_my_commands, delete_message, …


def _same(a, b) -> bool:
    dump = lambda x: x.model_dump() if x is not None else None  # noqa: E731
    return dump(a) == dump(b)


class Tg:
    """Drive the bot like a user would."""

    def __init__(self, dp: Dispatcher, bot: Bot, session: FakeSession, uid: int = UID):
        self.dp, self.bot, self.s, self.uid = dp, bot, session, uid
        self.upd = itertools.count(1)
        self.user_mid = itertools.count(1)

    @property
    def user(self) -> User:
        return User(id=self.uid, is_bot=False, first_name="Ali")

    async def send(self, text: str):
        n = len(self.s.calls)
        msg = Message(message_id=next(self.user_mid), date=dt.datetime.now(),
                      chat=Chat(id=self.uid, type="private"), from_user=self.user, text=text)
        await self.dp.feed_update(self.bot, Update(update_id=next(self.upd), message=msg))
        return self.s.calls[n:]

    async def press(self, mid: int, data: str, old: bool = False):
        """Press a button. old=True: Telegram sends the message as InaccessibleMessage (>48 h)."""
        m = self.s.msgs[mid]
        chat = Chat(id=self.uid, type="private")
        if old:
            msg = InaccessibleMessage(chat=chat, message_id=mid, date=0)
        else:
            msg = Message(message_id=mid, date=dt.datetime.now(), chat=chat, from_user=BOT_USER, text=m["text"],
                          reply_markup=m["markup"] if hasattr(m["markup"], "inline_keyboard") else None)
        n = len(self.s.calls)
        cq = CallbackQuery(id=str(next(self.upd)), from_user=self.user, chat_instance="x", message=msg, data=data)
        await self.dp.feed_update(self.bot, Update(update_id=next(self.upd), callback_query=cq))
        calls = self.s.calls[n:]
        if not any(isinstance(c, AnswerCallbackQuery) for c in calls):
            raise AssertionError(f"button {data!r} was never answered (spinner would stay)")
        if any("خطایی" in a for a in self.s.alerts):
            raise AssertionError(f"button {data!r} raised an error, see the log")
        return calls

    def last(self) -> int:
        return max(self.s.msgs)

    def text(self, mid: int) -> str:
        return self.s.msgs[mid]["text"]

    def btns(self, mid: int) -> List[tuple]:
        markup = self.s.msgs[mid]["markup"]
        out = []
        if markup is not None and hasattr(markup, "inline_keyboard"):
            for row in markup.inline_keyboard:
                for b in row:
                    out.append((b.text, b.callback_data or ("webapp:" + b.web_app.url if b.web_app else "?")))
        return out

    def data(self, mid: int, label_part: str) -> str:
        for t, d in self.btns(mid):
            if label_part in t:
                return d
        raise AssertionError(f"no button {label_part!r} in {self.btns(mid)}")


def make_bot(session: FakeSession) -> Bot:
    return Bot("123456:TESTTOKEN", session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
