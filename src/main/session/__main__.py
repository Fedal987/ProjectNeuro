"""
    ProjectNeuro
    author@Fedal987
    Powered by HeronStudio
    GitHub: https://github.com/Fedal987/ProjectNeuro
"""

import argparse

from .local_store import LocalSessionStore


def main():
    parser = argparse.ArgumentParser(
        description="Offline session index maintenance."
    )
    parser.add_argument("command", choices=["rebuild-index"])
    parser.add_argument("--directory", default="session", help="Session storage directory")
    args = parser.parse_args()
    errors = LocalSessionStore.rebuild_from_disk(args.directory)
    for error in errors:
        print(error)
    if errors:
        raise SystemExit(1)
    print("Session index rebuilt from JSONL.")


if __name__ == "__main__":
    main()
