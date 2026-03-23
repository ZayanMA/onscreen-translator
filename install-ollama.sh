#!/usr/bin/env bash
# install-ollama.sh — set up Ollama and pull a translation model
set -e

if command -v tput &>/dev/null && tput setaf 1 &>/dev/null; then
    GREEN=$(tput setaf 2); YELLOW=$(tput setaf 3); CYAN=$(tput setaf 6)
    BOLD=$(tput bold); RESET=$(tput sgr0)
else
    GREEN="" YELLOW="" CYAN="" BOLD="" RESET=""
fi
info()    { printf "${GREEN}[✓]${RESET} %s\n" "$*"; }
warn()    { printf "${YELLOW}[!]${RESET} %s\n" "$*"; }
heading() { printf "\n${BOLD}${CYAN}▶ %s${RESET}\n" "$*"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$HOME/.config/onscreen-translator/config.toml"

# ── Install Ollama ─────────────────────────────────────────────────────────────
heading "Ollama installation"

if command -v ollama &>/dev/null; then
    info "Ollama already installed: $(ollama --version 2>/dev/null || echo 'unknown version')"
else
    warn "Ollama not found — installing now…"
    curl -fsSL https://ollama.com/install.sh | sh
    info "Ollama installed"
fi

# ── Start Ollama server if not running ────────────────────────────────────────
heading "Ollama server"

if curl -sf http://localhost:11434 &>/dev/null; then
    info "Ollama server already running"
else
    warn "Starting Ollama server in background…"
    ollama serve &>/dev/null &
    sleep 2
    if curl -sf http://localhost:11434 &>/dev/null; then
        info "Ollama server started"
    else
        warn "Could not confirm server started — it may take a moment."
    fi
fi

# ── Pull a model ───────────────────────────────────────────────────────────────
heading "Translation model"

echo ""
echo "Choose a model for translation:"
printf "  ${BOLD}1)${RESET} llama3.2      (~2 GB)   Fast, good general quality\n"
printf "  ${BOLD}2)${RESET} qwen2.5:7b    (~4.7 GB) Better Japanese/Chinese quality\n"
printf "  ${BOLD}3)${RESET} Custom        Enter a model name manually\n"
echo ""
read -rp "Choice [1]: " MODEL_CHOICE

case "$MODEL_CHOICE" in
    2) CHOSEN_MODEL="qwen2.5:7b" ;;
    3) read -rp "Model name: " CHOSEN_MODEL ;;
    *) CHOSEN_MODEL="llama3.2" ;;
esac

echo ""
info "Pulling ${CHOSEN_MODEL} — this may take several minutes on first run…"
if ollama pull "$CHOSEN_MODEL"; then
    info "Model ${CHOSEN_MODEL} ready"
else
    warn "Pull failed. Try manually: ollama pull ${CHOSEN_MODEL}"
    exit 1
fi

# ── Update config ──────────────────────────────────────────────────────────────
heading "Updating config"

if [[ -f "$CONFIG_FILE" ]]; then
    if grep -q '^\[ollama\]' "$CONFIG_FILE"; then
        sed -i "s/^model = .*/model = \"${CHOSEN_MODEL}\"/" "$CONFIG_FILE"
        info "Updated config: model = ${CHOSEN_MODEL}"
    else
        cat >> "$CONFIG_FILE" <<TOML

[ollama]
# Local Ollama model used for translation.
model = "${CHOSEN_MODEL}"
url   = "http://localhost:11434"
TOML
        info "Added [ollama] section to config"
    fi
else
    warn "Config file not found at $CONFIG_FILE — run install.sh first"
fi

# ── Done ───────────────────────────────────────────────────────────────────────
echo ""
printf "${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}\n"
printf "${BOLD}${GREEN}  Ollama ready!${RESET}\n"
printf "${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}\n"
echo ""
printf "  Model:   ${BOLD}${CHOSEN_MODEL}${RESET}\n"
printf "  Config:  ${CONFIG_FILE}\n"
echo ""
printf "  To switch models later, edit the config or re-run this script.\n"
printf "  Then restart: ${BOLD}pkill -f onscreen_translator; ./onscreen-translator${RESET}\n"
echo ""
