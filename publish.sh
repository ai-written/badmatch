#!/bin/bash
set -e

REPO="hsiangleev/badmatch"
VERSION="${1:-latest}"

# 每次发布推**两个**标签：
#   :server-<版本> / :client-<版本>  —— 可回溯到某次提交
#   :server-latest / :client-latest  —— docker-compose.prod.yml 里实际写的标签
# 只推 `<版本>` 的话，生产 `docker compose pull` 永远拉不到新版。
# 版本号省略时就是 latest，这时只推一次、不重复上传。
if [ "$VERSION" = "latest" ]; then
  TAGS="latest"
else
  TAGS="$VERSION latest"
fi

# 工作区有未提交改动时，镜像内容 ≠ 版本号所指的提交。只提醒、不阻止：本地调试也要能发。
HERE="$(cd "$(dirname "$0")" && pwd)"
if command -v git >/dev/null 2>&1 && [ -n "$(git -C "$HERE" status --porcelain 2>/dev/null)" ]; then
  echo "警告：工作区有未提交改动，镜像内容与版本号 $VERSION 并不对应（建议先提交再发布）" >&2
fi

echo "=== Building BadMatch v${VERSION} ==="
docker compose build

echo "=== Tagging images ==="
for tag in $TAGS; do
  docker tag badmatch-server "${REPO}:server-${tag}"
  docker tag badmatch-client "${REPO}:client-${tag}"
done

# 从仓库回读 digest：push 的进度行和 digest 行不一定在同一个输出流上，
# 解析 push 输出容易拿到空值，回读才是"仓库里到底是什么"的真凭实据。
digest_of() {
  docker buildx imagetools inspect "$1" --format '{{.Manifest.Digest}}' 2>/dev/null \
    || echo "(读取 digest 失败，请手动 docker buildx imagetools inspect 核对)"
}

echo "=== Pushing to Docker Hub ==="
PUBLISHED=""
for tag in $TAGS; do
  for name in server client; do
    ref="${REPO}:${name}-${tag}"
    docker push "$ref"
    PUBLISHED="${PUBLISHED}${ref}
    $(digest_of "$ref")
"
  done
done

echo "=== Done ==="
echo "Images published:"
printf '%s' "$PUBLISHED"
echo
echo "生产上更新："
echo "  docker compose -f docker-compose.prod.yml pull"
echo "  docker compose -f docker-compose.prod.yml up -d"
