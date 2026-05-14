#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="${1:-$(cd "$SCRIPT_DIR/../.." && pwd)}"

if [[ ! -d "$ROOT_DIR" ]]; then
    echo "[line-endings][ERROR] target directory not found: $ROOT_DIR" >&2
    exit 1
fi

cd "$ROOT_DIR"

declare -a files=()
if git rev-parse --show-toplevel >/dev/null 2>&1; then
    while IFS= read -r path; do
        [[ -n "$path" ]] && files+=("$path")
    done < <(git ls-files '*.sh' '*.bash')
else
    while IFS= read -r path; do
        [[ -n "$path" ]] && files+=("${path#./}")
    done < <(find . -type f \( -name '*.sh' -o -name '*.bash' \) | sort)
fi

declare -a bad=()
for path in "${files[@]}"; do
    [[ -f "$path" ]] || continue

    issues=()
    if head -c 3 "$path" | grep -q $'\xEF\xBB\xBF'; then
        issues+=("BOM")
    fi
    if LC_ALL=C grep -q $'\r' "$path"; then
        issues+=("CRLF")
    fi

    if [[ ${#issues[@]} -gt 0 ]]; then
        bad+=("$path: $(IFS=,; echo "${issues[*]}")")
    fi
done

if [[ ${#bad[@]} -gt 0 ]]; then
    echo "BAD shell line endings:"
    printf '%s\n' "${bad[@]}"
    exit 1
fi

echo "OK: no BOM/CRLF in .sh or .bash files"
