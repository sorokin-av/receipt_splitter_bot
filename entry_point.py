from os import getenv

from aiogram import executor, Bot, Dispatcher
from dotenv import load_dotenv

from bot.receipt_bot import ReceiptBot
from utils.logger import init_logger
from bot_config import BOT_TOKEN, CREDENTIALS_PATH


load_dotenv(dotenv_path=CREDENTIALS_PATH)


if __name__ == '__main__':
    init_logger()

    dp = Dispatcher(bot=Bot(token=getenv(BOT_TOKEN)))
    bot = ReceiptBot(dispatcher=dp)

    dp.register_message_handler(bot.start_inline_poll, lambda message: bot.check_deeplink(message.text))
    dp.register_message_handler(bot.start_message, commands=["start"])
    dp.register_message_handler(bot.parse_receipt_qr_and_send_poll, lambda message: bot.check_qr_code(message.text))
    dp.register_message_handler(bot.parse_receipt_image_and_send_poll, content_types=["photo"])
    dp.register_callback_query_handler(bot.inline_poll_handler)

    dp.register_message_handler(
        bot.raw_items_validation, lambda message: bot.state_handler(message, state_id=bot.state.ITEMS_VALIDATION)
    )
    dp.register_message_handler(
        bot.raw_items_correction, lambda message: bot.state_handler(message, state_id=bot.state.ITEMS_CORRECTION)
    )
    dp.register_message_handler(
        bot.create_start_deeplink, lambda message: bot.state_handler(message, state_id=bot.state.ENTER_VOTERS_COUNT)
    )

    executor.start_polling(dp)
