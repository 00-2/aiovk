import json
from abc import ABC, abstractmethod
from typing import Union, Optional
import random

from . import API
from .api import LazyAPI
from .exceptions import VkLongPollError

class BaseLongPoll(ABC):
    """Interface for all types of Longpoll API"""
    def __init__(self, session_or_api, mode: Optional[Union[int, list]],
                 wait: int = 25, version: int = 2, timeout: int = None):
        """
        :param session_or_api: session object or data for creating a new session
        :type session_or_api: BaseSession or API or LazyAPI
        :param mode: additional answer options
        :param wait: waiting period
        :param version: protocol version
        :param timeout: timeout for *.getLongPollServer request in current session
        """
        if isinstance(session_or_api, (API, LazyAPI)):
            self.api = session_or_api
        else:
            self.api = API(session_or_api)

        self.timeout = timeout or self.api._session.timeout

        if type(mode) == list:
            mode = sum(mode)

        self.base_params = {
            'version': version,
            'wait': wait,
            'act': 'a_check'
        }

        if mode is not None:
            self.base_params['mode'] = mode

        self.pts = None
        self.ts = None
        self.key = None
        self.base_url = None

    @abstractmethod
    async def _get_long_poll_server(self, need_pts: bool = False) -> None:
        """Send *.getLongPollServer request and update internal data

        :param need_pts: need return the pts field
        """

    async def wait(self, need_pts=False) -> dict:
        """Send long poll request

        :param need_pts: need return the pts field
        """
        if not self.base_url:
            await self._get_long_poll_server(need_pts)

        params = {
            'ts': self.ts,
            'key': self.key,
        }
        params.update(self.base_params)
        # invalid mimetype from server
        status, response, _ = await self.api._session.driver.get_text(
            self.base_url, params,
            timeout=2 * self.base_params['wait']
        )

        if status == 403:
            raise VkLongPollError(403, 'smth weth wrong', self.base_url + '/', params)

        response = json.loads(response)
        failed = response.get('failed')

        if not failed:
            self.ts = response['ts']
            return response

        if failed == 1:
            self.ts = response['ts']
        elif failed == 4:
            raise VkLongPollError(
                4,
                'An invalid version number was passed in the version parameter',
                self.base_url + '/',
                params
            )
        else:
            self.base_url = None

        return await self.wait()
    
    async def iter(self):
        while True:
            response = await self.wait()
            for event in response['updates']:
                yield event

    async def get_pts(self, need_ts=False):
        if not self.base_url or not self.pts:
            await self._get_long_poll_server(need_pts=True)

        if need_ts:
            return self.pts, self.ts
        return self.pts


class UserLongPoll(BaseLongPoll):
    """Implements https://vk.ru/dev/using_longpoll"""
    # False for testing
    use_https = True

    async def _get_long_poll_server(self, need_pts=False):
        response = await self.api('messages.getLongPollServer', need_pts=int(need_pts), timeout=self.timeout)
        self.pts = response.get('pts')
        self.ts = response['ts']
        self.key = response['key']
        # fucking differences between long poll methods in vk api!
        self.base_url = f'http{"s" if self.use_https else ""}://{response["server"]}'


class LongPoll(UserLongPoll):
    """Implements https://vk.ru/dev/using_longpoll

    This class for backward compatibility
    """

    
class BotsLongPoll(BaseLongPoll):
    """Implements https://vk.ru/dev/bots_longpoll"""
    def __init__(self, session_or_api, group_id, wait=25, version=1, timeout=None):
        super().__init__(session_or_api, None, wait, version, timeout)
        self.group_id = group_id

    async def _get_long_poll_server(self, need_pts=False):
        response = await self.api('groups.getLongPollServer', group_id=self.group_id)
        self.pts = response.get('pts')
        self.ts = response['ts']
        self.key = response['key']
        self.base_url = '{}'.format(response['server'])  # Method already returning url with https://

# -*- coding: utf-8 -*-
"""
:authors: python273
:license: Apache License, Version 2.0, see LICENSE file

:copyright: (c) 2019 python273
"""

from collections import defaultdict
from datetime import datetime
from enum import IntEnum

import requests

CHAT_START_ID = int(2E9)  # id с которого начинаются беседы


class VkLongpollMode(IntEnum):
    """ Дополнительные опции ответа

    `Подробнее в документации VK API
    <https://vk.ru/dev/using_longpoll?f=1.+Подключение>`_
    """

    #: Получать вложения
    GET_ATTACHMENTS = 2

    #: Возвращать расширенный набор событий
    GET_EXTENDED = 2**3

    #: возвращать pts для метода `messages.getLongPollHistory`
    GET_PTS = 2**5

    #: В событии с кодом 8 (друг стал онлайн) возвращать
    #: дополнительные данные в поле `extra`
    GET_EXTRA_ONLINE = 2**6

    #: Возвращать поле `random_id`
    GET_RANDOM_ID = 2**7


