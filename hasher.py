import logging
import os
import pathlib
import sqlite3
import uuid
import shutil
import sys
import PIL.Image as Image
import PIL
from imgutils.tagging.pixai import get_pixai_tags
from dotenv import load_dotenv
from tqdm import tqdm
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

global logger
logger = logging.getLogger(__name__)load_dotenv()

home_dir = os.path.expanduser("~")
env_path = os.path.join(home_dir, "picture-store-anime.env")
load_dotenv(dotenv_path=env_path)
global retry_stop_attempt, waits_multiplier, waits_exponential_min, waits_exponential_max
retry_stop_attempt = int(os.getenv("STOP_AFTER_ATTEMPT", 5))
waits_multiplier = float(os.getenv("WAITS_MULTIPLIER", 1))
waits_exponential_min = int(os.getenv("WAITS_EXPONENTIAL_MIN", 2))
waits_exponential_max = int(os.getenv("WAITS_EXPONENTIAL_MAX", 10))

def main():
    pass
