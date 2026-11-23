#!/usr/bin/env bash
# 部署完整 Compose 栈。重复运行沿用已保存的环境配置与持久卷。
set +x
set -euo pipefail
umask 077

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
env_file="$root/.env.deploy"
project=buildsize
public_url=
web_port=
jenkins_host=
download_hosts=

fail() {
    printf '部署失败：%s\n' "$*" >&2
    exit 1
}

usage() {
    cat <<'EOF'
用法：bash deploy/deploy.sh [选项]

  --public-url URL          现有网关提供的 HTTPS 地址。首次省略时仅监听本机 HTTP
  --jenkins-host HOST       Jenkins BUILD_URL 的主机名，用于允许构建来源链接
  --apk-download-hosts LIST 服务器允许下载 APK 的主机名，多个用逗号分隔
  --port PORT               本机 Web 端口，首次默认 8080
  --env-file PATH           配置文件，默认为项目根目录的 .env.deploy
  --project-name NAME       Compose 项目名，默认 buildsize
  -h, --help               显示帮助

已有配置文件会被复用。改变地址或主机白名单时，先修改该文件再重新运行。
EOF
}

while (($# > 0)); do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --public-url|--jenkins-host|--apk-download-hosts|--port|--env-file|--project-name)
            [[ $# -ge 2 && -n "$2" && "$2" != --* ]] || fail "$1 需要一个值"
            case "$1" in
                --public-url) public_url=${2%/} ;;
                --jenkins-host) jenkins_host=$2 ;;
                --apk-download-hosts) download_hosts=$2 ;;
                --port) web_port=$2 ;;
                --env-file) env_file=$2 ;;
                --project-name) project=$2 ;;
            esac
            shift 2
            ;;
        *) fail "未知参数 $1，请查看 --help" ;;
    esac
done