DEFAULT_MODE = sum(VkLongpollMode)


class VkEventType(IntEnum):
    """ Перечисление событий, получаемых от longpoll-сервера.

    `Подробнее в документации VK API
    <https://vk.ru/dev/using_longpoll?f=3.+Структура+событий>`__
    """

    #: Замена флагов сообщения (FLAGS:=$flags)
    MESSAGE_FLAGS_REPLACE = 1

    #: Установка флагов сообщения (FLAGS|=$mask)
    MESSAGE_FLAGS_SET = 2

    #: Сброс флагов сообщения (FLAGS&=~$mask)
    MESSAGE_FLAGS_RESET = 3

    #: Добавление нового сообщения.
    MESSAGE_NEW = 4

    #: Редактирование сообщения.
    MESSAGE_EDIT = 5

    #: Прочтение всех входящих сообщений в $peer_id,
    #: пришедших до сообщения с $local_id.
    READ_ALL_INCOMING_MESSAGES = 6

    #: Прочтение всех исходящих сообщений в $peer_id,
    #: пришедших до сообщения с $local_id.
    READ_ALL_OUTGOING_MESSAGES = 7

    #: Друг $user_id стал онлайн. $extra не равен 0, если в mode был передан флаг 64.
    #: В младшем байте числа extra лежит идентификатор платформы
    #: (см. :class:`VkPlatform`).
    #: $timestamp — время последнего действия пользователя $user_id на сайте.
    USER_ONLINE = 8

    #: Друг $user_id стал оффлайн ($flags равен 0, если пользователь покинул сайт и 1,
    #: если оффлайн по таймауту). $timestamp — время последнего действия пользователя
    #: $user_id на сайте.
    USER_OFFLINE = 9

    #: Сброс флагов диалога $peer_id.
    #: Соответствует операции (PEER_FLAGS &= ~$flags).
    #: Только для диалогов сообществ.
    PEER_FLAGS_RESET = 10

    #: Замена флагов диалога $peer_id.
    #: Соответствует операции (PEER_FLAGS:= $flags).
    #: Только для диалогов сообществ.
    PEER_FLAGS_REPLACE = 11

    #: Установка флагов диалога $peer_id.
    #: Соответствует операции (PEER_FLAGS|= $flags).
    #: Только для диалогов сообществ.
    PEER_FLAGS_SET = 12

    #: Удаление всех сообщений в диалоге $peer_id с идентификаторами вплоть до $local_id.
    PEER_DELETE_ALL = 13

    #: Восстановление недавно удаленных сообщений в диалоге $peer_id с
    #: идентификаторами вплоть до $local_id.
    PEER_RESTORE_ALL = 14

    #: Один из параметров (состав, тема) беседы $chat_id были изменены.
    #: $self — 1 или 0 (вызваны ли изменения самим пользователем).
    CHAT_EDIT = 51

    #: Изменение информации чата $peer_id с типом $type_id
    #: $info — дополнительная информация об изменениях
    CHAT_UPDATE = 52

    #: Пользователь $user_id набирает текст в диалоге.
    #: Событие приходит раз в ~5 секунд при наборе текста. $flags = 1.
    USER_TYPING = 61

    #: Пользователь $user_id набирает текст в беседе $chat_id.
    USER_TYPING_IN_CHAT = 62

    #: Пользователь $user_id записывает голосовое сообщение в диалоге/беседе $peer_id
    USER_RECORDING_VOICE = 64

    #: Пользователь $user_id совершил звонок с идентификатором $call_id.
    USER_CALL = 70

    #: Счетчик в левом меню стал равен $count.
    MESSAGES_COUNTER_UPDATE = 80

    #: Изменились настройки оповещений.
    #: $peer_id — идентификатор чата/собеседника,
    #: $sound — 1/0, включены/выключены звуковые оповещения,
    #: $disabled_until — выключение оповещений на необходимый срок.
    NOTIFICATION_SETTINGS_UPDATE = 114

