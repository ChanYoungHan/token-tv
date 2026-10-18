# Docker로 TokenTV 실행하기

컨테이너에 Python, Pillow, Claude Code CLI, Codex CLI, 작업 중인 TokenTV 소스를
설치합니다. TokenTV는 `pipx`의 독립 가상환경에 설치됩니다. 공식 CLI 버전은
Dockerfile에 고정되어 있습니다. `TOKEN_TV_CLAUDE_VERSION`과
`TOKEN_TV_CODEX_VERSION` 환경 변수로 빌드할 버전을 변경할 수 있습니다.

Docker Engine과 Compose가 필요합니다. Linux 서버와 macOS Docker Desktop에서
같은 명령을 사용합니다. 기본 Docker 네트워크로 컨테이너에서 시계 IP에 직접
HTTP 요청을 보냅니다. 서버에서 시계 IP에 접근할 수 있어야 합니다.

Docker 네트워크는 기본 `172.16.241.0/24`이며, 이미 사용 중인 대역이면
`TOKEN_TV_DOCKER_SUBNET`으로 변경합니다. 다른 Docker 네트워크가 시계의 LAN과
겹쳐도 통신이 막힙니다. 예를 들어 시계가 `192.168.0.7`일 때 Docker 네트워크
`192.168.0.0/20`이 있으면 해당 네트워크부터 겹치지 않는 대역으로 바꿔야 합니다.
`host` 네트워크로 바꾸는 것만으로는 이 충돌이 해결되지 않습니다.

다음 명령은 저장소 디렉터리에서 실행합니다.

```bash
docker compose build
docker compose run --rm --no-deps token-tv token-tv setup --isolate
```

사용할 계정의 이메일과 `http://<시계-IP>`를 입력합니다. `--isolate`로 생성한
계정 A의 CLI 홈은 컨테이너 환경 변수의 경로와 같습니다. 설정은
`/data/token-tv/config.json`, 계정은 `/data/token-tv/homes/`, 원래 시계 설정과
실행 상태는 `/data/token-tv/state/`에 보관합니다. `/data`는 이름 있는 볼륨에
저장되어 컨테이너 재생성 후에도 유지됩니다. 기존 설정이 있으면 `setup`은
덮어쓰지 않습니다.

계정별로 공식 CLI 로그인을 진행합니다. 코드와 URL은 로그인 터미널에서
다룹니다. Codex는 기본적으로 device auth를 사용합니다.

```bash
docker compose run --rm --no-deps token-tv token-tv connect --account claude_a
docker compose run --rm --no-deps token-tv token-tv connect --account codex_a
docker compose run --rm --no-deps token-tv token-tv doctor --live
docker compose up -d
docker compose ps
docker compose logs --tail=30 token-tv
```

대시보드는 `http://127.0.0.1:8787` 또는 내부망의 `http://<서버-IP>:8787`에서
열립니다. 별도의 웹 로그인 없이 사용하며 기본 바인딩은 `0.0.0.0`입니다.
호스트에서만 열려면 `TOKEN_TV_BIND_ADDRESS=127.0.0.1`로 실행합니다.
웹의 Clock display에서 화면을 고르고 Apply to clock을 누릅니다.

## 웹에서 만료된 계정 다시 연결

인증이 만료되면 계정 카드에 안내와 **다시 로그인** 버튼이 나타납니다.
정상 계정에서도 같은 버튼으로 로그인을 다시 연결할 수 있습니다.

1. 연결할 계정 카드의 **다시 로그인**을 누릅니다. 계정 이메일을 확인합니다.
2. **공식 로그인 페이지 열기**를 누르고 해당 이메일로 로그인합니다.
3. Claude는 공식 페이지가 보여주는 인증 코드 전체를 TokenTV 창에 붙여 넣고
   **인증 코드 확인**을 누릅니다. Codex는 TokenTV가 보여주는 기기 코드를
   공식 로그인 페이지에 입력합니다.
