import logging.config

from bot_config import get_config, LOGGING_CONFIG_PATH, LOGGER_NAME

behavior_logger = logging.getLogger(LOGGER_NAME)


def init_logger():
    config = get_config(LOGGING_CONFIG_PATH)
    logging.config.dictConfig(config)


def behavior_log(message, level="INFO", exc_info=None):
    try:
        level_name = logging.getLevelName(level)
        behavior_logger.log(level_name, message, exc_info=exc_info)
    except:
        behavior_logger.log(logging.getLevelName("ERROR"), "behavior_log: Failed to write a log", exc_info=True)
