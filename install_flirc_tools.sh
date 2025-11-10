#!/bin/bash
# Automatic Flirc tools installer for Linux (Docker/native)
# Uses official Flirc install script for package managers or direct archive install

set -e

OFFICIAL_INSTALLER="https://apt.flirc.tv/install.sh"
FLIRC_BASE_URL="http://apt.flirc.tv/arch"
APPCAST_URL="https://flirc.com/software/release/gui/windows/appcast.xml"
INSTALL_DIR="/usr/local/bin"
RULES_DIR="/etc/udev/rules.d"
TMP_DIR="/tmp/flirc_install"

echo "==> Flirc Tools Installer for Linux"

# Check if flirc_util exists and get version
CURRENT_VERSION=""
if command -v flirc_util >/dev/null 2>&1; then
    CURRENT_VERSION=$(flirc_util version 2>/dev/null | head -n1 || echo "unknown")
    echo "==> Current flirc_util version: $CURRENT_VERSION"
fi

# Detect if we can use package manager (Debian/RedHat based)
USE_PKG_MANAGER=false
if [ -f /etc/debian_version ] || [ -f /etc/redhat-release ] || [ -f /etc/os-release ]; then
    USE_PKG_MANAGER=true
fi

# Option 1: Use official package manager installer (preferred for Debian/RedHat)
if [ "$USE_PKG_MANAGER" = true ] && [ -z "$FORCE_ARCHIVE" ]; then
    echo "==> Detected package manager support, using official installer"
    echo "==> Downloading and running: $OFFICIAL_INSTALLER"
    
    # Run official installer with -y flag (non-interactive)
    if curl -fsSL "$OFFICIAL_INSTALLER" | bash -s - -y; then
        if command -v flirc_util >/dev/null 2>&1; then
            echo "==> Installation via package manager successful!"
            NEW_VERSION=$(flirc_util version 2>/dev/null | head -n1 || echo "unknown")
            echo "==> Installed version: $NEW_VERSION"
            echo "==> flirc_util location: $(which flirc_util)"
            if ! command -v irtools >/dev/null 2>&1; then
                ln -sf "$(which flirc_util)" "$INSTALL_DIR/irtools"
                echo "==> Created shim: $INSTALL_DIR/irtools -> flirc_util"
            fi
            exit 0
        fi
        echo "WARNING: Package manager installer completed but flirc_util is missing; falling back to archive."
    else
        echo "WARNING: Package manager install failed, falling back to archive install"
    fi
fi

# Option 2: Direct archive install (for non-standard distros or when package manager fails)
echo "==> Using direct archive installation method"

echo "==> Fetching latest Flirc version from appcast..."
FLIRC_VERSION=$(curl -fsSL "$APPCAST_URL" | grep -oP '<title>Version \K[0-9.]+' | head -n1 || echo "3.27.19")
echo "==> Latest version: $FLIRC_VERSION"

echo "==> Detecting system architecture..."
ARCH=$(uname -m)
case "$ARCH" in
    x86_64|amd64)
        FLIRC_ARCH="x86_64"
        ;;
    i386|i686)
        FLIRC_ARCH="i386"
        ;;
    armv7l|armhf)
        FLIRC_ARCH="armhf"
        ;;
    aarch64|arm64)
        # Try armhf for ARM64 as fallback
        FLIRC_ARCH="armhf"
        ;;
    *)
        echo "ERROR: Unsupported architecture: $ARCH"
        exit 1
        ;;
esac

echo "==> Architecture: $ARCH -> Flirc package: $FLIRC_ARCH"

# Download and extract
echo "==> Downloading Flirc tools for $FLIRC_ARCH..."
mkdir -p "$TMP_DIR"
cd "$TMP_DIR"

DOWNLOAD_URL="${FLIRC_BASE_URL}/${FLIRC_ARCH}/flirc.latest.${FLIRC_ARCH}.tar.gz"
echo "==> Fetching from: $DOWNLOAD_URL"

if ! curl -fsSL "$DOWNLOAD_URL" -o flirc.tar.gz; then
    echo "ERROR: Failed to download Flirc tools"
    exit 1
fi

echo "==> Extracting archive..."
tar -xzf flirc.tar.gz

# Find binaries in extracted directory
FLIRC_UTIL=$(find . -type f -name "flirc_util" | head -n1)
IRTOOLS_BIN=$(find . -type f -name "irtools" | head -n1)
FLIRC_GUI=$(find . -type f -name "flirc" | head -n1)
RULES_FILE=$(find . -type f -name "99-flirc.rules" | head -n1)

if [ -z "$FLIRC_UTIL" ]; then
    echo "ERROR: flirc_util binary not found in archive"
    exit 1
fi

echo "==> Installing binaries to $INSTALL_DIR..."
install -m 755 "$FLIRC_UTIL" "$INSTALL_DIR/flirc_util"

if [ -n "$IRTOOLS_BIN" ]; then
    rm -f "$INSTALL_DIR/irtools"
    install -m 755 "$IRTOOLS_BIN" "$INSTALL_DIR/irtools"
    echo "==> Installed irtools binary"
else
    echo "ERROR: irtools binary not found in archive; installation cannot continue without it"
    exit 1
fi

if [ -n "$FLIRC_GUI" ]; then
    install -m 755 "$FLIRC_GUI" "$INSTALL_DIR/flirc" 2>/dev/null || true
fi

# Install udev rules if available
if [ -n "$RULES_FILE" ] && [ -d "$RULES_DIR" ]; then
    echo "==> Installing udev rules..."
    install -m 644 "$RULES_FILE" "$RULES_DIR/99-flirc.rules"
    # Reload udev rules if udevadm is available
    if command -v udevadm >/dev/null 2>&1; then
        udevadm control --reload-rules 2>/dev/null || true
        udevadm trigger 2>/dev/null || true
    fi
fi

# Cleanup
cd /
rm -rf "$TMP_DIR"

# Verify installation
NEW_VERSION=$(flirc_util version 2>/dev/null | head -n1 || echo "unknown")
echo "==> Installation complete!"
echo "==> Installed version: $NEW_VERSION"
echo "==> flirc_util location: $(which flirc_util)"

exit 0
