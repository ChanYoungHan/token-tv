# 동일 서비스의 여러 계정 표시

한 TokenTV 인스턴스에서 설정 순서의 첫 3개 계정을 시계에 표시합니다.
예: `CLAUDE A / CODEX A / CODEX B` 또는 `CODEX A / CODEX B / CODEX C`.
각 행의 사용량·리셋 시각·로그인/OLD 상태는 해당 계정에 속하며 합산하지 않습니다.
웹에서는 기존 서비스 카드의 A/B/C 탭으로 계정을 전환하고 사용량 기록에서는 각 계정을 비교합니다.

![동일한 예시 데이터로 렌더링한 7개 시계 화면](images/multi-account-clock-faces.png)

위 그림은 예시 사용률 `24% / 57% / 83%`입니다. 모든 화면은 실제 렌더러의 240×240 출력이며,
Space의 두 Codex 캐릭터에도 `CODEX A`, `CODEX B` 이름을 붙여 구분합니다.

## 계정 추가

실행 중인 앱을 먼저 멈춘 뒤 다음을 실행합니다. CODEX A가 있으면 CODEX B가 새 전용 홈으로 추가됩니다.

```bash
token-tv add-account --provider codex --email second@example.com
token-tv connect --account codex_b
token-tv run
```

기존 계정은 다시 로그인할 필요가 없습니다. 이미 등록된 이메일은 거부하며, 다른 계정의 홈이나
이미 존재하는 새 슬롯의 홈을 재사용하지 않습니다. Docker 명령은 [배포 가이드](docker.ko.md#같은-서비스의-계정-추가-codex-b)를 참고하세요.

## 검증 범위

- 전체 테스트 111개 통과
- 설정에 CODEX B 추가 후 기존 계정 메타데이터·합성 로그인 파일·DB 마커 보존 검사
- 같은 서비스 3행, 설정 순서, 로그인 오류/OLD 행 유지, 4개 이상의 계정은 웹에 유지
- 7개 시계 렌더러의 크기·형식·전송 용량 검사
- Linux AMD64 이미지에서 Claude/Codex CLI, 로그인 없는 Codex RPC, 가상 시계 JPEG/GIF 업로드·활성화·복원 확인
- 퍼블릭 이미지 12개 레이어 검사: 실행 계정의 로그인 파일·DB·설정이 포함되지 않음

로컬 통합 확인은 새 전용 Docker 볼륨과 `http://127.0.0.1:8788/`을 사용합니다.
기존 A 계정은 예시 스냅샷으로 대체하고 CODEX B만 새 계정으로 로그인합니다.
시계 화면은 로컬 가상 시계와 대시보드 미리보기에서 검증하므로 운영 시계와 분리됩니다.
