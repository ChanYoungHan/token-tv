# 소스와 배포 스택 관리

TokenTV 구현은 `ChanYoungHan/token-tv` 포크에서 관리합니다.
`PRJ-Home_management_system/services/token-tv`는 배포용 Compose와 운영 안내만 관리합니다.
서브모듈은 사용하지 않습니다.

## 로컬 빌드

포크 저장소에서 현재 소스를 빌드합니다. `TOKEN_TV_IMAGE`를 생략하면 `token-tv:local`입니다.

```bash
export TOKEN_TV_REVISION=$(git rev-parse HEAD)
docker compose build
```

Docker 이미지의 `org.opencontainers.image.revision` 레이블에 소스 커밋이 들어갑니다.
CLI 버전은 Dockerfile과 Compose에서 고정하며, TokenTV는 로컬 소스를 pipx로 설치합니다.

## 기존 서버 재배포

PRJ 저장소의 `services/token-tv` 디렉터리에서 실행합니다.

```bash
docker compose up -d --no-build --pull never
docker compose ps
```

Compose 프로젝트 이름은 `token-tv`입니다. 기존 데이터 볼륨
`token-tv_token-tv-data`를 그대로 사용하므로 같은 Docker 호스트에서 계정 로그인과
시계 설정 백업을 유지합니다. 컨테이너 교체 중에는 잠시 웹 접속이 중단될 수 있습니다.
`down -v`를 실행하지 않습니다. 다른 서버의 볼륨이나 인증 정보는 자동 복사하지 않습니다.

## 컨테이너 레지스트리

셀프호스팅 레지스트리 선정과 업로드는 현재 보류 상태입니다.
지금은 동일 Docker 호스트에서 로컬로 빌드한 이미지를 배포합니다.
레지스트리가 준비되면 커밋별 태그 또는 digest를 사용합니다.

```bash
# 실제 레지스트리 주소가 준비된 뒤 사용합니다.
export TOKEN_TV_IMAGE=registry.example.com/home/token-tv:커밋태그
# 포크 디렉터리에서 빌드 후 업로드
docker compose build
docker compose push
# PRJ 배포 디렉터리에서 다운로드 후 재배포
docker compose pull
docker compose up -d --no-build
```

레지스트리 비밀번호와 토큰은 소스에 기록하지 않습니다. 인증은 배포 호스트와
향후 CI의 자격 증명 저장소에서 관리합니다. GHCR이나 Docker Hub에는 자동 업로드하지 않습니다.
