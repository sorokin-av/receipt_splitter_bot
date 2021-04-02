from aiogram.types import ReplyKeyboardMarkup, KeyboardButton


class ReplyMarkups:
    APPROVE = "Все верно"
    DISAPPROVE = "Нужны правки"
    NEXT = "Нет общих позиций"

    @property
    def markup(self):
        return ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)

    @property
    def validation_markup(self):
        yes_button = KeyboardButton(self.APPROVE)
        no_button = KeyboardButton(self.DISAPPROVE)
        return self.markup.row(yes_button, no_button)

    @property
    def shared_options_markup(self):
        button = KeyboardButton(self.NEXT)
        return self.markup.row(button)
