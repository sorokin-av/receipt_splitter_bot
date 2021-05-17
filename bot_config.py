import os
import yaml


BASE_PATH = os.getcwd()
CONNECT_TIMEOUT, READ_TIMEOUT = 5, 10
LOGGER_NAME = "behavior_logger"

BOT_TOKEN = "BOT_SECRET_TOKEN"
FEDERAL_TAX_LOGIN = "FEDERAL_TAX_INN"
FEDERAL_TAX_PASSWORD = "FEDERAL_TAX_PASSWORD"
FEDERAL_TAX_SECRET_TOKEN = "FEDERAL_TAX_SECRET_TOKEN"

DATA_PATH = BASE_PATH + "/data"
CONFIGS_PATH = BASE_PATH + "/configs"
CREDENTIALS_PATH = BASE_PATH + "/credentials.env"
LOGGING_CONFIG_PATH = os.path.join(CONFIGS_PATH, "logging.yml")
PARSER_CONFIG_PATH = os.path.join(CONFIGS_PATH, "config.yml")

INPUT_FOLDER = os.path.join(DATA_PATH, "img")
TMP_FOLDER = os.path.join(DATA_PATH, "tmp")
OUTPUT_FOLDER = os.path.join(DATA_PATH, "txt")


def get_config(path):
    with open(path) as f:
        config = yaml.safe_load(f)
    return config or None
