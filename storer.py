import logging
import os
import pathlib
import sqlite3
from dotenv import load_dotenv

global logger
logger = logging.getLogger(__name__)
home_dir = os.path.expanduser("~")
env_path = os.path.join(home_dir, "picture-store-anime.env")
load_dotenv(dotenv_path=env_path)

def prepair_dir(path: str):
    dir_path = pathlib.Path(path)
    if not dir_path.exists():
        dir_path.mkdir(parents=True, exist_ok=True)

def prepair_dirs():
    vault_path = os.getenv("PICTURE_STORE_ANIME_VAULT_PATH")
    thumbnail_path = os.getenv("PICTURE_STORE_ANIME_THUMBNAIL_PATH")
    prepair_dir(vault_path)
    prepair_dir(thumbnail_path)

def scan_images(store_from: str):
    image_extensions = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp"}
    store_from_path = pathlib.Path(store_from)

    # rglob("*") で再帰的にすべてのファイルを取得
    for path in store_from_path.rglob("*"):
        if path.is_file() and path.suffix.lower() in image_extensions:
            yield path

def process(conn: sqlite3.Connection):
    store_from = os.getenv("PICTURE_STORE_ANIME_STORE_FROM")
    vault_path = os.getenv("PICTURE_STORE_ANIME_VAULT_PATH")
    thumbnail_path = os.getenv("PICTURE_STORE_ANIME_THUMBNAIL_PATH")
    vault_store_mode = os.getenv("PICTURE_STORE_ANIME_VAULT_STORE_MODE")

    if os.path.isdir(store_from) is False:
        logger.error(f"STORE_FROM directory does not exist: {store_from}")
        return

    prepair_dirs()

    image_list = list(scan_images(store_from))
    total_count = len(image_list)

    for image_path in tqdm(image_list, desc="Processing images", total=total_count, unit="img"):
        # logger.info を出すと tqdm のバーが崩れることがあるため、
        # tqdm.write を使うか、ログレベルを調整するのがコツです
        tqdm.write(f"Processing: {image_path.name}")

def main():
    print("Hello from picture-store-anime-storer!")
    db_path = os.getenv("PICTURE_STORE_ANIME_DB_PATH")
    conn = sqlite3.connect(db_path)

    process(conn)

    conn.close()

if __name__ == "__main__":
    main()
