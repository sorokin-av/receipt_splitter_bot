import os
import io
import re
import asyncio
import fnmatch
from copy import copy
from functools import partial
from concurrent.futures import ProcessPoolExecutor
from wand.image import Image as WandImage

import cv2
import numpy as np
import matplotlib.pyplot as plt
from PIL import Image
from pytesseract import pytesseract
from imutils.perspective import four_point_transform
from receipt_parser_core.enhancer import enhance_image, sharpen_image
from receipt_parser_core.config import read_config

from bot_config import PARSER_CONFIG_PATH, INPUT_FOLDER, TMP_FOLDER
from utils.logger import system_log


class ImageParser:
    NAME = "name"
    QUANTITY = "quantity"
    PRICE = "price"

    def __init__(self):
        self._config = read_config(PARSER_CONFIG_PATH)
        system_log("Init {parser}".format(parser=type(self).__name__))

    @property
    def item(self):
        return {
            self.NAME: "",
            self.QUANTITY: 0,
            self.PRICE: 0
        }

    @staticmethod
    def plot_rgb(image):
        plt.figure(figsize=(16, 10))
        return plt.imshow(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))

    @staticmethod
    def plot_gray(image):
        plt.figure(figsize=(16, 10))
        return plt.imshow(image, cmap='Greys_r')

    @staticmethod
    def opencv_resize(image, ratio):
        width = int(image.shape[1] * ratio)
        height = int(image.shape[0] * ratio)
        dim = (width, height)
        return cv2.resize(image, dim, interpolation=cv2.INTER_AREA)

    @staticmethod
    def approximate_contour(contour):
        peri = cv2.arcLength(contour, True)
        return cv2.approxPolyDP(contour, 0.032 * peri, True)

    def get_receipt_contour(self, contours):
        for c in contours:
            approx = self.approximate_contour(c)
            if len(approx) == 4:
                return approx

    @staticmethod
    def contour_to_rectangle(contour, resize_ratio):
        pts = contour.reshape(4, 2)
        rect = np.zeros((4, 2), dtype="float32")

        s = pts.sum(axis=1)
        rect[0] = pts[np.argmin(s)]
        rect[2] = pts[np.argmax(s)]

        diff = np.diff(pts, axis=1)
        rect[1] = pts[np.argmin(diff)]
        rect[3] = pts[np.argmax(diff)]
        return rect / resize_ratio

    def _resize_and_blur(self, image, resize_ratio):
        image = self.opencv_resize(image, resize_ratio)
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        rect_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
        return cv2.dilate(blurred, rect_kernel)

    @staticmethod
    def _find_receipt_contours(dilated_image):
        edged = cv2.Canny(dilated_image, 50, 200, apertureSize=3)
        contours, hierarchy = cv2.findContours(edged, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        largest_contours = sorted(contours, key=cv2.contourArea, reverse=True)[:10]
        return largest_contours

    def find_receipt_on_image_and_crop_it(self, filename):
        img = Image.open(filename)
        img.thumbnail((800, 800), Image.ANTIALIAS)
        image = cv2.imread(filename)
        original = image.copy()

        resize_ratio = 500 / image.shape[0]
        dilated = self._resize_and_blur(image, resize_ratio)
        largest_contours = self._find_receipt_contours(dilated)
        receipt_contour = self.get_receipt_contour(largest_contours)

        if receipt_contour is not None and len(receipt_contour) > 0:
            scanned = four_point_transform(original.copy(), self.contour_to_rectangle(receipt_contour, resize_ratio))
            cv2.imwrite(filename, scanned)

    def sharpen_image_and_run_ocr(self, tmp_path):
        sharpen_image(tmp_path, tmp_path, rotate=False)
        with io.BytesIO() as transfer:
            with WandImage(filename=tmp_path) as img:
                img.save(transfer)

            with Image.open(transfer) as img:
                image_data = pytesseract.image_to_string(
                    img, lang=self._config.language, timeout=5, config="--psm 6"
                )
        return image_data

    @staticmethod
    def _enhance_image(filename, blur=False):
        input_path = INPUT_FOLDER + "/" + filename
        img = enhance_image(cv2.imread(input_path), gaussian_blur=blur)

        prefix = "1" if blur else "2"
        tmp_path = os.path.join(TMP_FOLDER, prefix + filename)

        cv2.imwrite(tmp_path, img)
        return tmp_path

    async def parse(self, filename):
        system_log("Start processing image {name}".format(name=filename))

        blurred_img = self._enhance_image(filename, blur=True)
        non_blurred_img = self._enhance_image(filename, blur=False)
        blur_index, non_blur_index = 0, 1

        loop = asyncio.get_running_loop()
        with ProcessPoolExecutor(max_workers=2) as pool:
            futures = [
                loop.run_in_executor(pool, partial(self.sharpen_image_and_run_ocr, blurred_img)),
                loop.run_in_executor(pool, partial(self.sharpen_image_and_run_ocr, non_blurred_img))
            ]
            result = await asyncio.gather(*futures)

        items = [self.extract_items(result[i].splitlines(True)) for i in range(len(result))]
        if len(items[blur_index]) >= len(items[non_blur_index]):
            return items[blur_index]
        else:
            return items[non_blur_index]

    def extract_items(self, receipt_lines):
        items = []
        for line in receipt_lines:
            for stop_word in self._config.sum_keys:
                if fnmatch.fnmatch(line, f"*{stop_word}*"):
                    return items

            match = re.search(self._config.item_format, line)
            if hasattr(match, "group") and len(match.groups()) >= 3:
                line = line.lower().replace("\n", "")
                system_log("Matched line with receipt option regexp: {line}".format(line=line))

                name, quantity, price = self.get_item_attrs(regexp_match=match)

                if len(name) > 3:
                    parse_stop = False
                    for word in self._config.ignore_keys:
                        parse_stop = fnmatch.fnmatch(name, f"*{word}*")
                        if parse_stop:
                            break
                    if not parse_stop:
                        item = self.set_item_attrs(name, quantity, price)
                        if item:
                            items.append(item)

        system_log("Finish image processing and sending items to bot")
        return items

    @staticmethod
    def get_item_attrs(regexp_match):
        name = regexp_match.group(1)
        quantity = regexp_match.group(2).replace(",", ".")
        price = regexp_match.group(3).replace(",", ".")
        return name, quantity, price

    def set_item_attrs(self, name, quantity, price):
        item = copy(self.item)
        try:
            quantity = float(quantity)
            price = float(price)
        except ValueError:
            return
        else:
            item[self.NAME] = name.lower()
            item[self.QUANTITY] = int(quantity / 100) if quantity >= 100 else int(quantity)
            item[self.PRICE] = price
        return item
