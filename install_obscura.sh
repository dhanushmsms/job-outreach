#!/bin/bash
# Install Obscura headless browser (Rust, lightweight Chrome replacement)

set -e

OS=$(uname -s)
ARCH=$(uname -m)
INSTALL_DIR="$HOME/.local/bin"
mkdir -p "$INSTALL_DIR"

echo "Detecting platform: $OS / $ARCH"

if [ "$OS" = "Darwin" ]; then
    if [ "$ARCH" = "arm64" ]; then
        BINARY="obscura-aarch64-macos"
    else
        BINARY="obscura-x86_64-macos"
    fi
elif [ "$OS" = "Linux" ]; then
    BINARY="obscura-x86_64-linux"
else
    echo "Unsupported OS: $OS"
    exit 1
fi

URL="https://github.com/h4ckf0r0day/obscura/releases/latest/download/${BINARY}.tar.gz"
echo "Downloading $BINARY from $URL ..."
curl -L "$URL" -o /tmp/obscura.tar.gz
tar -xzf /tmp/obscura.tar.gz -C "$INSTALL_DIR"
chmod +x "$INSTALL_DIR/obscura"
rm /tmp/obscura.tar.gz

echo ""
echo "Obscura installed at $INSTALL_DIR/obscura"
echo "Add to PATH if needed:  export PATH=\"\$HOME/.local/bin:\$PATH\""
echo ""
echo "Test it:  obscura serve --port 9222 --stealth"