class VkBotEventType():
    """ Перечисление событий, получаемых от longpoll-сервера.

    `Подробнее в документации VK API
    <https://vk.ru/dev/using_longpoll?f=3.+Структура+событий>`__
    """

    #: Замена флагов сообщения (FLAGS:=$flags)
    MESSAGE_FLAGS_REPLACE = 1

    #: Установка флагов сообщения (FLAGS|=$mask)
    MESSAGE_FLAGS_SET = 2

    #: Сброс флагов сообщения (FLAGS&=~$mask)
    MESSAGE_FLAGS_RESET = 3

    #: Добавление нового сообщения.
    MESSAGE_NEW = 'message_new'

    #: Редактирование сообщения.
    MESSAGE_EDIT = 5

    #: Прочтение всех входящих сообщений в $peer_id,
    #: пришедших до сообщения с $local_id.
    READ_ALL_INCOMING_MESSAGES = 6

    #: Прочтение всех исходящих сообщений в $peer_id,
    #: пришедших до сообщения с $local_id.
    READ_ALL_OUTGOING_MESSAGES = 7

    #: Друг $user_id стал онлайн. $extra не равен 0, если в mode был передан флаг 64.
    #: В младшем байте числа extra лежит идентификатор платформы
    #: (см. :class:`VkPlatform`).
    #: $timestamp — время последнего действия пользователя $user_id на сайте.
    USER_ONLINE = 8

    #: Друг $user_id стал оффлайн ($flags равен 0, если пользователь покинул сайт и 1,
    #: если оффлайн по таймауту). $timestamp — время последнего действия пользователя
    #: $user_id на сайте.
    USER_OFFLINE = 9

    #: Сброс флагов диалога $peer_id.
    #: Соответствует операции (PEER_FLAGS &= ~$flags).
    #: Только для диалогов сообществ.
    PEER_FLAGS_RESET = 10

    #: Замена флагов диалога $peer_id.
    #: Соответствует операции (PEER_FLAGS:= $flags).
    #: Только для диалогов сообществ.
    PEER_FLAGS_REPLACE = 11

    #: Установка флагов диалога $peer_id.
    #: Соответствует операции (PEER_FLAGS|= $flags).
    #: Только для диалогов сообществ.
    PEER_FLAGS_SET = 12

    #: Удаление всех сообщений в диалоге $peer_id с идентификаторами вплоть до $local_id.
    PEER_DELETE_ALL = 13

    #: Восстановление недавно удаленных сообщений в диалоге $peer_id с
    #: идентификаторами вплоть до $local_id.
    PEER_RESTORE_ALL = 14

    #: Один из параметров (состав, тема) беседы $chat_id были изменены.
    #: $self — 1 или 0 (вызваны ли изменения самим пользователем).
    CHAT_EDIT = 51

    #: Изменение информации чата $peer_id с типом $type_id
    #: $info — дополнительная информация об изменениях
    CHAT_UPDATE = 52

    #: Пользователь $user_id набирает текст в диалоге.
    #: Событие приходит раз в ~5 секунд при наборе текста. $flags = 1.
    USER_TYPING = 61

    #: Пользователь $user_id набирает текст в беседе $chat_id.
    USER_TYPING_IN_CHAT = 62

    #: Пользователь $user_id записывает голосовое сообщение в диалоге/беседе $peer_id
    USER_RECORDING_VOICE = 64

    #: Пользователь $user_id совершил звонок с идентификатором $call_id.
    USER_CALL = 70

    #: Счетчик в левом меню стал равен $count.
    MESSAGES_COUNTER_UPDATE = 80

    #: Изменились настройки оповещений.
    #: $peer_id — идентификатор чата/собеседника,
    #: $sound — 1/0, включены/выключены звуковые оповещения,
    #: $disabled_until — выключение оповещений на необходимый срок.
    NOTIFICATION_SETTINGS_UPDATE = 114


class VkPlatform(IntEnum):
    """ Идентификаторы платформ """

    #: Мобильная версия сайта или неопознанное мобильное приложение
    MOBILE = 1

    #: Официальное приложение для iPhone
    IPHONE = 2

    #: Официальное приложение для iPad
    IPAD = 3

    #: Официальное приложение для Android
    ANDROID = 4

    #: Официальное приложение для Windows Phone
    WPHONE = 5

    #: Официальное приложение для Windows 8
    WINDOWS = 6

    #: Полная версия сайта или неопознанное приложение
    WEB = 7


class VkOfflineType(IntEnum):
    """ Выход из сети в событии :attr:`VkEventType.USER_OFFLINE` """

    #: Пользователь покинул сайт
    EXIT = 0

    #: Оффлайн по таймауту
    AWAY = 1


