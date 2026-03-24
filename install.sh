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
    python3-gst-1.0
    gstreamer1.0-pipewire
    gstreamer1.0-plugins-good
    gstreamer1.0-plugins-base
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
    "easyocr>=1.7"
    "deepl>=1.17"
)

warn "This may take a few minutes (PaddlePaddle is large)…"
if ! "$VENV_PIP" install --upgrade setuptools; then
    die "Failed to install setuptools."
fi
if ! "$VENV_PIP" install "${PIP_PKGS[@]}"; then
    die "pip install into venv failed."
fi
info "Python packages installed"

# ── DeepL API key ─────────────────────────────────────────────────────────────
heading "DeepL Translation API setup"

echo ""
printf "  DeepL provides ${BOLD}500,000 free characters/month${RESET} via its free API tier.\n"
printf "  Get a free API key at: ${BOLD}https://www.deepl.com/pro-api${RESET}\n"
echo ""
read -rp "  Enter your DeepL API key (or press Enter to configure later): " DEEPL_KEY
echo ""

if [[ -n "$DEEPL_KEY" ]]; then
    info "DeepL API key received — will be written to config after config file is created"
else
    warn "No API key entered — edit $CONFIG_FILE after install to add it"
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
[hotkey]
# Hotkey trigger command shortcut label.
preferred_trigger = "Super+t"

[overlay]
# Seconds before the translation card auto-dismisses (0 = never).
auto_dismiss_seconds = 12
# Show the original OCR text above the translation.
show_original = false
# Font sizes in pixels.
font_size_translated = 15
font_size_original = 11

[ocr]
# Language for text detection and recognition.
# Japanese (manga, games, anime): "japan"  Others: "ch", "korean", "en"
language = "japan"

[deepl]
# Free API key from https://www.deepl.com/pro-api (500,000 chars/month free)
api_key = ""
# Target language code. Examples: EN-US, EN-GB, DE, FR, ES, ZH
target_language = "EN-US"
TOML
    info "Config written to $CONFIG_FILE"
fi

# Write DeepL API key into config if provided
if [[ -n "$DEEPL_KEY" ]]; then
    sed -i "s/^api_key = \"\"/api_key = \"${DEEPL_KEY}\"/" "$CONFIG_FILE"
    info "DeepL API key saved to $CONFIG_FILE"
fi

# ── Install the package itself into the venv ──────────────────────────────────
heading "Installing onscreen-translator into venv"

if "$VENV_PIP" install -e "$SCRIPT_DIR"; then
    info "onscreen-translator installed (editable mode)"
else
    warn "Editable install failed — you can still run it with:"
    warn "  $VENV_PYTHON -m onscreen_translator.main"
fi

# ── Create launcher scripts ───────────────────────────────────────────────────
LAUNCHER="$SCRIPT_DIR/onscreen-translator"
cat > "$LAUNCHER" <<LAUNCHER_EOF
#!/usr/bin/env bash
SCRIPT_DIR="\$(cd "\$(dirname "\${BASH_SOURCE[0]}")" && pwd)"
export GI_TYPELIB_PATH="/usr/lib/x86_64-linux-gnu/girepository-1.0\${GI_TYPELIB_PATH:+:\$GI_TYPELIB_PATH}"
export GDK_BACKEND=wayland
exec "\$SCRIPT_DIR/.venv/bin/python" -m onscreen_translator.main "\$@"
LAUNCHER_EOF
chmod +x "$LAUNCHER"
info "Launcher created at $LAUNCHER"

# ── Create trigger script ─────────────────────────────────────────────────────
LAUNCHER_TRIGGER="$SCRIPT_DIR/onscreen-translator-trigger"
cat > "$LAUNCHER_TRIGGER" <<'TRIGGER_EOF'
#!/usr/bin/env bash
python3 -c "
import socket, sys
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
try:
    s.connect('/tmp/onscreen-translator.sock')
    s.close()
except Exception as e:
    print(f'onscreen-translator is not running: {e}', file=sys.stderr)
    sys.exit(1)
"
TRIGGER_EOF
chmod +x "$LAUNCHER_TRIGGER"
info "Trigger script created at $LAUNCHER_TRIGGER"

# ── Install desktop entry ─────────────────────────────────────────────────────
heading "Installing desktop entry"

APPS_DIR="$HOME/.local/share/applications"
DESKTOP_SRC="$SCRIPT_DIR/data/onscreen-translator.desktop"
mkdir -p "$APPS_DIR"
DESKTOP_DEST="$APPS_DIR/dev.zayan.onscreen-translator.desktop"
cp "$DESKTOP_SRC" "$DESKTOP_DEST"
sed -i "s|^Exec=.*|Exec=$LAUNCHER|" "$DESKTOP_DEST"
update-desktop-database "$APPS_DIR" 2>/dev/null || true
info "Desktop entry installed at $DESKTOP_DEST"

# ── Keyboard shortcut setup ───────────────────────────────────────────────────
heading "Keyboard shortcut setup"

echo ""
printf "  onscreen-translator is triggered by a command — you need to add it as\n"
printf "  a custom keyboard shortcut in GNOME Settings.\n"
echo ""
printf "  ${BOLD}Steps:${RESET}\n"
printf "  1. Open  Settings → Keyboard → View and Customize Shortcuts\n"
printf "  2. Scroll to the bottom → ${BOLD}Custom Shortcuts${RESET} → click  ${BOLD}[+]${RESET}\n"
printf "  3. ${BOLD}Name:${RESET}     Onscreen Translator\n"
printf "  4. ${BOLD}Command:${RESET}  %s\n" "$LAUNCHER_TRIGGER"
printf "  5. ${BOLD}Shortcut:${RESET} press  Super+T  then click  ${BOLD}Set${RESET}\n"
echo ""

read -rp "  Open GNOME keyboard settings now? [y/N] " OPEN_KBD
if [[ "$OPEN_KBD" =~ ^[Yy]$ ]]; then
    gnome-control-center keyboard &
    echo ""
    info "Opened GNOME keyboard settings — follow the steps above, then come back here."
fi

echo ""
while true; do
    read -rp "  Have you added the keyboard shortcut? [y/N] " KBD_DONE
    if [[ "$KBD_DONE" =~ ^[Yy]$ ]]; then
        info "Keyboard shortcut confirmed"
        break
    fi
    echo ""
    printf "  ${YELLOW}Not yet? That's fine — here are the steps again:${RESET}\n"
    printf "  1. Settings → Keyboard → View and Customize Shortcuts\n"
    printf "  2. Scroll down → Custom Shortcuts → [+]\n"
    printf "  3. Name: Onscreen Translator\n"
    printf "  4. Command: %s\n" "$LAUNCHER_TRIGGER"
    printf "  5. Shortcut: Super+T → Set\n"
    echo ""
    printf "  (Press Ctrl+C to skip and add it manually later.)\n"
    echo ""
done

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
printf "  ${BOLD}Start:${RESET}    %s\n" "$LAUNCHER"
printf "  ${BOLD}Trigger:${RESET}  %s\n" "$LAUNCHER_TRIGGER"
printf "  ${BOLD}Hotkey:${RESET}   Super+T  (GNOME custom shortcut → runs the trigger command)\n"
printf "  ${BOLD}Config:${RESET}   %s\n" "$CONFIG_FILE"
echo ""
printf "  On first launch, models will be downloaded automatically:\n"
printf "    • PaddleOCR detection model  (~50 MB)\n"
printf "    • Manga OCR model            (~400 MB, Japanese text recognition)\n"
printf "  Subsequent launches are instant.\n"
echo ""
