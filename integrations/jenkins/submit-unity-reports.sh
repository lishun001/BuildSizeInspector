#!/bin/sh
set +x
set -eu

if [ "$#" -ne 6 ]; then
    printf '%s\n' '用法：submit-unity-reports.sh context.json final.apk buildKey completed-at.txt submission-dir receipt.json' >&2
    exit 2
fi

buildsize_bin=${BUILDSIZE_BIN:-buildsize}
"$buildsize_bin" collect --context "$1" --apk "$2" --pgyer-build-key "$3" \
    --completed-at-file "$4" --output "$5" >&2
exec "$buildsize_bin" submit --reports-only --directory "$5" --receipt "$6"
