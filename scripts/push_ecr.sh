#!/usr/bin/env bash
# Publish an already-built image using the dedicated local IAM profile.
set -euo pipefail
umask 077

tag=${1:-}
[[ $# == 1 && "$tag" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ ]] || {
  echo 'Usage: bash scripts/push_ecr.sh <image-tag>' >&2; exit 2;
}
repository=public.ecr.aws/x2l9m6x8/token-tv
image=$repository:$tag
profile=token-tv-publish
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
for command in aws docker python3; do command -v "$command" >/dev/null; done

# Profile selection must not inherit credentials or alternate credential files.
aws_publisher() {
  env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN \
    -u AWS_SECURITY_TOKEN -u AWS_PROFILE -u AWS_DEFAULT_PROFILE \
    -u AWS_CONFIG_FILE -u AWS_SHARED_CREDENTIALS_FILE \
    aws --profile "$profile" --region us-east-1 "$@"
}
identity=$(aws_publisher sts get-caller-identity --query Arn --output text)
[[ "$identity" == arn:aws:iam::772326922092:user/token-tv-publisher ]] || {
  echo 'Expected the dedicated token-tv-publisher IAM user.' >&2; exit 1;
}
revision=$(docker image inspect "$image" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')
[[ "$revision" =~ ^[0-9a-f]{40}$ ]] || { echo 'Image needs a full source revision label.' >&2; exit 1; }
temporary=$(mktemp -d "${TMPDIR:-/tmp}/token-tv-publish-XXXXXXXX")
cleanup() { rm -rf -- "$temporary"; }
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Check every layer before registry authentication, including deleted state.
docker save -o "$temporary/image.tar" "$image"
python3 "$script_dir/check_public_image.py" "$temporary/image.tar" \
  --revision "$revision" --require-platform linux/amd64
rm -- "$temporary/image.tar"

# Existing tags may be repeated only for the exact same known registry digest.
if remote_digest=$(aws_publisher ecr-public describe-images --repository-name token-tv \
    --image-ids "imageTag=$tag" --query 'imageDetails[0].imageDigest' --output text 2>"$temporary/lookup-error"); then
  known_digests=$(docker image inspect "$image" --format '{{range .RepoDigests}}{{println .}}{{end}}')
  if ! printf '%s\n' "$known_digests" | python3 -c \
    'import sys; raise SystemExit(0 if sys.argv[1] in sys.stdin.read().splitlines() else 1)' "$repository@$remote_digest"; then
    echo 'Tag already exists with an unconfirmed image. Choose a new tag.' >&2; exit 1;
  fi
elif ! python3 -c 'import sys; from pathlib import Path; raise SystemExit(0 if "ImageNotFoundException" in Path(sys.argv[1]).read_text() else 1)' "$temporary/lookup-error"; then
  echo 'Could not check the existing ECR tag; upload cancelled.' >&2; exit 1;
fi

mkdir -m 700 "$temporary/docker"
export DOCKER_CONFIG="$temporary/docker"
aws_publisher ecr-public get-login-password | \
  docker login --username AWS --password-stdin public.ecr.aws >/dev/null
docker push "$image"
echo "Published $image using $profile."