class VkMessageFlag(IntEnum):
    """ Флаги сообщений """

    #: Сообщение не прочитано.
    UNREAD = 1

    #: Исходящее сообщение.
    OUTBOX = 2

    #: На сообщение был создан ответ.
    REPLIED = 2**2

    #: Помеченное сообщение.
    IMPORTANT = 2**3

    #: Сообщение отправлено через чат.
    CHAT = 2**4

    #: Сообщение отправлено другом.
    #: Не применяется для сообщений из групповых бесед.
    FRIENDS = 2**5

    #: Сообщение помечено как "Спам".
    SPAM = 2**6

    #: Сообщение удалено (в корзине).
    DELETED = 2**7

    #: Сообщение проверено пользователем на спам.
    FIXED = 2**8

    #: Сообщение содержит медиаконтент
    MEDIA = 2**9

    #: Приветственное сообщение от сообщества.
    HIDDEN = 2**16

    #: Сообщение удалено для всех получателей.
    DELETED_ALL = 2**17


class VkPeerFlag(IntEnum):
    """ Флаги диалогов """

    #: Важный диалог
    IMPORTANT = 1

    #: Неотвеченный диалог
    UNANSWERED = 2


class VkChatEventType(IntEnum):
    """ Идентификатор типа изменения в чате """

    #: Изменилось название беседы
    TITLE = 1

    #: Сменилась обложка беседы
    PHOTO = 2

    #: Назначен новый администратор
    ADMIN_ADDED = 3

    #: Изменены настройки беседы
    SETTINGS_CHANGED = 4

    #: Закреплено сообщение
    MESSAGE_PINNED = 5

    #: Пользователь присоединился к беседе
    USER_JOINED = 6

    #: Пользователь покинул беседу
    USER_LEFT = 7

    #: Пользователя исключили из беседы
    USER_KICKED = 8

    #: С пользователя сняты права администратора
    ADMIN_REMOVED = 9

    #: Бот прислал клавиатуру
    KEYBOARD_RECEIVED = 11


MESSAGE_EXTRA_FIELDS = [
    'peer_id', 'timestamp', 'text', 'extra_values', 'attachments', 'random_id'
]
MSGID = 'message_id'

EVENT_ATTRS_MAPPING = {
    VkEventType.MESSAGE_FLAGS_REPLACE: [MSGID, 'flags'] + MESSAGE_EXTRA_FIELDS,
    VkEventType.MESSAGE_FLAGS_SET: [MSGID, 'mask'] + MESSAGE_EXTRA_FIELDS,
    VkEventType.MESSAGE_FLAGS_RESET: [MSGID, 'mask'] + MESSAGE_EXTRA_FIELDS,
    VkEventType.MESSAGE_NEW: [MSGID, 'flags'] + MESSAGE_EXTRA_FIELDS,
    VkEventType.MESSAGE_EDIT: [MSGID, 'mask'] + MESSAGE_EXTRA_FIELDS,

    VkEventType.READ_ALL_INCOMING_MESSAGES: ['peer_id', 'local_id'],
    VkEventType.READ_ALL_OUTGOING_MESSAGES: ['peer_id', 'local_id'],

    VkEventType.USER_ONLINE: ['user_id', 'extra', 'timestamp'],
    VkEventType.USER_OFFLINE: ['user_id', 'flags', 'timestamp'],

    VkEventType.PEER_FLAGS_RESET: ['peer_id', 'mask'],
    VkEventType.PEER_FLAGS_REPLACE: ['peer_id', 'flags'],
    VkEventType.PEER_FLAGS_SET: ['peer_id', 'mask'],

    VkEventType.PEER_DELETE_ALL: ['peer_id', 'local_id'],
    VkEventType.PEER_RESTORE_ALL: ['peer_id', 'local_id'],

    VkEventType.CHAT_EDIT: ['chat_id', 'self'],
    VkEventType.CHAT_UPDATE: ['type_id', 'peer_id', 'info'],

    VkEventType.USER_TYPING: ['user_id', 'flags'],
    VkEventType.USER_TYPING_IN_CHAT: ['user_id', 'chat_id'],
    VkEventType.USER_RECORDING_VOICE: ['peer_id', 'user_id', 'flags', 'timestamp'],

    VkEventType.USER_CALL: ['user_id', 'call_id'],

    VkEventType.MESSAGES_COUNTER_UPDATE: ['count'],
    VkEventType.NOTIFICATION_SETTINGS_UPDATE: ['values']
}

def get_all_event_attrs():
    keys = set()

    for l in EVENT_ATTRS_MAPPING.values():
        keys.update(l)

    return tuple(keys)


ALL_EVENT_ATTRS = get_all_event_attrs()

PARSE_PEER_ID_EVENTS = [
    k for k, v in EVENT_ATTRS_MAPPING.items() if 'peer_id' in v
]
PARSE_MESSAGE_FLAGS_EVENTS = [
    VkEventType.MESSAGE_FLAGS_REPLACE,
    VkEventType.MESSAGE_NEW
]


