#!/bin/sh
# Build dist/image-hunt.zip from the committed tree (see .gitattributes export-ignore: no saved searches, no state).
set -eu
cd "$(dirname "$0")"
mkdir -p dist
git archive --format=zip --prefix=image-hunt/ -o dist/image-hunt.zip HEAD
echo "$(pwd)/dist/image-hunt.zip"
