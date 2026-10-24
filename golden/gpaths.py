"""Where a file of the node is stored inside golden/overlay/: the repository's .gitignore swallows directories named `scripts/` and `.venv*/`, so those two names are renamed in the store."""


def stored(path: str) -> str:
    return path.lstrip("/").replace("/.venv313/", "/venv313/").replace("/scripts/", "/bin-scripts/")
