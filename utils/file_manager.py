import shutil
from pathlib import Path
from utils.logger import logger

def read_file(file_path: str | Path) -> str:
    """Reads and returns the contents of a file as a string."""
    path = Path(file_path)
    try:
        logger.debug(f"Reading file: {path}")
        return path.read_text(encoding="utf-8")
    except Exception as e:
        logger.error(f"Failed to read file {path}: {e}")
        raise

def write_file(file_path: str | Path, content: str) -> None:
    """Writes content to a file, creating parent directories if necessary."""
    path = Path(file_path)
    try:
        logger.debug(f"Writing file: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except Exception as e:
        logger.error(f"Failed to write file {path}: {e}")
        raise

def copy_idf(src_path: str | Path, dest_path: str | Path) -> None:
    """Copies an IDF file from source to destination path."""
    src = Path(src_path)
    dest = Path(dest_path)
    try:
        logger.debug(f"Copying IDF file from {src} to {dest}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
    except Exception as e:
        logger.error(f"Failed to copy IDF file from {src} to {dest}: {e}")
        raise

def create_directory(dir_path: str | Path) -> None:
    """Creates a directory and any parent directories if they do not exist."""
    path = Path(dir_path)
    try:
        logger.debug(f"Creating directory: {path}")
        path.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.error(f"Failed to create directory {path}: {e}")
        raise

def clean_output_folders(dir_path: str | Path, keep_logs: bool = True) -> None:
    """
    Cleans up files in the given directory path.
    Optionally preserves the logs subdirectory.
    Gracefully skips items that cannot be deleted due to Windows file locks.
    """
    path = Path(dir_path)
    if not path.exists():
        logger.warning(f"Directory {path} does not exist. Skipping cleaning.")
        return

    logger.info(f"Cleaning directory: {path}")
    for item in path.iterdir():
        try:
            if item.is_dir():
                if keep_logs and item.name == "logs":
                    logger.debug("Preserving logs directory")
                    continue
                shutil.rmtree(item, ignore_errors=True)
                logger.debug(f"Attempted rmtree deletion on directory: {item}")
            else:
                item.unlink()
                logger.debug(f"Deleted file: {item}")
        except Exception as e:
            logger.warning(f"Could not delete {item} (possibly locked by another process): {e}")
    logger.info(f"Cleaning process for {path} completed.")