class MessageEvent(object):
    """ Событие, полученное от longpoll-сервера.

    Имеет поля в соответствии с `документацией
    <https://vk.ru/dev/using_longpoll_2?f=3.%2BСтруктура%2Bсобытий>`_.

    События `MESSAGE_NEW` и `MESSAGE_EDIT` имеют (среди прочих) такие поля:
        - `text` - `экранированный <https://ru.wikipedia.org/wiki/Мнемоники_в_HTML>`_ текст
        - `message` - оригинальный текст сообщения.

    События с полем `timestamp` также дополнительно имеют поле `datetime`.
    """

    def __init__(self, raw):
        self.raw = raw

        self.from_user = False
        self.from_chat = False
        self.from_group = False
        self.from_me = False
        self.to_me = False

        self.attachments = {}
        self.attachments_ids = []
        self.pad_id = None
        self.keyboard = ""
        self.message_data = None

        self.message_id = None
        self.timestamp = None
        self.peer_id = None
        self.flags = None
        self.extra = None
        self.extra_values = None
        self.type_id = None
        self.group_id = None
        self.fwd_messages = []
        self.text = None

        self.state = ''
        
        try:
            self.type = VkEventType(self.raw[0])
            self._list_to_attr(self.raw[1:], EVENT_ATTRS_MAPPING[self.type])
        except ValueError:
            self.type = self.raw[0]

        if self.extra_values:
            self._dict_to_attr(self.extra_values)

        if self.type in PARSE_PEER_ID_EVENTS:
            self._parse_peer_id()

        if self.type in PARSE_MESSAGE_FLAGS_EVENTS:
            self._parse_message_flags()

        if self.type is VkEventType.CHAT_UPDATE:
            self._parse_chat_info()
            try:
                self.update_type = VkChatEventType(self.type_id)
            except ValueError:
                self.update_type = self.type_id

        elif self.type is VkEventType.NOTIFICATION_SETTINGS_UPDATE:
            self._dict_to_attr(self.values)
            self._parse_peer_id()

        elif self.type is VkEventType.PEER_FLAGS_REPLACE:
            self._parse_peer_flags()

        elif self.type in [VkEventType.MESSAGE_NEW, VkEventType.MESSAGE_EDIT]:
            self._parse_message()

        elif self.type in [VkEventType.USER_ONLINE, VkEventType.USER_OFFLINE]:
            self.user_id = abs(self.user_id)
            self._parse_online_status()

        elif self.type is VkEventType.USER_RECORDING_VOICE:
            if isinstance(self.user_id, list):
                self.user_id = self.user_id[0]

        if self.timestamp:
            self.datetime = datetime.utcfromtimestamp(self.timestamp)

    def _list_to_attr(self, raw, attrs):
        for i in range(min(len(raw), len(attrs))):
            self.__setattr__(attrs[i], raw[i])

    def _dict_to_attr(self, values):
        for k, v in values.items():
            self.__setattr__(k, v)

    def _parse_peer_id(self):
        if self.peer_id < 0:  # Сообщение от/для группы
            self.from_group = True
            self.group_id = abs(self.peer_id)

        elif self.peer_id > CHAT_START_ID:  # Сообщение из беседы
            self.from_chat = True
            self.chat_id = self.peer_id - CHAT_START_ID

            if self.extra_values and 'from' in self.extra_values:
                self.user_id = int(self.extra_values['from'])

        else:  # Сообщение от/для пользователя
            self.from_user = True
            self.user_id = self.peer_id

    def _parse_message_flags(self):
        self.message_flags = set(
            x for x in VkMessageFlag if self.flags & x
        )

    def _parse_peer_flags(self):
        self.peer_flags = set(
            x for x in VkPeerFlag if self.flags & x
        )

    def _parse_message(self):
        if self.type is VkEventType.MESSAGE_NEW:
            if self.flags & VkMessageFlag.OUTBOX:
                self.from_me = True
            else:
                self.to_me = True

        # ВК возвращает сообщения в html-escaped виде,
        # при этом переводы строк закодированы как <br> и не экранированы

        self.text = self.text.replace('<br>', '\n')
        self.message = self.text \
            .replace('&lt;', '<') \
            .replace('&gt;', '>') \
            .replace('&quot;', '"') \
            .replace('&amp;', '&')

    def _parse_online_status(self):
        try:
            if self.type is VkEventType.USER_ONLINE:
                self.platform = VkPlatform(self.extra & 0xFF)

            elif self.type is VkEventType.USER_OFFLINE:
                self.offline_type = VkOfflineType(self.flags)

        except ValueError:
            pass

    def _parse_chat_info(self):
        if self.type_id == VkChatEventType.ADMIN_ADDED.value:
            self.info = {'admin_id': self.info}

        elif self.type_id == VkChatEventType.MESSAGE_PINNED.value:
            self.info = {'conversation_message_id': self.info}

        elif self.type_id in [VkChatEventType.USER_JOINED.value,
                              VkChatEventType.USER_LEFT.value,
                              VkChatEventType.USER_KICKED.value,
                              VkChatEventType.ADMIN_REMOVED.value]:
            self.info = {'user_id': self.info}
    
    def to_serializable(self):
        return {
            "raw": self.raw,
            "from_user": self.from_user,
            "from_chat": self.from_chat,
            "from_group": self.from_group,
            "from_me": self.from_me,
            "to_me": self.to_me,
            "attachments": self.attachments,
            "attachments_ids": self.attachments_ids,
            "keyboard": self.keyboard, 
            "message_data": self.message_data,
            "message_id": self.message_id,
            "timestamp": self.timestamp,
            "peer_id": self.peer_id,
            "flags": self.flags,
            "extra": self.extra,
            "extra_values": self.extra_values,
            "type_id": self.type_id,
            "group_id": self.group_id,
            "type_id": self.type,  # Added type_id field, assuming it's stored in self.type
            "fwd_messages": self.fwd_messages,  # Added fwd_messages field
            "state" : self.state,
            "text": self.text
        }

    @classmethod
    def from_serializable(cls, data):
        # Create a new instance of MessageEvent
        event = cls(data.get('raw'))

        # Set the properties of the object using the data from the dictionary
        event.raw = data.get('raw')
        event.from_user = data.get('from_user')
        event.from_chat = data.get('from_chat')
        event.from_group = data.get('from_group')
        event.from_me = data.get('from_me')
        event.to_me = data.get('to_me')
        event.attachments = data.get('attachments', '')
        event.attachments_ids = data.get('attachments_ids', []) 
        event.pad_id = data.get('pad_id')
        event.keyboard = data.get('keyboard', '')
        event.message_data = data.get('message_data')
        event.message_id = data.get('message_id')
        event.timestamp = data.get('timestamp')
        event.peer_id = data.get('peer_id')
        event.flags = data.get('flags')
        event.extra = data.get('extra')
        event.extra_values = data.get('extra_values')
        event.type_id = data.get('type_id')
        event.group_id = data.get('group_id')
        event.text = data.get('text')
        event.fwd_messages = data.get('fwd_messages')
        event.type = VkEventType(event.type_id)
        # Parse the timestamp into a datetime object, if it exists
        if 'datetime' in data and data['datetime']:
            event.datetime = datetime.fromisoformat(data['datetime'])
        event.state = data.get('state')
        
        return event

    def create_reply(self, reply_text, attachments=None, fwd_messages=None, keyboard=None, payload=None, state = "", attachments_ids=None, pad_id=None):
        """
        Create a MessageEvent as a reply to this message.
        The reply will be addressed to the sender of the original message.

        :param reply_text: The text of the reply message.
        :param attachments: Attachments to include in the reply.
        :param fwd_messages: Forwarded messages to include in the reply.
        :param keyboard: A keyboard layout for the reply.
        :param payload: Additional payload data.
        :return: A new MessageEvent object configured as a reply.
        """
        reply_event = MessageEvent(raw=self.raw.copy())

        # Set peer_id as per the original message source
        if self.from_user:
            reply_event.peer_id = self.user_id
        elif self.from_chat:
            reply_event.peer_id = self.chat_id + CHAT_START_ID  # Assuming CHAT_START_ID is a global constant
        elif self.from_group:
            reply_event.peer_id = -self.group_id

        # Set the reply properties
        reply_event.text = reply_text
        reply_event.message = reply_text
        reply_event.attachments = attachments if attachments else ""
        reply_event.fwd_messages = fwd_messages if fwd_messages else []
        reply_event.keyboard = keyboard if keyboard else ""
        reply_event.payload = payload
        reply_event.pad_id = pad_id if pad_id is not None else getattr(self, "pad_id", None)
        reply_event.attachments_ids = attachments_ids if attachments_ids is not None else None     

        # Set appropriate flags (modify as needed)
        reply_event.from_me = True
        reply_event.to_me = False

        # Reset or modify other fields as needed
        reply_event.message_data = None  # Assuming no additional data (modify as needed)

        # Set external fields
        reply_event.group_id = self.group_id
        
        reply_event.raw = [reply_event.type_id, reply_event.message_id, reply_event.flags, reply_event.peer_id, reply_event.timestamp, reply_event.text, reply_event.extra_values]
        
        reply_event.state = state
        
        return reply_event

    def to_command(self):
        """
        Convert a MessageEvent to a VK API command.

        Args:
        event (MessageEvent): The event to convert.

        Returns:
        str: A string representation of the VK API command.
        """
        if self.type == VkEventType.MESSAGE_NEW :
            # Construct the command for sending a message to a user
            user_id = self.peer_id
            message_text = self.text
            random_id = random.randint(0, 2048)

            # If the event includes attachments or other special content, add those here
            attachments = self.attachments if self.attachments else ''
            if self.keyboard:
                command = f"""API.messages.send({{"user_id": "{user_id}","message": "{message_text}","attachment": "{attachments}","keyboard": json_{self.state}_{self.peer_id}, "random_id": {random_id}}})"""
            else:
                command = f"""API.messages.send({{"user_id": "{user_id}","message": "{message_text}","attachment": "{attachments}", "random_id": {random_id}}})"""
        return command
    
    
    def has_attachments(self) -> bool:
        ex = self.extra_values or {}
        if self.attachments:
            return True
        return any(k.startswith("attach1") for k in ex.keys())
    
    async def normalized_attachments(self, api) -> list:
        async def _from_messages_getById():
            try:
                r = await api.messages.getById(message_ids=self.message_id, group_id=self.group_id)
            except Exception:
                try:
                    r = await api.messages.getById(message_ids=self.message_id)
                except Exception:
                    return []
            items = (r or {}).get("items") or []
            return items[0].get("attachments") or [] if items else []

        def _map(atts):
            out = []
            for a in atts or []:
                t = a.get("type")
                if t == "photo":
                    sz = (a.get("photo") or {}).get("sizes") or []
                    sz = [{"type": s.get("type"), "url": s.get("url")} for s in sz if s.get("url")]
                    if sz:
                        out.append({"type": "photo", "photo": {"sizes": sz}})
                elif t == "doc":
                    d = a.get("doc") or {}
                    out.append({"type": "doc", "doc": {
                        "title": d.get("title", ""),
                        "ext": d.get("ext", ""),
                        "url": d.get("url", ""),
                        "date": d.get("date", 0),
                    }})
            return out

        got = _map(await _from_messages_getById())
        if got:
            return got

        ex = self.extra_values or {}
        norm = []
        i = 1
        while f"attach{i}" in ex and f"attach{i}_type" in ex:
            token = str(ex[f"attach{i}"])              # "owner_id_item_id"
            tp = ex[f"attach{i}_type"]                 # "photo" | "doc"
            try:
                oid, iid = token.split("_", 1)
            except Exception:
                i += 1
                continue

            if tp == "photo":
                try:
                    ph = await api.photos.getById(photos=f"{oid}_{iid}", photo_sizes=1)
                    if ph:
                        sz = [{"type": s.get("type"), "url": s.get("url")}
                            for s in ph[0].get("sizes", []) if s.get("url")]
                        if sz:
                            norm.append({"type": "photo", "photo": {"sizes": sz}})
                except Exception:
                    pass
            elif tp == "doc":
                try:
                    d = await api.docs.getById(docs=f"{oid}_{iid}")
                    if d:
                        d0 = d[0]
                        norm.append({"type": "doc", "doc": {
                            "title": d0.get("title", ""),
                            "ext": d0.get("ext", ""),
                            "url": d0.get("url", ""),
                            "date": d0.get("date", 0),
                        }})
                except Exception:
                    pass
            i += 1

        return norm


