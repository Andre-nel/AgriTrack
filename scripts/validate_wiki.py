import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICE_PATH = REPO_ROOT / "app" / "services" / "wiki_service.py"
SPEC = importlib.util.spec_from_file_location("wiki_service", SERVICE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Could not load wiki service from {SERVICE_PATH}")
wiki_service = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(wiki_service)
WikiService = wiki_service.WikiService


def main() -> int:
    pages = WikiService.load_pages()
    errors = WikiService.validation_errors(pages)
    if errors:
        for error in errors:
            print(error)
        return 1
    print(f"Validated {len(pages)} wiki page(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
