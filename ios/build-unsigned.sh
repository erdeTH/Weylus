#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
command -v xcodegen >/dev/null || { echo 'Install XcodeGen on the macOS build host.' >&2; exit 1; }
xcodegen generate
xcodebuild -project WeylusUSB.xcodeproj -scheme WeylusUSB \
    -configuration Release -sdk iphoneos -destination 'generic/platform=iOS' \
    -derivedDataPath .build CODE_SIGNING_ALLOWED=NO build
mkdir -p .build/package/Payload
rm -rf .build/package/Payload/WeylusUSB.app
cp -R .build/Build/Products/Release-iphoneos/WeylusUSB.app .build/package/Payload/
rm -f .build/WeylusUSB-unsigned.ipa
(cd .build/package && zip -qry ../WeylusUSB-unsigned.ipa Payload)
echo "Unsigned app: $(pwd)/.build/WeylusUSB-unsigned.ipa"
