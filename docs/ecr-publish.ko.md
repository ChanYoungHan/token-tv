# 브라우저 로그인 없는 ECR 업로드

이 Mac의 AWS 프로필 `token-tv-publish`는 `token-tv-publisher` IAM 사용자를 사용합니다.
권한은 `public.ecr.aws/x2l9m6x8/token-tv` 이미지 업로드와 메타데이터 조회에 한정됩니다.
저장소·이미지 삭제, 다른 저장소 업로드, IAM 변경 권한은 부여하지 않습니다.
권한 정의는 [전용 정책](../docker/ecr-publish-policy.json)에 있습니다.

## 업로드

배포할 기능 커밋이 포함된 브랜치에서 linux/amd64 이미지를 빌드하고 고유 태그와 소스 리비전을 지정합니다.

```bash
docker buildx build --platform linux/amd64 --load \
  --build-arg VCS_REF="$(git rev-parse HEAD)" \
  -t public.ecr.aws/x2l9m6x8/token-tv:0.2.0-homelab.5 .
token-tv-push 0.2.0-homelab.5
```

`.5`는 다음 배포의 예시 태그입니다. 이미 사용한 태그는 다른 이미지로 덮어쓰지 않습니다.
이 Mac에는 `~/.local/bin/token-tv-push` 명령을 설치했습니다.
도구 본체와 이미지 검사기는 `~/.local/share/token-tv-publisher`에 있으며 키는 포함하지 않습니다.
설치하지 않은 환경에서는 이 레포의 `bash scripts/push_ecr.sh <태그>`를 사용할 수 있습니다.
스크립트는 전용 IAM 사용자를 확인하고 이미지의 모든 레이어에서 인증정보·DB 유입을 검사한 뒤 업로드합니다.
ECR의 12시간짜리 토큰은 실행할 때 자동 발급합니다. 브라우저 로그인이나 MFA 입력은 필요하지 않습니다.
임시 Docker 인증 파일은 작업이 끝나면 제거합니다. 기존 Docker 로그인 설정은 바꾸지 않습니다.

## 키 보관

키는 Mac의 `~/.aws/credentials` 안 `[token-tv-publish]` 프로필에만 보관합니다.
파일 권한은 `0600`입니다. 키를 `.env`, 프로젝트, Git, 컨테이너, 운영 서버에 복사하지 않습니다.
기존 `default` 프로필은 유지합니다. 이 키를 사용 중지하거나 삭제하면 다음 업로드부터 인증되지 않습니다.

키 교체는 관리 권한이 있는 계정으로 IAM 사용자 `token-tv-publisher`의 새 키를 만들고,
해당 로컬 프로필을 갱신한 뒤 이전 키를 폐기하는 순서로 진행합니다.
키 원문은 채팅이나 작업 로그에 출력하지 않습니다.

인증 방식: [AWS Access Key](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_access-keys.html),
[ECR Public 인증 토큰](https://docs.aws.amazon.com/AmazonECR/latest/public/public-registry-auth.html).
