"""
dump_to_txt.py

Standalone developer utility (not part of the game runtime). Walks the
project directory and concatenates every .py and .json file into a
single .txt file, with clear separators showing each file's relative
path. Useful for pasting the entire codebase into a chat/review tool
in one shot.

Usage:
    conda activate oraclefinanceai
    python dump_to_txt.py
    python dump_to_txt.py --output my_dump.txt
    python dump_to_txt.py --root . --extensions .py .json
"""
import os
import argparse
import datetime


DEFAULT_EXTENSIONS = (".py", ".json")

# Directories to always skip (VCS metadata, caches, envs, IDE folders).
EXCLUDED_DIRS = {
    ".git", "__pycache__", ".idea", ".vscode", "venv", ".venv",
    "env", "node_modules", ".pytest_cache", ".mypy_cache","helper","test"
}

# Never include the dump script's own output file(s) in the dump.
EXCLUDED_FILENAMES = {"savegame.json"}


def collect_files(root: str, extensions: tuple) -> list:
    collected = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]
        for filename in sorted(filenames):
            if filename in EXCLUDED_FILENAMES:
                continue
            if filename.endswith(extensions):
                full_path = os.path.join(dirpath, filename)
                rel_path = os.path.relpath(full_path, root)
                collected.append((rel_path, full_path))
    collected.sort(key=lambda pair: pair[0])
    return collected


def dump_files(root: str, output_path: str, extensions: tuple):
    files = collect_files(root, extensions)

    with open(output_path, "w", encoding="utf-8") as out:
        out.write("=" * 80 + "\n")
        out.write("PROJECT SOURCE DUMP\n")
        out.write(f"Generated: {datetime.datetime.now().isoformat()}\n")
        out.write(f"Root: {os.path.abspath(root)}\n")
        out.write(f"Extensions included: {', '.join(extensions)}\n")
        out.write(f"Total files: {len(files)}\n")
        out.write("=" * 80 + "\n\n")

        out.write("FILE INDEX\n")
        out.write("-" * 80 + "\n")
        for rel_path, _ in files:
            out.write(f"  {rel_path}\n")
        out.write("-" * 80 + "\n\n")

        for rel_path, full_path in files:
            out.write("\n" + "#" * 80 + "\n")
            out.write(f"# FILE: {rel_path}\n")
            out.write("#" * 80 + "\n\n")
            try:
                with open(full_path, "r", encoding="utf-8") as f:
                    content = f.read()
                out.write(content)
                if not content.endswith("\n"):
                    out.write("\n")
            except Exception as e:
                out.write(f"[ERROR READING FILE: {e}]\n")

    print(f"Dumped {len(files)} files into: {os.path.abspath(output_path)}")


def main():
    parser = argparse.ArgumentParser(description="Dump all project .py/.json files into one .txt file.")
    parser.add_argument("--root", default=".", help="Root directory to walk (default: current directory)")
    parser.add_argument("--output", default="project_dump.txt", help="Output .txt file path")
    parser.add_argument("--extensions", nargs="+", default=list(DEFAULT_EXTENSIONS),
                        help="File extensions to include (default: .py .json)")
    args = parser.parse_args()

    extensions = tuple(e if e.startswith(".") else f".{e}" for e in args.extensions)
    dump_files(args.root, args.output, extensions)


if __name__ == "__main__":
    main()