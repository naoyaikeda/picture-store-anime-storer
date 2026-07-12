import logging
import os
import pathlib
import sqlite3
import uuid
import shutil
import sys
import json
import hashlib
from argparse import ArgumentParser
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
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
home_dir = os.path.expanduser("~")
env_path = os.path.join(home_dir, "picture-store-anime.env")
load_dotenv(dotenv_path=env_path)
global retry_stop_attempt, waits_multiplier, waits_exponential_min, waits_exponential_max
retry_stop_attempt = int(os.getenv("STOP_AFTER_ATTEMPT", 5))
waits_multiplier = float(os.getenv("WAITS_MULTIPLIER", 1))
waits_exponential_min = int(os.getenv("WAITS_EXPONENTIAL_MIN", 2))
waits_exponential_max = int(os.getenv("WAITS_EXPONENTIAL_MAX", 10))

@retry(
    retry=retry_if_exception_type(OSError),
    stop=stop_after_attempt(int(retry_stop_attempt)),
    wait=wait_exponential(multiplier=waits_multiplier, min=waits_exponential_min, max=waits_exponential_max),
    reraise=True # 最終的にダメなら例外を投げる
)
def get_file_hash(filepath):
    hasher = hashlib.sha256() # 使用するハッシュアルゴリズムを指定
    with open(filepath, 'rb') as f:
        while chunk := f.read(8192): # ファイルを8KBずつ読み込む
            hasher.update(chunk)

    return hasher.hexdigest() # ハッシュ値を16進数で返す

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
        if safe_stat(path) and path.suffix.lower() in image_extensions:
            yield path

def generate_thumbnail(image_path: pathlib.Path, thumbnail_path: pathlib.Path, size=(128, 128)):
    logger.debug(f"Generating thumbnail for {image_path} at {thumbnail_path}")

    if thumbnail_path.parent.exists():
        # ここに「サムネイルがまだ存在しない場合のみ実行する」条件を入れるのが効率的です
        if not thumbnail_path.exists():
            img = safe_thumb(image_path, size)
            img.save(thumbnail_path)
    else:
        # 親ディレクトリがない場合は作成してから保存（保険的ロジック）
        thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
        img = safe_thumb(image_path, size)
        img.save(thumbnail_path)

def copy_to_vault(image_path: pathlib.Path, vault_path: pathlib.Path):

    logger.debug(f"Examining vault path: {vault_path.parent}")
    if vault_path.parent.exists():
        logger.debug(f"Vault path exists: {vault_path.parent}")
    else:
        logger.debug(f"Vault path does not exist, will create: {vault_path.parent}")
    logger.debug(f"Copying image to vault: {vault_path}")

    if vault_path.parent.exists():
        if not vault_path.exists():
            safe_copy2(image_path, vault_path)

def apply_tag_threshold(tags_list: list, threshold: float):
    # 各要素は {"name": "...", "confidence": ...} の辞書
    return [tag["name"] for tag in tags_list if tag.get("confidence", 0) >= threshold]

@retry(
    retry=retry_if_exception_type(OSError),
    stop=stop_after_attempt(int(retry_stop_attempt)),
    wait=wait_exponential(multiplier=waits_multiplier, min=waits_exponential_min, max=waits_exponential_max),
    reraise=True # 最終的にダメなら例外を投げる
)
def safe_get_pixai_tags(image_path: pathlib.Path):
    # ライブラリ内部の Image.open で起きる OSError をこれでキャッチしてリトライする
    return get_pixai_tags(image_path)
