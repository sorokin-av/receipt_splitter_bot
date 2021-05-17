import os
import re
import uuid
import time
import pickle

from aiogram import types
from aiogram.utils import deep_linking

from utils.logger import behavior_log
from services.qr_parser import QRParser
from services.img_parser import ImageParser
from bot_config import INPUT_FOLDER
from services.fields import NAME, PRICE, QUANTITY
from db.db_connectors import ReceiptsDBConnector
from db.fields import *
from .keyboard import ReplyMarkups


class UserState:
    START = "0"
    ITEMS_VALIDATION = "1"
    ITEMS_CORRECTION = "2"
    ENTER_VOTERS_COUNT = "3"
    USERS_VOTE = "4"
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
        behavior_log("Init {bot}".format(bot=type(self).__name__))

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
    def unpickle_markup(markup):
        if isinstance(markup, bytes):
            return pickle.loads(markup)
        else:
            return markup

    @staticmethod
    async def start_message(message: types.Message):
        await message.answer("Отправьте, пожалуйста, расшифрованный QR-код в виде строки")

    def init_receipt_document(self, chat_id, data: dict):
        receipt_document = self._db.receipt_document
        receipt_document[CHAT_ID] = chat_id
        receipt_document[RECEIPT_ID] = uuid.uuid4().hex

        for key, value in data.items():
            receipt_document[key] = value
        return receipt_document

    def _get_user_document(self, user_id, markup):
        user = self._db.user
        user[USER_ID] = user_id
        user[OPTIONS_MARKUP] = pickle.dumps(markup)
        return user

    async def parse_receipt_image_and_send_poll(self, message: types.Message):
        image = message.photo[-1]
        image_name = image.file_unique_id + ".jpg"
        path_to_image = os.path.join(INPUT_FOLDER, image_name)
        behavior_log("User: {user}, Trying to fetch receipt image {name}".format(user=message.chat.id, name=image_name))
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
            receipt_document = self.init_receipt_document(
                chat_id=message.chat.id,
                data={
                    RAW_ITEMS: items,
                    DIALOG_STATE_ID: self.state.ITEMS_VALIDATION
                }
            )
            self._db.set_receipt(document=receipt_document)
            await self.send_raw_items_for_validation(message, items)

    async def parse_receipt_qr_and_send_poll(self, message: types.Message):
        behavior_log("User: {user}, Start parsing qr code {code}".format(user=message.chat.id, code=message.text))
        items = await self.qr_parser.get_ticket_items(qr=message.text)
        if len(items) == 0:
            await self._bot.send_message(
                chat_id=message.chat.id,
                text="Не удалось получить чек у налоговой службы, попробуйте позже"
            )
        else:
            await message.answer(text="Данные по чеку получены")
            await self.save_receipt_and_ask_for_voters_count(message, items)

    async def send_raw_items_for_validation(self, message, items):
        raw_items = ""
        for i, item in enumerate(items):
            raw_items += self.RAW_ITEM_PATTERN.format(
                position=i, name=item[NAME], quantity=item[QUANTITY], price=item[PRICE]
            )

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
        if message.text == self.markup.CORRECT:
            await self.save_receipt_and_ask_for_voters_count(
                message=message,
                items=receipt[RAW_ITEMS]
            )
        elif message.text == self.markup.NEED_CORRECTIONS:
            self._db.update_receipt_by_id(
                receipt_id=receipt[RECEIPT_ID],
                update={
                    DIALOG_STATE_ID: self.state.ITEMS_CORRECTION,
                    ACCESS_TIMESTAMP: time.time()
                }
            )
            await message.answer(text="Скопируйте распознанный текст чека, поправьте опечатки и перешлите боту")
        elif message.text == self.markup.INCORRECT:
            await message.answer(text="Если качество очень плохое, то можно отправить новую фотографию")
        else:
            await message.answer(text="Воспользуйтесь кнопками")

    async def raw_items_correction(self, message: types.Message):
        corrected_items = []
        corrected_raw_items = re.split("\n\d\. ", message.text)
        for corrected_raw_item in corrected_raw_items:
            result = re.search(self.RAW_ITEM_REGEXP, corrected_raw_item)
            attrs = self.img_parser.get_item_attrs(result)
            corrected_item = self.img_parser.set_item_attrs(*attrs)
            corrected_items.append(corrected_item)

        await self.save_receipt_and_ask_for_voters_count(
            message=message,
            items=corrected_items
        )

    @staticmethod
    def combine_identical_items(items):
        words_pool = set()
        combined_items = {}
        for item in items:
            name, price = item.get(NAME), item.get(PRICE)
            cleaned_name = set([word for word in name.split() if len(word) > 1])
            if words_pool.intersection(name.split()):
                combine_flag = False
                for comb_name, comb_item in combined_items.items():
                    if cleaned_name.intersection(comb_name.split()) and comb_item[PRICE] == price:
                        combined_items[comb_name][QUANTITY] += 1
                        combine_flag = True
                        break
                if not combine_flag:
                    combined_items.update({name: item})
            else:
                combined_items.update({name: item})

            words_pool.update(cleaned_name)
        return list(combined_items.values())

    async def save_receipt_and_ask_for_voters_count(self, message, items):
        combined_items = self.combine_identical_items(items)
        inline_markup = self.markup.inline_options(combined_items)
        receipt_document = self.init_receipt_document(
            chat_id=message.chat.id,
            data={
                CLEAN_ITEMS: combined_items,
                DIALOG_STATE_ID: self.state.ENTER_VOTERS_COUNT,
                RECEIPT_MARKUP: pickle.dumps(inline_markup)
            }
        )
        self._db.set_receipt(document=receipt_document)
        await message.answer(text="На скольких человек делим чек?")

    async def create_start_deeplink(self, message: types.Message):
        behavior_log("User: {user}, Creating receipt deeplink".format(user=message.chat.id))
        receipt = self._db.get_receipt_by_state(
            chat_id=message.chat.id,
            state_id=self.state.ENTER_VOTERS_COUNT
        )
        self._db.update_receipt_by_id(
            receipt_id=receipt[RECEIPT_ID],
            update={
                TOTAL_VOTERS_COUNT: int(message.text),
                DIALOG_STATE_ID: self.state.USERS_VOTE,
                ACCESS_TIMESTAMP: time.time()
            }
        )
        link = await deep_linking.get_start_link(payload=self.DEEP_LINK_TRIGGER + receipt[RECEIPT_ID])
        await message.answer(text=link)
        behavior_log("User: {user}, Send created deeplink: {link}".format(user=message.chat.id, link=link))
        await message.answer(text="Скопируйте ссылку на опрос и отправьте друзьям")

    async def start_inline_poll(self, message: types.Message):
        user_id = str(message.from_user.id)
        receipt_id = message.text.replace("/start {}".format(self.DEEP_LINK_TRIGGER), "")
        behavior_log("User: {user}, Start poll by deeplink: receipt {id}".format(user=message.chat.id, id=receipt_id))
        receipt = self._db.get_receipt(keys={RECEIPT_ID: receipt_id})

        behavior_log("User: {user}, Set inline poll for user".format(user=message.chat.id))
        inline_markup = self.markup.inline_options(
            items=receipt[CLEAN_ITEMS],
            total_voters_count=receipt[TOTAL_VOTERS_COUNT]
        )
        self._db.update_receipt_by_id(
            receipt_id=receipt[RECEIPT_ID],
            update={
                self.composite_key(USERS, user_id): self._get_user_document(user_id, inline_markup),
                ACCESS_TIMESTAMP: time.time()
            }
        )
        behavior_log("User: {user}, Sending inline poll".format(user=message.chat.id))
        await self._bot.send_message(
            chat_id=message.chat.id,
            text="Выберите нужные позиции в чеке \nДля общих позиций нажмите на 'шаг' чтобы сделать его дробным",
            reply_markup=inline_markup
        )

    async def inline_poll_handler(self, callback_query: types.CallbackQuery):
        user_id = str(callback_query.from_user.id)
        behavior_log("User: {user}, Inline poll callback handle".format(user=user_id))

        receipt = self._db.get_receipt(
            keys={
                self.composite_key(USERS, user_id, USER_ID): user_id,
                DIALOG_STATE_ID: self.state.USERS_VOTE
            },
            sort=[(ACCESS_TIMESTAMP, -1)]
        )
        pickled_markup = receipt[USERS][user_id][OPTIONS_MARKUP]
        user_markup = self.unpickle_markup(pickled_markup)
        await self.edit_inline_poll(callback_query, user_markup, receipt)

        if callback_query.data == self.markup.callback_data.CLOSE_POLL:
            await self.close_inline_poll(callback_query, receipt)

    async def edit_inline_poll(self, callback, user_markup, receipt):
        user_id = str(callback.from_user.id)
        behavior_log("User: {user}, Edit poll with callback {data}".format(user=user_id, data=callback.data))

        updated_user_markup, updated_master_markup = self.markup.update_options_markup(
            callback_data=callback.data,
            master_markup=self.unpickle_markup(receipt[RECEIPT_MARKUP]),
            user_markup=user_markup
        )
        self._db.update_receipt_by_id(
            receipt_id=receipt[RECEIPT_ID],
            update={
                self.composite_key(USERS, user_id, OPTIONS_MARKUP): updated_user_markup,
                RECEIPT_MARKUP: updated_master_markup,
                ACCESS_TIMESTAMP: time.time()
            }
        )
        await self._bot.answer_callback_query(callback.id)
        await self._edit_inline_poll(callback, updated_user_markup)

    async def _edit_inline_poll(self, callback, updated_markup):
        return await self._bot.edit_message_text(
            text=callback.message.text,
            chat_id=callback.from_user.id,
            message_id=callback.message.message_id,
            reply_markup=updated_markup
        )

    async def close_inline_poll(self, callback, receipt):
        voters_count = receipt[VOTERS_COUNT] + 1
        behavior_log("User: {user}, Closing poll".format(user=callback.from_user.id))

        self._db.update_receipt_by_id(
            receipt_id=receipt[RECEIPT_ID],
            update={
                VOTERS_COUNT: voters_count,
                ACCESS_TIMESTAMP: time.time()
            }
        )
        await self._bot.answer_callback_query(callback.id)
        await self._bot.send_message(
            chat_id=callback.message.chat.id,
            text="Спасибо! Ожидате окончания голосования"
        )
        if voters_count == receipt[TOTAL_VOTERS_COUNT]:
            await self.close_receipt(receipt_id=receipt[RECEIPT_ID])

    async def close_receipt(self, receipt_id):
        behavior_log("Closing receipt: {id}".format(id=receipt_id))
        receipt = self._db.get_receipt(keys={RECEIPT_ID: receipt_id})

        debt_results = self.debt_calculations(receipt)
        for user_id, debt in debt_results.items():
            behavior_log("User: {user}, Send debt to user: sum = {debt}".format(user=user_id, debt=debt))
            await self._bot.send_message(
                chat_id=user_id,
                text="Опрос окончен! \n"
                     "Ваш долг по чеку составляет {:.2f} руб".format(debt)
            )

    def debt_calculations(self, receipt):
        users_debt_map = {}
        users = receipt[USERS]
        master_markup = self.unpickle_markup(receipt[RECEIPT_MARKUP])
        for user_id, user in users.items():
            user_markup = self.unpickle_markup(user[OPTIONS_MARKUP])
            if user_id not in users_debt_map:
                users_debt_map[user_id] = 0

            for option_id, option in enumerate(user_markup.inline_keyboard):
                option = option[0]
                if hasattr(option, QUANTITY) and option.quantity > 0:
                    voters_quantity = master_markup.inline_keyboard[option_id][0].quantity
                    users_debt_map[user_id] += option.price / voters_quantity
        return users_debt_map