4. 서버가 CLI 로그인 결과와 계정 이메일을 확인합니다. 성공하면 사용량을
   즉시 조회하고 시계 이미지를 전송합니다. 5분을 기다리거나 재시작할 필요가 없습니다.

로그인 창을 닫아도 15분 동안 같은 계정 버튼으로 이어서 진행할 수 있습니다.
**로그인 취소**를 누르거나 15분이 지나면 해당 CLI 프로세스를 종료합니다.
다른 이메일로 로그인했거나 인증이 실패하면 다시 시작할 수 있습니다.

로그인 URL, 기기 코드, 입력한 일회용 코드는 메모리에서만 처리하고 완료 후
비웁니다. 비밀번호는 공식 페이지에서 입력하며, 실제 인증 정보 저장과 갱신은
공식 CLI가 Docker 볼륨에서 관리합니다. 웹 응답이나 로그로 토큰을 내보내지 않습니다.
로그인 중인 계정의 정기 조회는 잠시 멈추고 나머지 계정은 계속 갱신합니다.

웹 로그인 API는 다른 웹사이트에서 보내는 요청을 거부합니다. IP 주소,
localhost, `.local`/`.lan` 호스트 이름을 사용할 수 있습니다. 다른 내부 DNS 이름을
사용한다면 `TOKEN_TV_AUTH_HOSTS=서버이름`을 지정합니다.

API는 계정별로 세션을 하나만 유지합니다. `starting → waiting → verifying → succeeded`
순서로 진행하며 오류, 취소, 시간 초과는 각각 `failed`, `cancelled`, `expired`입니다.
새로고침이나 중복 클릭은 진행 중인 세션을 이어서 사용합니다.

| API | 역할 |
| --- | --- |
| `POST /auth/<계정>/start` | 공식 CLI 로그인 시작 또는 진행 중인 세션 반환 |
| `GET /auth/<계정>` | 상태, 공식 로그인 URL, 필요하면 기기 코드 확인 |
| `POST /auth/<계정>/code` | Claude 일회용 코드 전달. 현재 세션 ID 필요 |
| `POST /auth/<계정>/cancel` | 현재 세션 ID를 확인하고 로그인 중단 |

변경 요청은 JSON과 `X-TokenTV-Request: 1` 헤더를 사용합니다. API는 CLI의 원본
출력 대신 위 상태와 필요한 로그인 정보만 돌려주며, `/snapshot`에는 로그인 URL이나
코드를 포함하지 않습니다.

## 주간 사용량 기록

웹 대시보드에서 계정별 일별 막대와 수집 누적선을 확인할 수 있습니다.
기본 2주를 표시하고 그래프와 기간 선택기를 드래그해 범위를 바꿉니다.
SQLite DB는 `/data/token-tv/state/usage-history.sqlite3`에 생성되어 기존 볼륨에 유지됩니다.
첫 정상 관측을 기준점으로 삼으며, 기록 전 사용량은 소급하지 않습니다.
자세한 집계 기준과 운영 방법은 [일별 사용량 기록](usage-history.ko.md)을 참고하세요.

## 터미널에서 다시 연결

실행 중 계정 로그인을 다시 하려면 다음 명령을 사용합니다. TokenTV는 다음
조회 주기에 갱신된 로그인을 사용합니다.

```bash
docker compose exec token-tv token-tv connect --account claude_a
docker compose exec token-tv token-tv connect --account codex_a
```

일반 종료는 `docker compose down`입니다. `docker compose down -v`는 로그인,
설정, 원래 시계 설정 백업까지 삭제하므로 데이터를 폐기할 때만 사용합니다.

시계를 원래 상태로 되돌릴 때는 먼저 서비스 전송을 멈춥니다.

```bash
docker compose stop token-tv
docker compose run --rm --no-deps token-tv token-tv run --restore-display
```

계정 없이 이미지와 CLI 동작을 확인하는 명령입니다.

