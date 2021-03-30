import re
import fnmatch
from copy import copy

import cv2
import numpy as np
import matplotlib.pyplot as plt
from PIL import Image
from imutils.perspective import four_point_transform

from bot_config import PARSER_CONFIG_PATH
from receipt_parser_core.enhancer import process_receipt
from receipt_parser_core.config import read_config


class ImageParser:
    NAME = "name"
    QUANTITY = "quantity"
    PRICE = "price"

    def __init__(self):
        self._config = read_config(PARSER_CONFIG_PATH)

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

    def parse_items(self, filename):
        items = []
        receipt = process_receipt(self._config, filename=filename)
        for line in receipt.lines:
            for stop_word in self._config.sum_keys:
                if fnmatch.fnmatch(line, f"*{stop_word}*"):
                    return items

            match = re.search(self._config.item_format, line)
            if hasattr(match, "group") and len(match.groups()) >= 3:
                item = copy(self.item)
                name = match.group(1)
                quantity = match.group(2)
                price = match.group(3)

                if len(name) > 3:
                    parse_stop = False
                    for word in self._config.ignore_keys:
                        parse_stop = fnmatch.fnmatch(name, f"*{word}*")
                        if parse_stop:
                            break
                    if not parse_stop:
                        item[self.NAME] = name
                        if float(quantity) >= 100:
                            quantity = int(float(quantity) / 100)
                        else:
                            quantity = int(float(quantity))
                        item[self.QUANTITY] = quantity
                        item[self.PRICE] = float(price) / quantity
                        items.append(item)
        return items
