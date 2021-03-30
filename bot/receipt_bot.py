import os
import uuid
from copy import deepcopy

from aiogram import types
from aiogram.utils import deep_linking

from utils.logger import system_log
from services.qr_parser import QRParser
from services.img_parser import ImageParser
from bot_config import INPUT_FOLDER
from db.db_connectors import ReceiptsDBConnector
from db.fields import *


class ReceiptBot:
    START_STATE = "0"
    ENTER_VOTERS_STATE = "1"
    CLOSED_STATE = "2"

    DEEP_LINK_TRIGGER = "receipt"

    def __init__(self, dispatcher):
        self._bot = dispatcher.bot
        self._db = ReceiptsDBConnector()
        self.qr_parser = QRParser()
        self.img_parser = ImageParser()
        system_log("Init {bot}".format(bot=type(self).__name__))

    @staticmethod
    def check_qr_code(text):
        return text and "fp" in text and "fn" in text

    def check_deeplink(self, text):
        return text and self.DEEP_LINK_TRIGGER in text.lower()

    def state_handler(self, message, value):
        chat_id = message.chat.id
        states = self._db.get_dialog_states(chat_id)
        return value in states

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

    def init_receipt_document(self, chat_id):
        receipt_document = self._db.receipt_document
        receipt_document[CHAT_ID] = chat_id
        receipt_document[RECEIPT_ID] = uuid.uuid4().hex
        receipt_document[DIALOG_STATE_ID] = self.ENTER_VOTERS_STATE
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

    async def form_and_send_poll(self, message, items):
        poll = types.Poll()
        poll.question = "Выберите нужные позиции в чеке"
        polls, raw_options = self._form_polls_from_receipt(poll, items)

        receipt_document = self.init_receipt_document(chat_id=message.chat.id)

        for i, poll in enumerate(polls):
            poll_info = await self._send_poll(chat_id=message.chat.id, poll=poll)
            poll_document = self._get_poll_document(poll_info, raw_poll_options=raw_options[i])
            receipt_document[POLLS].append(poll_document)

        self._db.set_receipt(document=receipt_document)
        await message.answer(text="На скольких человек делим чек?")

    async def parse_receipt_image_and_send_poll(self, message: types.Message):
        image = message.photo[-1]
        image_name = image.file_unique_id + ".jpg"
        path_to_image = os.path.join(INPUT_FOLDER, image_name)
        await image.download(path_to_image)
        # self.img_parser.find_receipt_on_image_and_crop_it(path_to_image)
        items = self.img_parser.parse_items(image_name)
        if len(items) == 0:
            await self._bot.send_message(
                chat_id=message.chat.id,
                text="Не удалось распознать чек :( \n"
                     "Сфотографируйте чек как можно ближе и без вспышки"
            )
        else:
            await self.form_and_send_poll(message, items)

    async def parse_receipt_qr_and_send_poll(self, message: types.Message):
        items = await self.qr_parser.get_ticket_items(qr=message.text)
        if len(items) == 0:
            await self._bot.send_message(
                chat_id=message.chat.id,
                text="Не удалось получить чек у налоговой службы, попробуйте позже"
            )
        else:
            await self.form_and_send_poll(message, items)

    async def start_deeplink(self, message: types.Message):
        receipt = self._db.get_receipt(
            keys={
                CHAT_ID: message.chat.id,
                DIALOG_STATE_ID: self.ENTER_VOTERS_STATE
            }
        )
        self._db.update_receipt_by_id(
            receipt_id=receipt[RECEIPT_ID],
            update={
                TOTAL_VOTERS_COUNT: int(message.text),
                DIALOG_STATE_ID: self.CLOSED_STATE
            }
        )
        link = await deep_linking.get_start_link(payload=self.DEEP_LINK_TRIGGER + receipt[RECEIPT_ID])
        await message.answer(text=link)
        await message.answer(text="Скопируйте ссылку на опрос и отправьте друзьям")

    async def share_poll(self, message: types.Message):
        receipt_id = message.text.replace("/start {}".format(self.DEEP_LINK_TRIGGER), "")
        receipt = self._db.get_receipt(
            keys={
                RECEIPT_ID: receipt_id
            }
        )
        for poll in receipt[POLLS]:
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

                _update_user_clicks()
                _update_voted_user(document=self._db.user)
                _update_poll(users_key=db_users_key, voters_key=db_voters_count_key)

                self._db.update_receipt_by_id(
                    receipt_id=receipt[RECEIPT_ID],
                    update=poll_update
                )
                if poll[VOTERS_COUNT] == receipt[TOTAL_VOTERS_COUNT] - 1:
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
            debt_results = self.debt_calculations(polls=receipt[POLLS])
            for user_id, debt in debt_results.items():
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
