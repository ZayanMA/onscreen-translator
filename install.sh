#!/usr/bin/env bash
# install.sh — onscreen-translator installer
# Installs system deps, Python packages, Argos language models, and config.
set -e

# ── Colour helpers ────────────────────────────────────────────────────────────
if command -v tput &>/dev/null && tput setaf 1 &>/dev/null; then
    GREEN=$(tput setaf 2)
    YELLOW=$(tput setaf 3)
    RED=$(tput setaf 1)
    CYAN=$(tput setaf 6)
    BOLD=$(tput bold)
    RESET=$(tput sgr0)
else
    GREEN="" YELLOW="" RED="" CYAN="" BOLD="" RESET=""
fi

info()    { printf "${GREEN}[✓]${RESET} %s\n" "$*"; }
warn()    { printf "${YELLOW}[!]${RESET} %s\n" "$*"; }
error()   { printf "${RED}[✗]${RESET} %s\n" "$*" >&2; }
heading() { printf "\n${BOLD}${CYAN}▶ %s${RESET}\n" "$*"; }
die()     { error "$*"; exit 1; }

# ── Sanity checks ─────────────────────────────────────────────────────────────
heading "Checking prerequisites"

for cmd in python3 pip3 apt-get; do
    if ! command -v "$cmd" &>/dev/null; then
        die "Required command not found: $cmd"
    fi
done

PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PYTHON_MAJOR=$(python3 -c "import sys; print(sys.version_info.major)")
PYTHON_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)")

if [[ "$PYTHON_MAJOR" -lt 3 || ( "$PYTHON_MAJOR" -eq 3 && "$PYTHON_MINOR" -lt 11 ) ]]; then
    die "Python 3.11+ required (found $PYTHON_VERSION)"
fi
info "Python $PYTHON_VERSION — OK"

# ── System packages ───────────────────────────────────────────────────────────
heading "Installing system packages (requires sudo)"

SYSTEM_PKGS=(
    gir1.2-gtk4layershell-1.0
    libgtk4-layer-shell0
    python3-dbus
    python3-gi
    python3-gi-cairo
)

warn "Running: sudo apt-get install -y ${SYSTEM_PKGS[*]}"
if ! sudo apt-get install -y "${SYSTEM_PKGS[@]}"; then
    die "apt-get install failed. Are you on a Debian/Ubuntu-based system?"
fi
info "System packages installed"

# ── Virtual environment ────────────────────────────────────────────────────────
heading "Setting up Python virtual environment"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

# --system-site-packages lets the venv see system python3-dbus and python3-gi
if [[ ! -d "$VENV_DIR" ]]; then
    python3 -m venv --system-site-packages "$VENV_DIR"
    info "Created venv at $VENV_DIR"
else
    info "Venv already exists at $VENV_DIR"
fi

VENV_PYTHON="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"

# ── Python packages ───────────────────────────────────────────────────────────
heading "Installing Python packages into venv"

PIP_PKGS=(
    "paddleocr>=2.7"
    "paddlepaddle>=2.6"
    "argostranslate>=1.9"
    "langdetect>=1.0"
)

warn "This may take a few minutes (PaddlePaddle is large)…"
# setuptools must be installed first — langdetect's setup.py uses distutils
# which was removed in Python 3.12+ but setuptools provides a compatibility shim.
if ! "$VENV_PIP" install --upgrade setuptools; then
    die "Failed to install setuptools."
fi
if ! "$VENV_PIP" install "${PIP_PKGS[@]}"; then
    die "pip install into venv failed."
fi
info "Python packages installed"

# ── Argos Translate language pairs ───────────────────────────────────────────
heading "Argos Translate language model selection"

# Available pairs: (from_code, to_code, display_name)
declare -a LANG_CODES_FROM
declare -a LANG_CODES_TO
declare -a LANG_NAMES

LANG_CODES_FROM=( ja   zh   ko   es   fr   de   it   pt   ru   ar  )
LANG_CODES_TO=(   en   en   en   en   en   en   en   en   en   en  )
LANG_NAMES=(
    "Japanese → English"
    "Chinese (Simplified) → English"
    "Korean → English"
    "Spanish → English"
    "French → English"
    "German → English"
    "Italian → English"
    "Portuguese → English"
    "Russian → English"
    "Arabic → English"
)

echo ""
echo "Select the language pairs you want to install."
echo "Enter the numbers separated by spaces, or type ${BOLD}all${RESET} for all of them."
echo ""

for i in "${!LANG_NAMES[@]}"; do
    printf "  ${BOLD}%2d)${RESET} %s\n" $((i + 1)) "${LANG_NAMES[$i]}"
done

echo ""
read -rp "Your choice: " USER_CHOICE

declare -a SELECTED_FROM
declare -a SELECTED_TO
declare -a SELECTED_NAMES

if [[ "$USER_CHOICE" == "all" || "$USER_CHOICE" == "ALL" ]]; then
    SELECTED_FROM=( "${LANG_CODES_FROM[@]}" )
    SELECTED_TO=(   "${LANG_CODES_TO[@]}"   )
    SELECTED_NAMES=( "${LANG_NAMES[@]}" )
else
    for token in $USER_CHOICE; do
        if ! [[ "$token" =~ ^[0-9]+$ ]]; then
            warn "Ignoring non-numeric token: $token"
            continue
        fi
        idx=$((token - 1))
        if [[ "$idx" -lt 0 || "$idx" -ge "${#LANG_NAMES[@]}" ]]; then
            warn "Number out of range, ignoring: $token"
            continue
        fi
        SELECTED_FROM+=( "${LANG_CODES_FROM[$idx]}" )
        SELECTED_TO+=(   "${LANG_CODES_TO[$idx]}"   )
        SELECTED_NAMES+=( "${LANG_NAMES[$idx]}" )
    done