def process_image(image_path: pathlib.Path, conn: sqlite3.Connection):
    file_name = image_path.name

    # --- 衝突チェック & 既存 ID 取得 ---

    cursor = conn.execute("SELECT id, vault_path, thumbnail_path FROM images WHERE file_name = ?", (file_name,))
    results = cursor.fetchall()
    is_already = False

    # 汎用的な重複排除（ファイル名に関わらず中身で判定）にする場合
    file_hash = get_file_hash(image_path)
    c2 = conn.execute("SELECT image_id FROM imagehashes WHERE phash = ?", (file_hash,))
    r2 = c2.fetchone()

    if r2:
        is_already = True

    if is_already:
        # 重複が判明した ID を使って images テーブルからパス情報を取得する
        image_id = r2[0]
        c3 = conn.execute("SELECT vault_path, thumbnail_path, file_name FROM images WHERE id = ?", (image_id,))
        r3 = c3.fetchone()
        registered_name = r3[2]
        tqdm.write(f"Skipped DB insert (already exists): {registered_name}")

        # ファイルの実体操作
        vault_name = r3[0]
        thumbnail_name = r3[1]

        generate_thumbnail(image_path, pathlib.Path(os.path.join(os.getenv("PICTURE_STORE_ANIME_THUMBNAIL_PATH"), thumbnail_name)))
        copy_to_vault(image_path, pathlib.Path(os.path.join(os.getenv("PICTURE_STORE_ANIME_VAULT_PATH"), vault_name)))

        return
    else:
        # 新規登録の場合のみ PixAI の重い処理を走らせる
        try:
            result = safe_get_pixai_tags(image_path) # 先ほどのリトライ付き関数
        except Exception as e:
            logger.error(f"Failed to get tags for {file_name}: {e}")
            return

        general_tags, character_tags = result
        file_uuid = uuid.uuid4().hex
        file_extension = image_path.suffix.lower()
        vault_name = f"{file_uuid}{file_extension}"
        thumbnail_name = f"{file_uuid}_thumb{file_extension}"

        # 画像情報の挿入
        cursor = conn.execute("""
            INSERT INTO images (uuid, file_name, vault_path, thumbnail_path)
            VALUES (?, ?, ?, ?)
        """, (file_uuid, file_name, vault_name, thumbnail_name))
        image_id = cursor.lastrowid # last_insert_rowid() より直感的

        cursor = conn
        cursor = conn.execute("""
            INSERT INTO imagehashes (image_id, phash)
            VALUES (?, ?)
        """, (image_id, file_hash))

    # --- タグ処理 (新規の場合のみここに到達) ---
    all_tags_list = []
    for name, confidence in general_tags.items():
        all_tags_list.append({"name": name, "confidence": confidence})
    for name, confidence in character_tags.items():
        all_tags_list.append({"name": name, "confidence": confidence})

    filtered_tags = apply_tag_threshold(all_tags_list, float(os.getenv("TAGGER_THRESHOLD", "0.5")))

    for tag in filtered_tags:
        # タグ自体の存在確認と登録
        cursor = conn.execute("SELECT id FROM tags WHERE tag = ?", (tag,))
        tag_row = cursor.fetchone()
        if tag_row:
            tag_id = tag_row[0]
        else:
            conn.execute("INSERT INTO tags (tag) VALUES (?)", (tag,))
            tag_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # 中間テーブルへの登録
        conn.execute("""
            INSERT OR IGNORE INTO image_tags (image_id, tag_id)
            VALUES (?, ?)
        """, (image_id, tag_id))

    conn.commit()

    # ファイルの実体操作
    generate_thumbnail(image_path, pathlib.Path(os.path.join(os.getenv("PICTURE_STORE_ANIME_THUMBNAIL_PATH"), thumbnail_name)))
    copy_to_vault(image_path, pathlib.Path(os.path.join(os.getenv("PICTURE_STORE_ANIME_VAULT_PATH"), vault_name)))

    return

def is_network_timeout(exception):
    return isinstance(exception, OSError) and getattr(exception, 'winerror', None) == 121

def safe_is_file(path: pathlib.Path):
    """WinError 121対策のリトライ付きファイルチェック"""
    return path.is_file()

@retry(
    retry=retry_if_exception_type(OSError),
    stop=stop_after_attempt(int(retry_stop_attempt)),
    wait=wait_exponential(multiplier=waits_multiplier, min=waits_exponential_min, max=waits_exponential_max),
    # ネットワークタイムアウト(121)の場合のみリトライ
    retry_error_callback=lambda retry_state: False
)
def safe_stat(path: pathlib.Path):
    """リトライ付きで path.is_file() を判定するためのラッパー"""
    # winerror 121 を確実に拾うため、明示的に例外をフィルタリングする場合は
    # retry 引数に custom predicate を渡すことも可能です
    return path.is_file()

