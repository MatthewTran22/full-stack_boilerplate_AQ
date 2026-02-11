import os
import logging

logger = logging.getLogger(__name__)

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "..", "templates", "nextjs")


def get_template_files() -> list[dict]:
    """Read all template files from the nextjs template directory.

    Returns a list of dicts: [{"path": "relative/path", "content": "..."}]
    """
    files = []
    template_root = os.path.abspath(TEMPLATE_DIR)

    for dirpath, _, filenames in os.walk(template_root):
        for filename in filenames:
            full_path = os.path.join(dirpath, filename)
            rel_path = os.path.relpath(full_path, template_root)

            try:
                with open(full_path, "r", encoding="utf-8") as f:
                    content = f.read()
                files.append({"path": rel_path, "content": content})
            except Exception as e:
                logger.warning(f"Failed to read template file {rel_path}: {e}")

    logger.info(f"Loaded {len(files)} template files")
    return files