host_re='[a-zA-Z0-9]([a-zA-Z0-9.-]*[a-zA-Z0-9])?'
origin_re="^https://($host_re|\[[0-9a-fA-F:]+\])(:([0-9]{1,5}))?$"
valid_port() {
    [[ "$1" =~ ^[1-9][0-9]{0,4}$ ]] && ((10#$1 <= 65535))
}
if [[ -n "$public_url" ]]; then
    [[ "$public_url" =~ $origin_re ]] || fail '--public-url 必须为 HTTPS 地址，可带端口'
    public_port=${BASH_REMATCH[4]:-443}
    valid_port "$public_port" || fail 'HTTPS 地址的端口须介于 1 和 65535'
fi
if [[ -n "$web_port" ]]; then
    valid_port "$web_port" || fail 'Web 端口须介于 1 和 65535'
fi
[[ -z "$jenkins_host" || "$jenkins_host" =~ ^$host_re$ ]] ||
    fail '--jenkins-host 只填主机名，不含协议、端口或路径'
[[ -z "$download_hosts" || "$download_hosts" =~ ^$host_re(,$host_re)*$ ]] ||
    fail '--apk-download-hosts 请填逗号分隔的精确主机名'
[[ "$project" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || fail 'Compose 项目名不合法'
[[ "$env_file" = /* ]] || env_file="$PWD/$env_file"
[[ ! -L "$env_file" && ( ! -e "$env_file" || -f "$env_file" ) ]] ||
    fail '配置路径必须指向普通文件'

command -v docker >/dev/null || fail '请先安装 Docker Engine 和 Compose 插件'
docker compose version >/dev/null || fail 'Docker Compose 插件不可用'
engine_os=$(docker info --format '{{.OSType}}') || fail '无法连接 Docker 服务，请检查启动状态和权限'
[[ "$engine_os" = linux ]] || fail 'Docker 需要使用 Linux 容器'
up_help=$(docker compose up --help)
[[ "$up_help" = *--wait-timeout* ]] || fail '请升级 Compose 插件以支持 --wait-timeout'

compose() (
    # 显式使用部署文件，避免交互环境中的变量覆盖已保存的密钥或端口。
    unset APP_ENV PUBLIC_BASE_URL DATABASE_URL POSTGRES_PASSWORD SESSION_SECRET TOKEN_HASH_PEPPER
    unset TRUSTED_LINK_HOSTS APK_DOWNLOAD_HOSTS BUILDSIZE_WEB_PORT BUILDSIZE_API_IMAGE BUILDSIZE_WEB_IMAGE
    unset UPLOAD_MAX_BYTES UPLOAD_TIMEOUT_SECONDS ANALYSIS_TIMEOUT_SECONDS
    unset APK_RETENTION_DAYS REPORT_RETENTION_DAYS METRICS_RETENTION_DAYS
    docker compose --env-file "$env_file" \
        -p "$project" -f "$root/deploy/compose.yaml" "$@"
)

if [[ ! -f "$env_file" ]]; then
    volumes=$(docker volume ls --filter "label=com.docker.compose.project=$project" --format '{{.Name}}')
    [[ -z "$volumes" ]] || fail "项目 $project 已有数据卷，请先找回原配置文件"
    command -v openssl >/dev/null || fail '首次生成密钥需要 openssl'
    [[ -d "$(dirname -- "$env_file")" ]] || fail '配置文件的父目录不存在'
    web_port=${web_port:-8080}
    app_env=production
    if [[ -z "$public_url" ]]; then
        app_env=development
        public_url="http://127.0.0.1:$web_port"
    fi
    db_password=$(openssl rand -hex 32)
    session_secret=$(openssl rand -hex 32)
    token_pepper=$(openssl rand -hex 32)
    temp_env=$(mktemp "$(dirname -- "$env_file")/.env.buildsize.XXXXXX")
    trap 'rm -- "$temp_env"' EXIT
    cat > "$temp_env" <<EOF
APP_ENV=$app_env
PUBLIC_BASE_URL=$public_url
DATABASE_URL=postgresql+psycopg://buildsize:$db_password@postgres:5432/buildsize
POSTGRES_PASSWORD=$db_password
SESSION_SECRET=$session_secret
TOKEN_HASH_PEPPER=$token_pepper
TRUSTED_LINK_HOSTS=$jenkins_host
APK_DOWNLOAD_HOSTS=$download_hosts
BUILDSIZE_WEB_PORT=$web_port
EOF
    if [[ "$project" != buildsize ]]; then
        printf 'BUILDSIZE_API_IMAGE=buildsize-api:%s\nBUILDSIZE_WEB_IMAGE=buildsize-web:%s\n' \
            "$project" "$project" >> "$temp_env"
    fi
    ln -- "$temp_env" "$env_file" || fail '无法建立配置文件，请检查路径或其他部署进程'
    rm -- "$temp_env"
    trap - EXIT
    printf '已生成部署配置 %s\n' "$env_file"
else
    printf '使用已有配置 %s\n' "$env_file"
fi

compose config --quiet
# 只提取非敏感配置。Compose 的完整插值环境不输出到终端。
setting() {
    compose config --environment | awk -v key="$1" \
        'index($0, key "=") == 1 { print substr($0, length(key) + 2) }'
}
for pair in "PUBLIC_BASE_URL=$public_url" "TRUSTED_LINK_HOSTS=$jenkins_host" \
    "APK_DOWNLOAD_HOSTS=$download_hosts" "BUILDSIZE_WEB_PORT=$web_port"; do
    key=${pair%%=*}
    expected=${pair#*=}
    [[ -n "$expected" ]] || continue
    current=$(setting "$key")
    [[ "${current%/}" = "$expected" ]] || fail "$key 与已有配置不同，请先修改 $env_file"
done

printf '%s\n' '正在构建 API 与 Web 镜像'
compose build api web
printf '%s\n' '正在启动服务、迁移数据库并等待健康检查'
if ! compose up -d --no-build --wait --wait-timeout 180; then
    compose ps -a >&2 || true
    fail '服务未能就绪，请查看 migrate、api 和 web 容器日志'
fi
ready=$(compose exec -T web wget -q -T 5 -O - http://127.0.0.1:8080/api/v1/health/ready) ||
    fail 'Web 容器无法访问 API 就绪接口'
[[ "$ready" = '{"status":"ready"}' ]] || fail 'API 就绪接口返回异常'

public_url=$(setting PUBLIC_BASE_URL)
binding=$(compose port web 8080)
printf 'BuildSize 已就绪 · %s\n' "$public_url"
if [[ "$public_url" = https://* ]]; then
    printf 'HTTPS 网关的本机回源目标 http://%s\n' "$binding"
fi
printf '首次使用请按 deploy/README.md 创建管理员与项目。\n'