@retry(
    retry=retry_if_exception_type(OSError),
    stop=stop_after_attempt(int(retry_stop_attempt)),
    wait=wait_exponential(multiplier=waits_multiplier, min=waits_exponential_min, max=waits_exponential_max),
    reraise=True
)
def safe_thumb(image_path: pathlib.Path, size=(128, 128)):
    """リトライ付きサムネイル生成"""
def safe_thumb(image_path: pathlib.Path, size=(128, 128)):
    """リトライ付きサムネイル生成（色空間の変換対応）"""
    with PIL.Image.open(image_path) as img:
        # RGBAなどの透過チャンネルがある場合、白背景と合成するか、単に変換する
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            # まずRGBAに変換することで、Pモードの透明度情報もアルファチャンネルに変換される
            img = img.convert("RGBA")
            # 背景を白（255, 255, 255）にした新規画像を作成
            background = PIL.Image.new("RGB", img.size, (255, 255, 255))
            # アルファチャンネルをマスクとして貼り付け
            background.paste(img, mask=img.split()[-1])
            img = background
        else:
            # それ以外のモード（PやCMYKなど）も一律RGBに変換
            img = img.convert("RGB")

        img.thumbnail(size)
        # thumbnail()は破壊的メソッドですが、中身が入れ替わったimgを返すために
        # 一度別の変数で保持するか、コピーを返すようにします
        return img.copy()

@retry(
    retry=retry_if_exception_type(OSError),
    stop=stop_after_attempt(int(retry_stop_attempt)),
    wait=wait_exponential(multiplier=waits_multiplier, min=waits_exponential_min, max=waits_exponential_max),
    # ネットワークタイムアウト(121)の場合のみリトライ
    retry_error_callback=lambda retry_state: False
)
def safe_copy2(src: pathlib.Path, dst: pathlib.Path):
    """WinError 121対策のリトライ付きファイルコピー"""
    shutil.copy2(src, dst)

def process(conn: sqlite3.Connection, sources: dict, source: str = "pixiv"):
    store_from = sources[source]
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
        process_image(image_path, conn)

def prepair_tables(conn: sqlite3.Connection):
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS images (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uuid VARCHAR(255) NOT NULL UNIQUE,  -- ( ) を外す
        file_name TEXT NOT NULL,
        vault_path TEXT NOT NULL,
        thumbnail_path TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS imagehashes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        image_id INTEGER NOT NULL UNIQUE,
        phash VARCHAR(255) NOT NULL UNIQUE,
        FOREIGN KEY (image_id) REFERENCES images(id)
    )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tags (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tag VARCHAR(255) NOT NULL UNIQUE          -- ( ) を外す
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS image_tags (
        image_id INTEGER,
        tag_id INTEGER,
        FOREIGN KEY (image_id) REFERENCES images(id),
        FOREIGN KEY (tag_id) REFERENCES tags(id),
        PRIMARY KEY (image_id, tag_id)
    )
    """)

    conn.commit()
    cursor.close()

def main():
    logger.info("Starting storer.py")
    parser = ArgumentParser(description="Store images into the picture store anime database.")
    parser.add_argument("source", help="Source directory to store images from")
    parser.add_argument("catalog", help="Catalog name to use for database connection")

    args = parser.parse_args()
    home_dir = os.path.expanduser("~")
    catalogs_path = os.path.join(home_dir, "picture-store-anime-catalogs.json")
    sources_path = os.path.join(home_dir, "picture-store-anime-sources.json")

    catalogs = {}
    with open(catalogs_path, "r", encoding="utf-8") as f:
        catalogs = json.load(f)

    sources = {}
    with open(sources_path, "r", encoding="utf-8") as f:
        sources = json.load(f)

    if args.catalog not in catalogs:
        logger.error(f"Catalog '{args.catalog}' not found in catalogs.")
        sys.exit(1)

    if args.source not in sources:
        logger.error(f"Source '{args.source}' not found in sources.")
        sys.exit(1)

    db_path = catalogs[args.catalog]["catalog_path"]
    conn = sqlite3.connect(db_path)
    prepair_tables(conn)

    process(conn, sources, args.source)

    conn.close()

if __name__ == "__main__":
    main()
