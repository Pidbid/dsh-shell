#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "build_linux.sh must run on Linux" >&2
  exit 1
fi

for command_name in uv dpkg-deb desktop-file-validate; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "missing build command: ${command_name}" >&2
    exit 1
  fi
done

if [[ "$(dpkg --print-architecture)" != "amd64" ]]; then
  echo "only amd64 DEB builds are supported by this script" >&2
  exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT}"

DEB_ONLY=0
if [[ "${1:-}" == "--deb-only" ]]; then
  DEB_ONLY=1
fi

APP_VERSION="$(uv run python -c 'from dsh_shell.version import __version__; print(__version__)')"
DEB_NAME="dsh-shell_${APP_VERSION}_amd64.deb"
BUILD_DIR="dist/nuitka-linux"
PACKAGE_ROOT="dist/deb-root"
APP_ROOT="${PACKAGE_ROOT}/opt/dsh-shell"

for asset in icon.png icon.ico icon-48.png icon-256.png icon-512.png; do
  if [[ ! -f "assets/${asset}" ]]; then
    echo "missing assets/${asset}; the icon set is committed to the repository" >&2
    exit 1
  fi
done

if [[ "${DEB_ONLY}" == "0" ]]; then
  rm -rf "${BUILD_DIR}" "${PACKAGE_ROOT}" "dist/dsh-shell"
  mkdir -p dist

  echo "==> Nuitka onefile ${APP_VERSION}"
  uv run nuitka \
    --onefile \
    --remove-output \
    --enable-plugin=pyside6 \
    --include-package=dsh_shell \
    --include-package=loguru \
    --include-package=ruamel.yaml \
    --include-package=bottle \
    --include-package=proxy_tools \
    --include-package=qtpy \
    --include-qt-plugins=platforminputcontexts \
    --include-data-dir=ui=ui \
    --include-data-dir=assets=assets \
    --output-dir="${BUILD_DIR}" \
    --output-filename=dsh-shell \
    --assume-yes-for-downloads \
    main.py

  BINARY="${BUILD_DIR}/dsh-shell"
  if [[ ! -x "${BINARY}" ]]; then
    echo "Nuitka did not produce ${BINARY}" >&2
    exit 1
  fi
  cp "${BINARY}" dist/dsh-shell
  chmod 0755 dist/dsh-shell

  echo "==> smoke test packaged onefile"
  ./dist/dsh-shell --smoke-test --no-reuse || {
    echo "packaged smoke test failed" >&2
    exit 1
  }
else
  if [[ ! -x dist/dsh-shell ]]; then
    echo "--deb-only requires dist/dsh-shell" >&2
    exit 1
  fi
  BINARY="dist/dsh-shell"
  rm -rf "${PACKAGE_ROOT}"
fi

echo "==> assemble DEB"
mkdir -p \
  "${PACKAGE_ROOT}/DEBIAN" \
  "${APP_ROOT}" \
  "${PACKAGE_ROOT}/usr/bin" \
  "${PACKAGE_ROOT}/usr/share/applications" \
  "${PACKAGE_ROOT}/usr/share/icons/hicolor/48x48/apps" \
  "${PACKAGE_ROOT}/usr/share/icons/hicolor/256x256/apps" \
  "${PACKAGE_ROOT}/usr/share/icons/hicolor/512x512/apps" \
  "${PACKAGE_ROOT}/usr/share/doc/dsh-shell"

cp "${BINARY}" "${APP_ROOT}/dsh-shell"
chmod 0755 "${APP_ROOT}/dsh-shell"

cat > "${PACKAGE_ROOT}/usr/bin/dsh-shell" <<'EOF'
#!/bin/sh
exec /opt/dsh-shell/dsh-shell "$@"
EOF
chmod 0755 "${PACKAGE_ROOT}/usr/bin/dsh-shell"

cat > "${PACKAGE_ROOT}/usr/share/applications/dsh-shell.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=dsh-shell
GenericName=DeepSeek Harness Desktop
Comment=Frameless DeepSeek Harness shell with MCP and skills management
Exec=/usr/bin/dsh-shell
Icon=dsh-shell
Terminal=false
Categories=Development;
StartupNotify=true
StartupWMClass=dsh-shell
EOF

for size in 48 256 512; do
  install -m 0644 "assets/icon-${size}.png" \
    "${PACKAGE_ROOT}/usr/share/icons/hicolor/${size}x${size}/apps/dsh-shell.png"
done

cp README.md "${PACKAGE_ROOT}/usr/share/doc/dsh-shell/README.md"
gzip -9 -n -c README.md > "${PACKAGE_ROOT}/usr/share/doc/dsh-shell/README.md.gz"

INSTALLED_SIZE="$(du -sk "${PACKAGE_ROOT}" | cut -f1)"
cat > "${PACKAGE_ROOT}/DEBIAN/control" <<EOF
Package: dsh-shell
Version: ${APP_VERSION}
Section: devel
Priority: optional
Architecture: amd64
Maintainer: dsh-shell <dsh-shell@localhost>
Installed-Size: ${INSTALLED_SIZE}
Recommends: nodejs, npm
Depends: hicolor-icon-theme, libc6 (>= 2.34), libasound2t64 | libasound2, libatomic1, libbrotli1, libbz2-1.0, libdbus-1-3, libdrm2, libegl1, libexpat1, libffi8, libfontconfig1, libfreetype6, libgbm1, libgcc-s1, libgl1, libglib2.0-0t64 | libglib2.0-0, libglx0, libgssapi-krb5-2, libnspr4, libnss3, libpng16-16t64 | libpng16-16, libstdc++6, libx11-6, libx11-xcb1, libxau6, libxcb1, libxcb-cursor0, libxcb-dri3-0, libxcb-icccm4, libxcb-image0, libxcb-keysyms1, libxcb-randr0, libxcb-render-util0, libxcb-render0, libxcb-shape0, libxcb-shm0, libxcb-sync1, libxcb-util1, libxcb-xfixes0, libxcb-xkb1, libxdmcp6, libxcomposite1, libxdamage1, libxext6, libxfixes3, libxkbcommon0, libxkbcommon-x11-0, libxkbfile1, libxrandr2, libxrender1, libxshmfence1, libxtst6, libzstd1
Description: Frameless DeepSeek Harness desktop shell
 A pywebview desktop shell for the DeepSeek Harness web UI. It reuses an
 existing dsh web server or starts one via dsh/npx/pnpm, and manages the
 official cordis.patch.yml MCP entries plus ~/.agents/skills.
EOF

dpkg-deb --root-owner-group --build "${PACKAGE_ROOT}" "dist/${DEB_NAME}"
desktop-file-validate "${PACKAGE_ROOT}/usr/share/applications/dsh-shell.desktop"
dpkg-deb --info "dist/${DEB_NAME}"
sha256sum "dist/dsh-shell" "dist/${DEB_NAME}"

echo "Linux build complete:"
echo "  dist/dsh-shell"
echo "  dist/${DEB_NAME}"
