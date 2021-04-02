import os
import re
import uuid
import time
from copy import deepcopy

from aiogram import types
from aiogram.utils import deep_linking

from utils.logger import system_log
from services.qr_parser import QRParser
from services.img_parser import ImageParser
from bot_config import INPUT_FOLDER
from db.db_connectors import ReceiptsDBConnector
from db.fields import *
from .keyboard import ReplyMarkups


class UserState:
    START = "0"
    ITEMS_VALIDATION = "1"
    ITEMS_CORRECTION = "2"
    CHOOSE_SHARED_ITEMS = "3"
    ENTER_VOTERS_COUNT = "4"
    CLOSED = "5"


class ReceiptBot:
    DEEP_LINK_TRIGGER = "receipt"
    RAW_ITEM_PATTERN = "{position}. {name}:\n количество={quantity}, сумма={price}\n"
    RAW_ITEM_REGEXP = "([а-яА-ЯёЁa-zA-Z].+)\s количество=(\d{1,2}), сумма=(\d{1,5}.\d{1,2})"

    def __init__(self, dispatcher):
        self._bot = dispatcher.bot
        self._db = ReceiptsDBConnector()
        self.qr_parser = QRParser()
        self.img_parser = ImageParser()
        self.state = UserState()
        self.markup = ReplyMarkups()
        system_log("Init {bot}".format(bot=type(self).__name__))

    @staticmethod
    def check_qr_code(text):
        return text and "fp" in text and "fn" in text

    def check_deeplink(self, text):
        return text and self.DEEP_LINK_TRIGGER in text.lower()

    def state_handler(self, message, state_id):
        chat_id = message.chat.id
        current_state_id = self._db.get_dialog_state(chat_id)
        return current_state_id == state_id

    @staticmethod
    def composite_key(*args):
        return ".".join(args)

    @staticmethod
    async def start_message(message: types.Message):
        await message.answer("Отправьте, пожалуйста, расшифрованный QR-код в виде строки")

    def _form_polls_from_receipt(self, poll, items):
        def _form_poll_option(raw_option, fallback=False):
            price = item["price"] if not fallback else 0
            item_text = text if not fallback else "Ничего из перечисленного"
            raw_option[TEXT], raw_option[PRICE] = item_text, price
            option = types.PollOption(text=item_text, price=price)
            poll_options.append(raw_option)
            current_poll.options.append(option)

        polls, raw_options = [], []
        poll.options, poll_options = [], []
        current_poll = deepcopy(poll)
        options_count, max_options = 0, 9
        for item in items:
            item["price"] = item["price"] / item["quantity"]
            text = "{name}: {price} руб".format(name=item["name"], price=item["price"])
            for _ in range(item["quantity"]):
                _form_poll_option(raw_option=self._db.option)
                options_count += 1
                if options_count >= max_options:
                    _form_poll_option(raw_option=self._db.option, fallback=True)
                    polls.append(current_poll)
                    raw_options.append(poll_options)
                    current_poll = deepcopy(poll)
                    poll_options, options_count = [], 0

        _form_poll_option(raw_option=self._db.option, fallback=True)
        polls.append(current_poll)
        raw_options.append(poll_options)
        return polls, raw_options

    def init_receipt_document(self, chat_id, raw_items):
        receipt_document = self._db.receipt_document
        receipt_document[CHAT_ID] = chat_id
        receipt_document[RECEIPT_ID] = uuid.uuid4().hex
        receipt_document[RAW_ITEMS] = raw_items
        receipt_document[DIALOG_STATE_ID] = self.state.ITEMS_VALIDATION
        return receipt_document

    def _get_poll_document(self, poll_info: types.Message, raw_poll_options):
        poll_document = self._db.poll
        poll_document[POLL_ID] = poll_info.poll.id
        poll_document[MESSAGE_ID] = poll_info.message_id
        poll_document[OPTIONS] = raw_poll_options
        return poll_document

    async def _send_poll(self, chat_id, poll):
        return await self._bot.send_poll(
            chat_id=chat_id,
            question=poll.question,
            options=[option.text for option in poll.options],
            is_anonymous=False,
            allows_multiple_answers=True
        )

    async def form_and_send_poll(self, message, receipt):
        poll = types.Poll()
        poll.question = "Выберите нужные позиции в чеке"
        polls, raw_options = self._form_polls_from_receipt(poll, items=receipt[CLEAN_ITEMS])

        polls_document = []
        for i, poll in enumerate(polls):
            system_log("User: {user}, Sending poll to user".format(user=message.chat.id))
            poll_info = await self._send_poll(chat_id=message.chat.id, poll=poll)
            poll_document = self._get_poll_document(poll_info, raw_poll_options=raw_options[i])
            polls_document.append(poll_document)

        system_log("User: {user}, Update receipt polls".format(user=message.chat.id))
        self._db.update_receipt_by_id(
            receipt_id=receipt[RECEIPT_ID],
            update={
                DIALOG_STATE_ID: self.state.ENTER_VOTERS_COUNT,
                ACCESS_TIMESTAMP: time.time(),
                SHARED_ITEMS: receipt[SHARED_ITEMS],
                POLLS: polls_document
            }
        )
        await message.answer(text="На скольких человек делим чек?")

    async def parse_receipt_image_and_send_poll(self, message: types.Message):
        image = message.photo[-1]
        image_name = image.file_unique_id + ".jpg"
        path_to_image = os.path.join(INPUT_FOLDER, image_name)
        system_log("User: {user}, Trying to fetch receipt image {name}".format(user=message.chat.id, name=image_name))
        await image.download(path_to_image)
        await message.answer(text="Идет распознавание чека")
        # self.img_parser.find_receipt_on_image_and_crop_it(path_to_image)
        items = await self.img_parser.parse(image_name)
        if len(items) == 0:
            await self._bot.send_message(
                chat_id=message.chat.id,
                text="Не удалось распознать чек :( \n"
                     "Сфотографируйте его как можно ближе и без вспышки"
            )
        else:
            await self.send_raw_items_for_validation(message, items)

    async def parse_receipt_qr_and_send_poll(self, message: types.Message):
        system_log("User: {user}, Start parsing qr code {code}".format(user=message.chat.id, code=message.text))
        items = await self.qr_parser.get_ticket_items(qr=message.text)
        if len(items) == 0:
            await self._bot.send_message(
                chat_id=message.chat.id,
                text="Не удалось получить чек у налоговой службы, попробуйте позже"
            )
        else:
            await message.answer(text="Данные по чеку получены")
            receipt_document = self.init_receipt_document(chat_id=message.chat.id, raw_items=[])
            self._db.set_receipt(document=receipt_document)
            await self.ask_for_shared_items(
                message=message, receipt_id=receipt_document[RECEIPT_ID], items=items
            )

    async def send_raw_items_for_validation(self, message, items):
        raw_items = ""
        for i, item in enumerate(items):
            raw_items += self.RAW_ITEM_PATTERN.format(
                position=i, name=item["name"], quantity=item["quantity"], price=item["price"]
            )

        receipt_document = self.init_receipt_document(chat_id=message.chat.id, raw_items=items)
        self._db.set_receipt(document=receipt_document)

        await message.answer(text=raw_items)
        await self._bot.send_message(
            chat_id=message.chat.id,
            text="Проверьте, что количество и сумма распознанных элементов совпадает с чеком",
            reply_markup=self.markup.validation_markup
        )

    async def raw_items_validation(self, message: types.Message):
        receipt = self._db.get_receipt_by_state(
            chat_id=message.chat.id,
            state_id=self.state.ITEMS_VALIDATION
        )
        if message.text == self.markup.APPROVE:
            await self.ask_for_shared_items(
                message=message, receipt_id=receipt[RECEIPT_ID], items=receipt[RAW_ITEMS]
            )
        elif message.text == self.markup.DISAPPROVE:
            self._db.update_receipt_by_id(
                receipt_id=receipt[RECEIPT_ID],
                update={
                    DIALOG_STATE_ID: self.state.ITEMS_CORRECTION,
                    ACCESS_TIMESTAMP: time.time()
                }
            )
            await message.answer(text="Скопируйте распознанный текст чека, поправьте опечатки и перешлите боту")
            await message.answer(text="Если качество очень плохое, то можно отправить новую фотографию")
        else:
            await message.answer(text="Воспользуйтесь кнопками")

    async def ask_for_shared_items(self, message, receipt_id, items):
        self._db.update_receipt_by_id(
            receipt_id=receipt_id,
            update={
                DIALOG_STATE_ID: self.state.CHOOSE_SHARED_ITEMS,
                ACCESS_TIMESTAMP: time.time(),
                CLEAN_ITEMS: items
            }
        )
        await self._bot.send_message(
            chat_id=message.chat.id,
            text="Если в чеке есть позиции, которые делятся на несколько человек, то укажите их через пробел",
            reply_markup=self.markup.shared_options_markup
        )

    async def raw_items_correction(self, message: types.Message):
        corrected_items = []
        corrected_raw_items = re.split("\n\d\. ", message.text)
        for corrected_raw_item in corrected_raw_items:
            result = re.search(self.RAW_ITEM_REGEXP, corrected_raw_item)
            attrs = self.img_parser.get_item_attrs(result)
            corrected_item = self.img_parser.set_item_attrs(*attrs)
            corrected_items.append(corrected_item)

        receipt = self._db.get_receipt_by_state(
            chat_id=message.chat.id,
            state_id=self.state.ITEMS_CORRECTION
        )
        await self.ask_for_shared_items(message, receipt_id=receipt[RECEIPT_ID], items=corrected_items)

    async def mark_shared_items(self, message: types.Message):
        receipt = self._db.get_receipt_by_state(
            chat_id=message.chat.id,
            state_id=self.state.CHOOSE_SHARED_ITEMS
        )
        shared_items = []
        if message.text != self.markup.DISAPPROVE:
            for option in message.text.split():
                if option.isdigit():
                    shared_items.append(int(option))

        receipt[SHARED_ITEMS] = shared_items
        await self.form_and_send_poll(message, receipt)

    async def start_deeplink(self, message: types.Message):
        system_log("User: {user}, Creating receipt deeplink".format(user=message.chat.id))
        receipt = self._db.get_receipt_by_state(
            chat_id=message.chat.id,
            state_id=self.state.ENTER_VOTERS_COUNT
        )
        self._db.update_receipt_by_id(
            receipt_id=receipt[RECEIPT_ID],
            update={
                TOTAL_VOTERS_COUNT: int(message.text),
                DIALOG_STATE_ID: self.state.CLOSED,
                ACCESS_TIMESTAMP: time.time()
            }
        )
        link = await deep_linking.get_start_link(payload=self.DEEP_LINK_TRIGGER + receipt[RECEIPT_ID])
        await message.answer(text=link)
        system_log("User: {user}, Send created deeplink: {link}".format(user=message.chat.id, link=link))
        await message.answer(text="Скопируйте ссылку на опрос и отправьте друзьям")

    async def share_poll(self, message: types.Message):
        receipt_id = message.text.replace("/start {}".format(self.DEEP_LINK_TRIGGER), "")
        system_log("User: {user}, Start poll by deeplink: receipt {id}".format(user=message.chat.id, id=receipt_id))
        receipt = self._db.get_receipt(
            keys={
                RECEIPT_ID: receipt_id
            }
        )
        for poll in receipt[POLLS]:
            system_log("User: {user}, Forwarding poll, id: {id}".format(user=message.chat.id, id=poll[POLL_ID]))
            await self._bot.forward_message(
                chat_id=message.chat.id,
                from_chat_id=receipt[CHAT_ID],
                message_id=poll[MESSAGE_ID]
            )

    async def handle_poll_answer(self, answer: types.PollAnswer):
        def _update_voted_user(document):
            document[USER_ID] = answer.user.id
            document[OPTIONS_IDS] = answer.option_ids
            poll[USERS].append(document)

        def _update_user_clicks():
            for option_id in answer.option_ids:
                clicks_key = self.composite_key(OPTIONS, str(option_id), CLICKS)
                absolute_clicks_key = self.composite_key(db_poll_key, clicks_key)
                poll_update[absolute_clicks_key] = poll[OPTIONS][option_id][CLICKS] + 1

        def _update_poll(users_key, voters_key):
            poll_update[users_key] = poll[USERS]
            poll_update[voters_key] = poll[VOTERS_COUNT] + 1

        system_log("User: {user}, Answer handling, poll id: {id}".format(user=answer.user.id, id=answer.poll_id))
        receipt = self._db.get_receipt(
            keys={
                self.composite_key(POLLS, POLL_ID): answer.poll_id
            }
        )
        for i, poll in enumerate(receipt[POLLS]):
            if poll[POLL_ID] == answer.poll_id:
                poll_update = {}
                db_poll_key = self.composite_key(POLLS, str(i))
                db_users_key = self.composite_key(db_poll_key, USERS)
                db_voters_count_key = self.composite_key(db_poll_key, VOTERS_COUNT)

                system_log("User: {user}, Updating user, poll id: {id}".format(user=answer.user.id, id=answer.poll_id))
                _update_user_clicks()
                _update_voted_user(document=self._db.user)
                _update_poll(users_key=db_users_key, voters_key=db_voters_count_key)

                self._db.update_receipt_by_id(
                    receipt_id=receipt[RECEIPT_ID],
                    update=poll_update
                )
                if poll[VOTERS_COUNT] == receipt[TOTAL_VOTERS_COUNT] - 1:
                    system_log("User: {user}, Stop poll, id: {id}".format(user=answer.user.id, id=answer.poll_id))
                    await self._bot.stop_poll(
                        chat_id=receipt[CHAT_ID],
                        message_id=poll[MESSAGE_ID]
                    )

    async def close_poll(self, closed_poll: types.Poll):
        receipt = self._db.get_receipt(
            keys={
                self.composite_key(POLLS, POLL_ID): closed_poll.id
            }
        )
        all_polls_are_closed = True
        for poll in receipt[POLLS]:
            if poll[VOTERS_COUNT] < receipt[TOTAL_VOTERS_COUNT]:
                all_polls_are_closed = False

        if all_polls_are_closed:
            system_log("Closing receipt: {id}".format(id=receipt[RECEIPT_ID]))
            debt_results = self.debt_calculations(polls=receipt[POLLS])
            for user_id, debt in debt_results.items():
                system_log("User: {user}, Send debt to user: sum = {debt}".format(user=user_id, debt=debt))
                await self._bot.send_message(
                    chat_id=user_id,
                    text="Опрос окончен! \n"
                         "Ваш долг по чеку составляет {:.2f} руб".format(debt)
                )

    @staticmethod
    def debt_calculations(polls):
        users_debt_map = {}
        for poll in polls:
            for user in poll[USERS]:
                if user[USER_ID] not in users_debt_map:
                    users_debt_map[user[USER_ID]] = 0

                for option_id, option in enumerate(poll[OPTIONS]):
                    if option_id in user[OPTIONS_IDS]:
                        users_debt_map[user[USER_ID]] += option[PRICE] / option[CLICKS]
        return users_debt_map