```bash
docker compose run --rm --no-deps token-tv claude --version
docker compose run --rm --no-deps token-tv codex --version
docker compose run --rm --no-deps token-tv token-tv --help
docker compose run --rm --no-deps token-tv token-tv demo --out /tmp/token-tv-demo
docker compose run --rm --no-deps -T \
  -v "$PWD/scripts/check_docker.py:/tmp/check_docker.py:ro" \
  token-tv /opt/pipx/venvs/token-tv/bin/python /tmp/check_docker.py
```

마지막 명령은 샘플 계정과 테스트용 시계 HTTP 서버로 대시보드, JPEG/GIF 생성,
업로드, 화면 활성화, 동일 이미지 재전송 방지, 복원을 확인합니다. 실제 계정과
시계에 요청하지 않습니다.

## 이 작업 환경에서 확인한 결과 (2026-10-13)

- Docker Desktop의 Linux ARM64 컨테이너에서 이미지 빌드 성공.
- Python 3.11.2, Pillow 12.3.0, TokenTV 0.2.0 설치.
- Claude Code 2.1.296, Codex CLI 0.162.1의 버전 및 로그인 도움말 실행 성공.
- Codex app-server의 JSON-RPC 초기화와 미로그인 계정 조회 성공.
- 기존 테스트 97개와 Docker 통합 검증 성공. 모든 화면 생성, 대시보드,
  JPEG/GIF 업로드, 표시 활성화, 같은 이미지 재전송 방지, 원래 설정 복원 확인.
- Compose 서비스와 HTTP health 정상. 계정 정보는 Docker 볼륨에 준비됨.
- 시계 LAN과 충돌하던 빈 `pp-test_default` 네트워크를 사용자 승인 후
  `192.168.0.0/20`에서 `172.16.240.0/24`로 재생성. 이름과 Compose 레이블 보존.
- 기본 Docker 네트워크에서 실제 시계 `192.168.0.7` 접속 성공.
  모델은 SmallTV-Ultra, 펌웨어는 Ultra-V9.0.51. 최초 `/app.json`은 HTTP 404였고,
  사용자가 Weather Clock Today 테마를 저장한 뒤 백업 및 업로드 성공.
- 실제 시계에 `tokentv.jpg` 22,986바이트 업로드(HTTP 200). 시계에서 다시 받은
  이미지가 로컬 렌더 결과와 SHA-256으로 일치하며 240×240 확인. 사진 테마 3,
  자동 재생 끄기, 대시보드의 전송 상태 `ok` 확인. 이때 계정은 미로그인 상태라
  사용량을 알 수 없는 화면을 표시함.
- 이 최초 검증 시점에는 실제 계정이 로그인 전(`auth_required`)이었음.

## 추가 운영 확인 (2026-10-16)

- 서버가 계속 실행 중이며 Docker health 및 시계 전송 정상.
- Codex device auth 로그인 완료 후 `Verification: ok`, `Account verified` 확인.
- Claude의 `OLD` 원인은 사용량 API의 HTTP 401. 공식 Claude CLI의 짧은 요청으로
  인증을 갱신하고 TokenTV 서비스를 재시작함.
- 재조회 결과 Claude와 Codex 모두 `status: ok`, `identity_verified: true`,
  오류 코드 없음. 두 값의 조회 시각이 30초 이내이며 시계 전송도 `ok` 확인.
- 웹 재로그인 기능 배포 후 기존 계정 2개와 시계 전송 정상 유지.
  내부망 주소 `http://192.168.0.4:8787/health` HTTP 200 확인.
- 배포 직후 시계 전송이 한 차례 실패해 현재 스타일로 재시도함. 최종 업로드는
  HTTP 200이며, 시계에서 받은 32,092바이트 JPEG가 서버 렌더 결과와 일치함.
- 테스트 104개 통과. 샘플 계정 브라우저에서 코드 제출, 기기 코드 안내,
  완료 후 사용량 갱신, 창 닫기와 이어하기, 취소를 확인.
- 실제 설치된 Claude/Codex CLI는 임시 홈에서 로그인 안내를 수신하고 취소하는
  단계까지 검증. 기존 실제 계정의 인증 정보는 테스트에 사용하지 않음.
