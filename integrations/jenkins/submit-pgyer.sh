#!/bin/bash
# 在蒲公英发布成功后独立执行。上报故障告警，中止仍透传。
set +x

if [[ $# -ne 2 || -z "${WORKSPACE:-}" ]]; then
  printf '%s\n' 'BuildSize 跳过上报，请提供构建目录、APK 父目录和 Jenkins WORKSPACE' >&2
  exit 0
fi

ci_dir=$1
apk_dir=$2
report_script="$(dirname -- "${BASH_SOURCE[0]}")/unity-report-handoff.py"
publish_log="$WORKSPACE/.jenkins/pgyer_upload.log"

shopt -s nullglob
apks=("$apk_dir"/*.apk)
if (( ${#apks[@]} != 1 )); then
  printf 'BuildSize 跳过上报，%s 下需要恰好 1 个 APK，实际找到 %d 个\n' \
    "$apk_dir" "${#apks[@]}" >&2
  exit 0
fi

for file in "$report_script" "${apks[0]}" "$ci_dir/context.json" \
  "$ci_dir/completed-at.txt" "$publish_log"
do
  if [[ ! -f "$file" || ! -s "$file" ]]; then
    printf 'BuildSize 跳过上报，文件缺失或为空 %s\n' "$file" >&2
    exit 0
  fi
done

umask 077
download_url_file=$(mktemp "$ci_dir/.apk-download-url.XXXXXXXX") || {
  printf '%s\n' 'BuildSize 跳过上报，无法创建临时下载链接文件' >&2
  exit 0
}
trap 'rm -- "$download_url_file"' EXIT

build_key=$(
  if [[ -f "$WORKSPACE/.env.sh" ]]; then
    # 中台本次构建生成的配置与 Jenkins 的其他变量在此子进程隔离。
    source "$WORKSPACE/.env.sh" >/dev/null 2>&1 || exit 2
  fi
  export PGY_API_KEY PGYER_KEY
  # 蒲公英使用系统公共 CA。上报进程仍使用原配置的 BuildSize CA。
  unset SSL_CERT_FILE
  exec python3 "$report_script" pgyer-url --publish-log "$publish_log" --output "$download_url_file"
) || {
  status=$?
  case "$status" in 130|143) exit "$status" ;; esac
  printf '%s\n' 'BuildSize 未取得本次 APK 直链，请查看上方错误' >&2
  exit 0
}

if python3 "$report_script" submit \
  --directory "$ci_dir" --apk "${apks[0]}" --pgyer-build-key "$build_key" \
  --apk-url-file "$download_url_file" --completed-at-file "$ci_dir/completed-at.txt" \
  --receipt "$ci_dir/receipt.json"; then
  printf '%s\n' 'BuildSize 已接收，等待后台下载和分析 APK'
else
  status=$?
  case "$status" in 130|143) exit "$status" ;; esac
  printf '%s\n' 'BuildSize 上报失败，请查看上方错误' >&2
fi