from datetime import datetime

# предполагаю, что у тебя уже есть:
# from vk_api.longpoll import VkEventType

class BotMessageEvent(MessageEvent):
    """
    Обёртка над Callback API-событием (webhook / server-side),
    ведущая себя как MessageEvent из longpoll.

    Ожидаемый raw:
    {
        "group_id": ...,
        "type": "message_new",
        "event_id": "...",
        "v": "5.131",
        "object": {
            "client_info": {...},
            "message": {
                "date": ...,
                "from_id": ...,
                "id": ...,
                "peer_id": ...,
                "text": "...",
                "attachments": [...],
                "fwd_messages": [...],
                ...
            }
        }
    }
    """

    def __init__(self, raw: dict):
        # --- базовая инициализация полей, как в MessageEvent.__init__ ---
        self.raw = raw

        self.from_user = False
        self.from_chat = False
        self.from_group = False
        self.from_me = False
        self.to_me = False

        self.attachments = {}
        self.attachments_ids = []
        self.pad_id = None
        self.keyboard = ""
        self.message_data = None

        self.message_id = None
        self.timestamp = None
        self.peer_id = None
        self.flags = None
        self.extra = None
        self.extra_values = None
        self.type_id = None
        self.group_id = None
        self.fwd_messages = []
        self.text = None

        self.state = ''

        # --- разбор callback JSON ---
        self.type = raw.get("type")           # 'message_new'
        self.event_id = raw.get("event_id")
        self.v = raw.get("v")

        self.group_id = raw.get("group_id")

        obj = raw.get("object") or {}
        self.message = obj.get("message") or {}

        # основные поля сообщения
        self.message_id = self.message.get("id")
        self.peer_id = self.message.get("peer_id")
        self.conversation_message_id = self.message.get("conversation_message_id")
        self.from_id = self.message.get("from_id")
        self.text = self.message.get("text") or ""
        self.timestamp = self.message.get("date")

        if self.timestamp:
            self.datetime = datetime.utcfromtimestamp(self.timestamp)

        # VK attachments + fwd
        # тут оставляем в "сыром" виде из Callback API
        self.attachments = self.message.get("attachments") or []
        self.fwd_messages = self.message.get("fwd_messages") or []

        # client_info пригодится, если ты используешь его где-то
        self.client_info = obj.get("client_info") or {}

        # --- флаги from_* / to_me максимально близко к MessageEvent ---
        if self.peer_id is not None:
            if self.peer_id >= 2000000000:
                # беседа
                self.from_chat = True
            elif self.peer_id > 0:
                # личка с юзером
                self.from_user = True
            elif self.peer_id < 0:
                # диалог с сообществом
                self.from_group = True

        # сообщение "от бота"
        if self.group_id is not None and getattr(self, "from_id", None) is not None:
            if self.from_id == -int(self.group_id):
                self.from_me = True

        # Callback приходит целиком "боту"
        self.to_me = True

        # --- attachments_ids в духе vk_api ---
        self.attachments_ids = []
        for a in self.attachments:
            if not isinstance(a, dict):
                continue

            atype = a.get("type")
            payload = a.get(atype) if atype and atype in a else None
            if not isinstance(payload, dict):
                continue

            owner_id = payload.get("owner_id")
            media_id = payload.get("id")
            if atype and owner_id is not None and media_id is not None:
                # например: photo12345_67890
                self.attachments_ids.append(f"{atype}{owner_id}_{media_id}")

    async def normalized_attachments(self, api) -> list:
        async def _from_messages_getById():
            try:
                r = await api.messages.getById(cmids=self.conversation_message_id, peer_id=self.peer_id)
            except Exception as e:
                print('Except ', e)
                try:
                    r = await api.messages.getById(message_ids=self.message_id)
                except Exception:
                    return []
            items = (r or {}).get("items") or []
            return items[0].get("attachments") or [] if items else []

        def _map(atts):
            out = []
            for a in atts or []:
                t = a.get("type")
                if t == "photo":
                    sz = (a.get("photo") or {}).get("sizes") or []
                    sz = [{"type": s.get("type"), "url": s.get("url")} for s in sz if s.get("url")]
                    if sz:
                        out.append({"type": "photo", "photo": {"sizes": sz}})
                elif t == "doc":
                    d = a.get("doc") or {}
                    out.append({"type": "doc", "doc": {
                        "title": d.get("title", ""),
                        "ext": d.get("ext", ""),
                        "url": d.get("url", ""),
                        "date": d.get("date", 0),
                    }})
            return out

        got = _map(await _from_messages_getById())
        if got:
            return got

        ex = self.extra_values or {}
        norm = []
        i = 1
        while f"attach{i}" in ex and f"attach{i}_type" in ex:
            token = str(ex[f"attach{i}"])              # "owner_id_item_id"
            tp = ex[f"attach{i}_type"]                 # "photo" | "doc"
            try:
                oid, iid = token.split("_", 1)
            except Exception:
                i += 1
                continue

            if tp == "photo":
                try:
                    ph = await api.photos.getById(photos=f"{oid}_{iid}", photo_sizes=1)
                    if ph:
                        sz = [{"type": s.get("type"), "url": s.get("url")}
                            for s in ph[0].get("sizes", []) if s.get("url")]
                        if sz:
                            norm.append({"type": "photo", "photo": {"sizes": sz}})
                except Exception:
                    pass
            elif tp == "doc":
                try:
                    d = await api.docs.getById(docs=f"{oid}_{iid}")
                    if d:
                        d0 = d[0]
                        norm.append({"type": "doc", "doc": {
                            "title": d0.get("title", ""),
                            "ext": d0.get("ext", ""),
                            "url": d0.get("url", ""),
                            "date": d0.get("date", 0),
                        }})
                except Exception:
                    pass
            i += 1

        return norm


class EventEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, MessageEvent):
            return obj.to_serializable()
        return json.JSONEncoder.default(self, obj)