fi

if [[ "${#SELECTED_FROM[@]}" -eq 0 ]]; then
    warn "No language pairs selected — skipping model download."
    warn "You can run install.sh again later to add language pairs."
else
    echo ""
    info "Will download and install:"
    for name in "${SELECTED_NAMES[@]}"; do
        printf "    • %s\n" "$name"
    done
    echo ""

    # Build Python lists for the inline script
    FROM_LIST=$(printf "'%s'," "${SELECTED_FROM[@]}")
    FROM_LIST="[${FROM_LIST%,}]"
    TO_LIST=$(printf "'%s'," "${SELECTED_TO[@]}")
    TO_LIST="[${TO_LIST%,}]"

    "$VENV_PYTHON" - <<PYEOF
import sys

from_codes = ${FROM_LIST}
to_codes   = ${TO_LIST}

try:
    import argostranslate.package as pkg
except ImportError:
    print("ERROR: argostranslate not importable — is it installed?", file=sys.stderr)
    sys.exit(1)

print("Updating Argos Translate package index…")
try:
    pkg.update_package_index()
except Exception as e:
    print(f"WARNING: Could not update index ({e}). Trying with cached index.", file=sys.stderr)

available = pkg.get_available_packages()

pairs = list(zip(from_codes, to_codes))
total = len(pairs)

for i, (fc, tc) in enumerate(pairs, 1):
    print(f"[{i}/{total}] Looking for {fc} → {tc} …")
    candidates = [p for p in available if p.from_code == fc and p.to_code == tc]
    if not candidates:
        print(f"  WARNING: No package found for {fc} → {tc}. Skipping.")
        continue
    package = candidates[0]
    print(f"  Downloading {package.from_name} → {package.to_name} …")
    try:
        dl_path = package.download()
        pkg.install_from_path(dl_path)
        print(f"  Installed {fc} → {tc}")
    except Exception as e:
        print(f"  ERROR installing {fc} → {tc}: {e}", file=sys.stderr)

print("Language model installation complete.")
PYEOF

    info "Argos Translate language models installed"
fi

# ── Default config ────────────────────────────────────────────────────────────
heading "Creating default configuration"

CONFIG_DIR="$HOME/.config/onscreen-translator"
CONFIG_FILE="$CONFIG_DIR/config.toml"

mkdir -p "$CONFIG_DIR"

if [[ -f "$CONFIG_FILE" ]]; then
    warn "Config already exists at $CONFIG_FILE — not overwriting."
else
    cat > "$CONFIG_FILE" <<'TOML'
[translation]
# BCP-47 language code for the output language.
# Must match an installed Argos Translate model.
target_language = "en"

[hotkey]
# Hotkey to trigger the region picker.
# Format: Modifier+Key  (e.g. "Super+t", "Ctrl+Alt+t")
preferred_trigger = "Super+t"

[overlay]
# Seconds before the translation card auto-dismisses (0 = never).
auto_dismiss_seconds = 12
# Show the original OCR text above the translation.
show_original = true
# Font sizes in pixels.
font_size_translated = 15
font_size_original = 11
TOML
    info "Config written to $CONFIG_FILE"
fi

# ── Install the package itself into the venv ──────────────────────────────────
heading "Installing onscreen-translator into venv"

if "$VENV_PIP" install -e "$SCRIPT_DIR"; then
    info "onscreen-translator installed (editable mode)"
else
    warn "Editable install failed — you can still run it with:"
    warn "  $VENV_PYTHON -m onscreen_translator.main"
fi

# ── Create a launcher wrapper script ─────────────────────────────────────────
LAUNCHER="$SCRIPT_DIR/onscreen-translator"
cat > "$LAUNCHER" <<LAUNCHER_EOF
#!/usr/bin/env bash
exec "$VENV_DIR/bin/python" -m onscreen_translator.main "\$@"
LAUNCHER_EOF
chmod +x "$LAUNCHER"
info "Launcher created at $LAUNCHER"

# ── Autostart (optional) ──────────────────────────────────────────────────────
heading "Autostart (optional)"

AUTOSTART_DIR="$HOME/.config/autostart"
AUTOSTART_FILE="$AUTOSTART_DIR/onscreen-translator.desktop"
DESKTOP_SRC="$SCRIPT_DIR/data/onscreen-translator.desktop"

echo ""
read -rp "Add onscreen-translator to autostart on login? [y/N] " ADD_AUTOSTART

if [[ "$ADD_AUTOSTART" =~ ^[Yy]$ ]]; then
    mkdir -p "$AUTOSTART_DIR"
    cp "$DESKTOP_SRC" "$AUTOSTART_FILE"
    sed -i "s|^Exec=.*|Exec=$LAUNCHER|" "$AUTOSTART_FILE"
    info "Autostart entry created at $AUTOSTART_FILE"
else
    info "Skipped autostart — run manually with: $LAUNCHER"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
printf "${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}\n"
printf "${BOLD}${GREEN}  onscreen-translator is ready!${RESET}\n"
printf "${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}\n"
echo ""
printf "  ${BOLD}Start:${RESET}   %s\n" "$LAUNCHER"
printf "  ${BOLD}Hotkey:${RESET}  Super+T  (configurable in %s)\n" "$CONFIG_FILE"
printf "  ${BOLD}Config:${RESET}  %s\n" "$CONFIG_FILE"
echo ""
printf "  On first launch PaddleOCR will download its models (~450 MB).\n"
printf "  Subsequent launches will be instant.\n"
echo ""
