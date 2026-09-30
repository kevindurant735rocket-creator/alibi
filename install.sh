#!/usr/bin/env bash
# Install alibi. It is one Python package with no dependencies, so there is
# nothing to build and nothing to download but this file.
#
#   curl -fsSL https://raw.githubusercontent.com/kevindurant735rocket-creator/alibi/main/install.sh | sh
#
# What it does: clones the repo into ~/.local/share/alibi and drops an `alibi`
# shim on your PATH. It touches nothing else.
set -euo pipefail

PREFIX="${ALIBI_PREFIX:-$HOME/.local}"
REPO_DIR="$PREFIX/share/alibi"
REPO_URL="https://github.com/kevindurant735rocket-creator/alibi.git"
BRANCH="${ALIBI_BRANCH:-main}"

if ! command -v python3 >/dev/null 2>&1; then
  echo "alibi: python3 not found. It needs Python 3.9+ and git, nothing else." >&2
  exit 1
fi

version=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
major=${version%%.*}
minor=${version##*.}
if [ "$major" -lt 3 ] || { [ "$major" -eq 3 ] && [ "$minor" -lt 9 ]; }; then
  echo "alibi: Python 3.9 or newer is required; this is $version." >&2
  exit 1
fi

if ! command -v git >/dev/null 2>&1; then
  echo "alibi: git not found, and git is the only dependency." >&2
  exit 1
fi

mkdir -p "$PREFIX/share" "$PREFIX/bin"

if [ -d "$REPO_DIR/.git" ]; then
  echo "alibi: updating $REPO_DIR"
  git -C "$REPO_DIR" fetch --quiet origin "$BRANCH"
  git -C "$REPO_DIR" checkout --quiet "$BRANCH"
  git -C "$REPO_DIR" pull --quiet --ff-only
else
  echo "alibi: installing into $REPO_DIR"
  git clone --quiet --depth 1 --branch "$BRANCH" "$REPO_URL" "$REPO_DIR"
fi

cat > "$PREFIX/bin/alibi" <<SHIM
#!/usr/bin/env bash
exec python3 -c 'import sys; sys.path.insert(0, "$REPO_DIR"); from alibi.cli import main; raise SystemExit(main())' "\$@"
SHIM
chmod +x "$PREFIX/bin/alibi"

case ":$PATH:" in
  *":$PREFIX/bin:"*) ;;
  *) echo "alibi: add this to your shell profile:"; echo "    export PATH=\"$PREFIX/bin:\$PATH\"" ;;
esac

echo
"$PREFIX/bin/alibi" --version
echo "alibi: run 'alibi doctor' to see which agents it can read."
