# onscreen-translator

A live on-screen Japanese translator for Linux/Wayland. Press a hotkey and translated text appears directly over the original — no copy-pasting, no alt-tabbing.

> Built for reading Japanese games, manga readers, and visual novels on Linux.

## How it works

1. Press **Super+T** to start live translation mode
2. The app captures your screen every 500ms via PipeWire
3. Japanese text is detected and recognized using PaddleOCR + Manga OCR
4. Translations are fetched from the DeepL API
5. A transparent overlay displays the translated text at the exact position of the original

The overlay is fully click-through — you can interact with whatever is behind it normally.

## Requirements

- Linux with **Wayland** (X11 is not supported)
- GNOME or another compositor that supports the `wlr-layer-shell` protocol
- A free [DeepL API key](https://www.deepl.com/pro-api)
- Python 3.11+
- `apt`-based distro (Debian/Ubuntu/Pop!_OS)

## Installation

```bash
git clone https://github.com/yourusername/onscreen-translator
cd onscreen-translator
./install.sh
```

The installer will:
- Install system dependencies via `apt`
- Create a Python virtual environment
- Install Python dependencies (PaddleOCR, Manga OCR, DeepL)
- Prompt you for your DeepL API key
- Write a default config to `~/.config/onscreen-translator/config.toml`

## Usage

```bash
# Start the application (runs in the background)
./onscreen-translator

# Toggle live translation on/off
./onscreen-translator-trigger
```

On first run, GNOME will ask for screen capture permission. The token is saved so subsequent runs are silent.

## Configuration

Edit `~/.config/onscreen-translator/config.toml`:

```toml
[hotkey]
preferred_trigger = "Super+t"

[overlay]
auto_dismiss_seconds = 12   # Hide translation after N seconds
show_original = false        # Also show the original Japanese text
font_size_translated = 15
font_size_original = 11

[ocr]
language = "japan"

[deepl]
api_key = "your-key-here"
target_language = "EN-US"
```

## OCR pipeline

The app uses a multi-stage OCR approach to maximize accuracy on stylized game/manga fonts:

- **PaddleOCR** (PP-OCRv3) handles full-frame text detection
- Low-confidence or small/difficult regions are refined with additional passes: RGB upscaling, grayscale+contrast enhancement, or **Manga OCR** (a transformer model specialized for manga/game text)
- Recognized text groups are scored by Japanese character ratio, length, and OCR confidence — the best result across all variants is kept
- Regions are cached by content hash to avoid redundant processing on unchanged frames

## Tech stack

| Component | Technology |
|-----------|-----------|
| GUI / overlay | GTK4 + GtkLayerShell |
| Screen capture | PipeWire via D-Bus ScreenCast portal |
| Global hotkey | D-Bus GlobalShortcuts portal |
| OCR | PaddleOCR + Manga OCR |
| Translation | DeepL API |

## Autostart

To launch automatically on login, copy the desktop entry:

```bash
cp data/onscreen-translator.desktop ~/.config/autostart/
```

## License

MIT
