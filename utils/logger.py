import logging.config

from bot_config import get_config, LOGGING_CONFIG_PATH, SYSTEM_LOGGER_NAME

system_logger = logging.getLogger(SYSTEM_LOGGER_NAME)


def init_logger():
    config = get_config(LOGGING_CONFIG_PATH)
    logging.config.dictConfig(config)


def system_log(message, level="INFO", exc_info=None):
    try:
        level_name = logging.getLevelName(level)
        system_logger.log(level_name, message, exc_info=exc_info)
    except:
        system_logger.log(logging.getLevelName("ERROR"), "system_log: Failed to write a log", exc_info=True)
