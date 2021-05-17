from copy import copy
from fractions import Fraction

from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup

from db.fields import TOTAL_VOTERS_COUNT
from services.fields import NAME, QUANTITY, PRICE


class CallbackData:
    QUANTITY = "quantity"
    DECREMENT = "decrement"
    INCREMENT = "increment"
    QUANTITY_STEP = "quantity_step"
    CLOSE_POLL = "close_poll"


class ReplyMarkups:
    CORRECT = "Все верно"
    NEED_CORRECTIONS = "Нужны правки"
    INCORRECT = "Все плохо"
    CLOSE_POLL = "Завершить Опрос"
    DEFAULT_STEP = 1
    DEFAULT_QUANTITY = 0
    MAX_OPTIONS = 30
    MAX_OPTION_NAME_LEN = 25

    def __init__(self):
        self.callback_data = CallbackData()

    @property
    def reply_markup(self):
        return ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)

    @property
    def validation_markup(self):
        yes_button = KeyboardButton(self.CORRECT)
        corrections_button = KeyboardButton(self.NEED_CORRECTIONS)
        no_button = KeyboardButton(self.INCORRECT)
        return self.reply_markup.row(yes_button, corrections_button, no_button)

    @staticmethod
    def _form_callback(*args):
        return ".".join(args)

    @staticmethod
    def _parse_callback(data):
        return data.split(".")

    @property
    def close_poll(self):
        return InlineKeyboardButton(
            text=self.CLOSE_POLL,
            callback_data=self.callback_data.CLOSE_POLL
        )

    def inline_options(self, items, **kwargs):
        def _set_init_option_params():
            button.name = item[NAME]
            button.price = item[PRICE]
            button.total_quantity = int(item[QUANTITY])
            button.quantity = Fraction(self.DEFAULT_QUANTITY)
            button.default_step = Fraction(self.DEFAULT_STEP)
            button.custom_step = Fraction(self.DEFAULT_STEP, kwargs.get(TOTAL_VOTERS_COUNT, 1))
            button.quantity_step = button.default_step

        inline_markup = InlineKeyboardMarkup()
        items = items[:self.MAX_OPTIONS]
        for index, item in enumerate(items):
            name = item[NAME][:self.MAX_OPTION_NAME_LEN]
            # price = int(item["price"] / int(item["quantity"]))
            price = int(item[PRICE])
            quantity = int(item[QUANTITY])
            text = "{name}: {price} руб, {quantity} шт.".format(name=name, price=price, quantity=quantity)
            button = InlineKeyboardButton(
                text=text,
                callback_data=self._form_callback(str(index))
            )
            _set_init_option_params()
            inline_markup.add(button)

        return inline_markup.add(self.close_poll)

    def set_option_quantity(self, item_id, option):
        quantity_choice_row = list()
        quantity_step_view = "шаг: {}".format(option.quantity_step)
        quantity_callback = self._form_callback(str(item_id), self.callback_data.QUANTITY)
        decrement_callback = self._form_callback(str(item_id), self.callback_data.DECREMENT)
        increment_callback = self._form_callback(str(item_id), self.callback_data.INCREMENT)
        step_callback = self._form_callback(str(item_id), self.callback_data.QUANTITY_STEP)
        quantity_choice_row.append(InlineKeyboardButton(text="-", callback_data=decrement_callback))
        quantity_choice_row.append(InlineKeyboardButton(text=str(option.quantity), callback_data=quantity_callback))
        quantity_choice_row.append(InlineKeyboardButton(text="+", callback_data=increment_callback))
        quantity_choice_row.append(InlineKeyboardButton(text=quantity_step_view, callback_data=step_callback))
        return quantity_choice_row

    @staticmethod
    def remove_quantity_choice_row(markup, item_id=None):
        markup_copy = copy(markup)
        remove_choice_row_flag = False
        for index, option in enumerate(markup_copy.inline_keyboard):
            if len(option) > 1:
                markup.inline_keyboard.pop(index)
                if (item_id or item_id == 0) and index == item_id + 1:
                    remove_choice_row_flag = True
        return remove_choice_row_flag

    def update_options_markup(self, callback_data, user_markup, master_markup):
        def _quantity_step_handler():
            if self.callback_data.QUANTITY_STEP in parsed_callback:
                if option.quantity_step == option.default_step:
                    option.quantity_step = option.custom_step
                else:
                    option.quantity_step = option.default_step

        def _quantity_handler():
            master_option = master_markup.inline_keyboard[item_id][0]
            if self.callback_data.DECREMENT in parsed_callback:
                if option.quantity - option.quantity_step >= 0:
                    option.quantity -= option.quantity_step
                    master_option.quantity -= option.quantity_step
                else:
                    if option.quantity > self.DEFAULT_QUANTITY:
                        master_option.quantity -= option.quantity
                        option.quantity = self.DEFAULT_QUANTITY
            elif self.callback_data.INCREMENT in parsed_callback:
                if option.quantity + option.quantity_step <= option.total_quantity:
                    option.quantity += option.quantity_step
                    master_option.quantity += option.quantity_step
                else:
                    if option.quantity < option.total_quantity:
                        master_option.quantity += option.total_quantity - option.quantity
                        option.quantity = option.total_quantity

        if callback_data.isdigit():
            item_id = int(callback_data)
            flag = self.remove_quantity_choice_row(user_markup, item_id=item_id)
            if not flag:
                option = user_markup.inline_keyboard[item_id][0]
                quantity_choice_row = self.set_option_quantity(item_id, option)
                user_markup.inline_keyboard.insert(item_id + 1, quantity_choice_row)
        else:
            if callback_data == self.callback_data.CLOSE_POLL:
                self.remove_quantity_choice_row(user_markup)
            else:
                parsed_callback = self._parse_callback(callback_data)
                item_id = int(parsed_callback[0])
                option = user_markup.inline_keyboard[item_id][0]

                _quantity_step_handler()
                _quantity_handler()

                quantity_choice_row = self.set_option_quantity(item_id, option)
                user_markup.inline_keyboard[item_id+1] = quantity_choice_row
        return user_markup, master_markup
