#!/usr/bin/env bash
# recipes/sassc/test.sh — smoke test the staged sassc.
#
#  1. sassc and the LibSass in it report the pinned versions.
#  2. It compiles SCSS the way libadwaita's meson build calls it
#     (`sassc -a -M -t compact in.scss out.css`): variables, nesting, a mixin,
#     an @import of a partial, and the colour functions GTK stylesheets lean
#     on (mix, transparentize). The output is compared byte for byte.
#  3. It rejects invalid SCSS with a non-zero exit.
# Everything runs from a relocated copy of the prefix.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"

echo "-- sassc smoke test (${CVC_PLATFORM:-native}) --"

T="$(mktemp -d)"
trap 'rm -rf "${T}"' EXIT

MOVED="${T}/relocated/prefix"
mkdir -p "${MOVED}"
cp -pR "${CVC_INSTALL_DIR}/." "${MOVED}/"
SASSC="${MOVED}/bin/sassc"

ver="$("${SASSC}" --version)"
echo "${ver}" | sed 's/^/  /'
echo "${ver}" | grep -qx 'sassc: 3.6.2' || { echo "FAIL: sassc version"; exit 1; }
echo "${ver}" | grep -qx 'libsass: 3.6.6' || { echo "FAIL: libsass version"; exit 1; }

mkdir -p "${T}/scss"
cat > "${T}/scss/_colors.scss" <<'EOF'
$accent: #3584e4;
$bg: #ffffff;
EOF
cat > "${T}/scss/main.scss" <<'EOF'
@import 'colors';

@mixin focus-ring($c) { outline: 2px solid transparentize($c, 0.5); }

button {
  color: $accent;
  background: mix($accent, $bg, 20%);
  &:focus { @include focus-ring($accent); }
  .label { padding: 2px * 3; }
}
EOF
cat > "${T}/want.css" <<'EOF'
button { color: #3584e4; background: #d7e6fa; }

button:focus { outline: 2px solid rgba(53, 132, 228, 0.5); }

button .label { padding: 6px; }
EOF
"${SASSC}" -a -M -t compact "${T}/scss/main.scss" "${T}/got.css"
if ! diff -u "${T}/want.css" "${T}/got.css"; then
    echo "FAIL: compiled CSS differs"; exit 1
fi
echo "  OK: SCSS compiled (variables, @import, mixin, nesting, mix, transparentize)"

printf 'a { color: ; }\n' > "${T}/bad.scss"
if "${SASSC}" "${T}/bad.scss" > "${T}/bad.out" 2>&1; then
    echo "FAIL: invalid SCSS was accepted"; exit 1
fi
echo "  OK: invalid SCSS rejected ($(head -n 1 "${T}/bad.out"))"

echo "-- sassc smoke test passed --"
