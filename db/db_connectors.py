import time
import pickle

from pymongo import MongoClient
from redis import Redis

from utils.logger import system_log
from .fields import *


class MongoBase:
    URI = "mongodb://localhost:27017"
    POLL_DATABASE = "poll_db"

    def __init__(self):
        self._client = MongoClient(self.URI)
        self._db = self._client[self.POLL_DATABASE]

    def insert_one(self, collection, data):
        self._db[collection].insert_one(document=data)

    def find(self, collection, query, many=False, **kwargs):
        if many:
            document = []
            cursor = self._db[collection].find(filter=query, **kwargs)
            for doc in cursor:
                document.append(doc)
        else:
            document = self._db[collection].find_one(filter=query, **kwargs)
        return document

    def update_one(self, collection, query, data):
        document = self._db[collection].update_one(filter=query, update=data)
        return document

    def delete_one(self, collection, query):
        document = self._db[collection].delete_one(filter=query)
        return document

    def drop(self, collection):
        self._db[collection].drop()


class ReceiptsDBConnector(MongoBase):
    RECEIPTS = "receipts_collection"

    def __init__(self):
        system_log("Init {connector}".format(connector=type(self).__name__))
        super().__init__()
        # self.drop(self.RECEIPTS)

    @property
    def receipt_document(self):
        return {
            CHAT_ID: "",
            RECEIPT_ID: "",
            RAW_ITEMS: [],
            CLEAN_ITEMS: [],
            SHARED_ITEMS: [],
            DIALOG_STATE_ID: "",
            ACCESS_TIMESTAMP: time.time(),
            IS_RECEIPT_CLOSED: False,
            TOTAL_VOTERS_COUNT: 0,
            POLLS: []
        }

    @property
    def poll(self):
        return {
            POLL_ID: "",
            MESSAGE_ID: "",
            VOTERS_COUNT: 0,
            OPTIONS: [],
            USERS: []
        }

    @property
    def option(self):
        return {
            TEXT: "",
            PRICE: "",
            CLICKS: 0
        }

    @property
    def user(self):
        return {
            USER_ID: "",
            DEBT_SUM: 0,
            OPTIONS_IDS: []
        }

    def set_receipt(self, document):
        system_log("Insert in {coll} document: {doc}".format(coll=self.RECEIPTS, doc=document))
        self.insert_one(collection=self.RECEIPTS, data=document)
        system_log("Document was successfully inserted")

    def get_dialog_state(self, chat_id):
        current_receipt = self.find(
            collection=self.RECEIPTS,
            query={
                CHAT_ID: chat_id
            },
            sort=[(ACCESS_TIMESTAMP, -1)]
        )
        return current_receipt[DIALOG_STATE_ID] if current_receipt else None

    def get_receipt(self, keys, **kwargs):
        system_log("Find document in {coll} by query: {query}".format(coll=self.RECEIPTS, query=keys))
        receipt = self.find(
            collection=self.RECEIPTS,
            query=keys,
            **kwargs
        )
        system_log("Document was successfully found: {doc}".format(doc=receipt))
        return receipt if receipt else {}

    def get_receipt_by_state(self, chat_id, state_id):
        return self.get_receipt(
            keys={
                CHAT_ID: chat_id,
                DIALOG_STATE_ID: state_id
            },
            sort=[(ACCESS_TIMESTAMP, -1)]
        )

    def update_receipt_by_id(self, receipt_id, update):
        set_key = "$set"
        mongo_update = {set_key: {}}

        for key, value in update.items():
            mongo_update[set_key][key] = value

        system_log("Update document in {coll} by id: {id}".format(coll=self.RECEIPTS, id=receipt_id))
        self.update_one(
            collection=self.RECEIPTS,
            query={
                RECEIPT_ID: receipt_id
            },
            data=mongo_update
        )
        system_log("Document was successfully updated. Update data: {data}".format(data=update))

    @property
    def all_documents(self):
        return list(self._db[self.RECEIPTS].find({}))


class UserConnector(MongoBase):
    USER_STATE = "state_collection"

    def __init__(self):
        super().__init__()
        self.drop(collection=self.USER_STATE)

    def set_user_state(self, chat_id, **kwargs):
        state = {
            "chat_id": chat_id,
            "message_id": kwargs.get("message_id"),
            "state": {
                "state_id": kwargs.get("state_id"),
                "poll_id": kwargs.get("poll_id")
            }
        }
        self.insert_one(collection=self.USER_STATE, data=state)

    def get_user_state(self, chat_id):
        state = self.find_one(
            collection=self.USER_STATE,
            query={"chat_id": chat_id}
        )
        return state.get("state") if state else {}

    def get_poll_state(self, poll_id):
        state = self.find_one(
            collection=self.USER_STATE,
            query={"state.poll_id": poll_id}
        )
        return state if state else {}

    @property
    def all_documents(self):
        return list(self._db[self.USER_STATE].find({}))


class PollConnector(MongoBase):
    POLLS = "polls_collection"

    def __init__(self):
        super().__init__()
        self.drop(collection=self.POLLS)

    def set_poll(self, poll_id, poll, users=None):
        poll = {
            "poll_id": poll_id,
            "poll": pickle.dumps(poll),
            "users": users or {}
        }
        self.insert_one(collection=self.POLLS, data=poll)

    def get_poll(self, poll_id, document_key="poll"):
        poll = self.find_one(
            collection=self.POLLS,
            query={"poll_id": poll_id}
        )
        for key, value in poll.items():
            if isinstance(value, bytes):
                poll[key] = pickle.loads(value)

        if document_key:
            return poll.get(document_key) if poll else {}
        else:
            return poll

    def update_poll(self, poll_id, **kwargs):
        set_key = "$set"
        update = {set_key: {}}
        for key, value in kwargs.items():
            if key == "new_poll_id":
                update[set_key]["poll_id"] = value
            else:
                update[set_key][key] = pickle.dumps(value)

        self.update_one(
            collection=self.POLLS,
            query={"poll_id": poll_id},
            data=update
        )

    def delete_poll(self, poll_id):
        self.delete_one(
            collection=self.POLLS,
            query={"poll_id": poll_id}
        )

    @property
    def all_documents(self):
        return list(self._db[self.POLLS].find({}))


class RedisConnector:
    def __init__(self):
        self._db = Redis()
        self.flush()

    def set(self, key, value):
        self._db.set(key, value)

    def get(self, key):
        value = self._db.get(key)
        if isinstance(value, bytes):
            value = value.decode()
        return value

    @property
    def all_keys(self):
        return [x for x in self._db.scan_iter()]

    def flush(self):
        for key in self.all_keys:
            self._db.delete(key)
