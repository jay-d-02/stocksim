# 리눅스 서버 운영 안내

Oracle Cloud 무료 서버(우분투) + Tailscale Funnel 주소(`https://market-sim.<tailnet>.ts.net`)로 운영하는 방법.
비용 0원. 명령은 모두 서버의 `~/stocksim` 폴더에서 실행한다 (윈도우 PC에서 하는 단계는 따로 표시).

## 한눈에 보는 점검표

| | 할 일 | 언제 | 안 하면 |
|---|---|---|---|
| ★ | Tailscale 기기 키 만료 끄기 (5번) | 옮긴 직후 한 번 | 180일 뒤 주소 접속 끊김 |
| ★ | 백업을 서버 밖(구글 드라이브)으로 (6번) | 옮긴 직후 한 번 | 서버가 사라지면 데이터도 사라짐 |
| ★ | Oracle 계정을 유료로 업그레이드하지 않기 | 항상 | 한도를 넘으면 요금이 나감 |
| | 사용률 확인 (8번) | 옮기고 1주일 뒤 | 서버가 회수될 수 있음 |
| | 다운 알림 등록 (7번) | 옮긴 직후 한 번 | 사이트가 죽어도 모름 |
| | 백업 파일을 PC로 내려받기 | 한 달에 한 번 | |

## 1. 서버 만들기 (Oracle 콘솔)

- 가입할 때 **홈 리전을 서울(또는 춘천)**으로. 무료 서버는 홈 리전에만 만들 수 있고 나중에 못 바꾼다.
- Compute → Instances → Create instance
  - Image: **Ubuntu 24.04**
  - Shape: **VM.Standard.A1.Flex, 1 OCPU / 6GB** (무료 한도는 2 OCPU / 12GB. 작게 만드는 이유는 8번)
  - SSH 키: 새로 만들어 개인 키를 내려받아 둔다
- "Out of capacity"가 뜨면 시간을 두고 다시 시도.
- 포트는 열지 않는다. Tailscale은 서버에서 바깥으로 나가는 연결이라 SSH(22)만 있으면 된다.

## 2. 서버 기본 설정

```bash
ssh -i 개인키 ubuntu@서버IP
sudo timedatectl set-timezone Asia/Seoul        # 백업 시각·로그를 한국 시간으로
curl -fsSL https://get.docker.com | sudo sh     # Docker + compose (부팅 때 자동 시작)
sudo usermod -aG docker ubuntu
exit                                            # 다시 접속하면 sudo 없이 docker 사용
```

보안 업데이트는 우분투가 자동으로 설치한다 (`unattended-upgrades`).

## 3. 코드 올리기

윈도우 PC (PowerShell, 프로젝트 폴더에서):

```powershell
tar -czf stocksim.tgz --exclude=.git --exclude=legacy --exclude=__pycache__ --exclude=.pytest_cache --exclude=*.dump .
scp -i 개인키 stocksim.tgz ubuntu@서버IP:~
```

서버:

```bash
mkdir -p ~/stocksim && tar -xzf ~/stocksim.tgz -C ~/stocksim && cd ~/stocksim
sed -i 's/\r$//' .env          # 윈도우 줄바꿈 제거
nano .env                      # 아래 두 가지 확인
```

- `POSTGRES_PASSWORD`: 처음 켜기 전에 긴 무작위 값으로 바꾼다 (`openssl rand -base64 24`). DB를 처음 만들 때만 적용된다.
- `TS_AUTHKEY`: 5번에서 새로 만든 키로 바꾼다.

`.env`에는 비밀값이 들어 있으니 다른 곳에 올리지 않는다.

## 4. 데이터 옮기기 (회원·게임 기록·명예의 전당·뉴스)

윈도우 PC (PowerShell). `>`로 받으면 파일이 깨지므로 아래처럼:

```powershell
docker compose exec -T db sh -c 'pg_dump -U $POSTGRES_USER -d market_sim -Fc -f /tmp/stocksim.dump'
docker cp stocksim-db:/tmp/stocksim.dump .
scp -i 개인키 stocksim.dump ubuntu@서버IP:~/stocksim/
```

서버:

```bash
bash deploy/restore.sh stocksim.dump --yes
```

진행 중인 게임 상태(Redis)는 옮기지 않아도 된다. 앱이 DB의 복구 지점에서 다시 만든다.

## 5. 주소 넘기기 (Tailscale Funnel)

1. 윈도우 PC에서 `docker compose down` (같은 주소를 두 서버가 쓰지 않게).
2. [Tailscale 관리 화면](https://login.tailscale.com/admin/machines)에서 예전 `market-sim` 기기 삭제.
   지우지 않으면 새 서버가 `market-sim-1` 같은 다른 주소를 받는다.
3. Settings → Keys → Generate auth key 로 새 키를 만들어 서버 `.env`의 `TS_AUTHKEY`에 넣는다.
4. 서버에서:
   ```bash
   docker compose up -d --build          # .env 의 COMPOSE_PROFILES=funnel 로 tailscale 까지 함께 뜸
   docker compose ps                     # 모두 running / healthy 인지
   ```
5. **★ 관리 화면 → Machines → market-sim → ⋯ → Disable key expiry.**
   끄지 않으면 180일 뒤 기기 인증이 만료돼 주소 접속이 끊긴다.

## 6. 백업 자동화

매일 04:00에 DB를 백업한다. 서버 안에는 14일, 서버 밖(구글 드라이브)에는 60일 보관.

```bash
mkdir -p ~/stocksim-backup
bash deploy/backup.sh                 # 한 번 손으로 돌려 확인
crontab -e                            # 아래 한 줄 추가
0 4 * * * bash $HOME/stocksim/deploy/backup.sh >> $HOME/stocksim-backup/backup.log 2>&1
```

**★ 서버 밖으로 복사 (구글 드라이브, 무료 15GB):**

```bash
sudo apt install -y rclone
rclone config
#   n (새로) → 이름: gdrive → Storage: drive → client_id·secret 은 비움 → scope: 1 (전체)
#   → "Use web browser to automatically authenticate?" 에 n
#   → 화면에 나오는 `rclone authorize "drive" "…"` 명령을 브라우저가 있는 PC에서 실행
#     (PC에 rclone 설치 필요: https://rclone.org/downloads/) → 나온 토큰을 서버에 붙여 넣기
rclone mkdir gdrive:stocksim-backup
```

그다음 `.env`에 `RCLONE_REMOTE=gdrive:stocksim-backup`을 넣고 `bash deploy/backup.sh`로 확인한다.
`backup.log` 끝에 "서버 밖 복사 완료"가 보이면 된다.

## 7. 다운 알림 (UptimeRobot, 무료)

[uptimerobot.com](https://uptimerobot.com)에 가입하고 New monitor → HTTP(s) →
`https://market-sim.<tailnet>.ts.net/health` 를 5분 간격으로 등록하면 사이트가 죽을 때 메일이 온다.
`/health`는 DB와 Redis 상태까지 확인한다.

## 8. Oracle 회수 피하기

7일 동안 **CPU·네트워크·메모리 사용률이 모두 20% 미만**이면 Oracle이 무료 서버를 회수한다.
방문자가 적으면 CPU·네트워크는 20%를 넘기 어렵기 때문에 **메모리**로 조건에서 벗어난다.
이 앱은 메모리를 약 1.2~1.5GB 쓰므로 6GB 서버에서 20%를 넘는다.

옮기고 1주일 뒤 확인:

```bash
free -m      # used / total 이 20% 넘는지
```

Oracle 콘솔 → 인스턴스 → Metrics 에서도 볼 수 있다. 20%에 못 미치면 서버를 더 작게(메모리 4GB) 바꾼다.

## 9. 코드 업데이트

```bash
bash deploy/backup.sh                 # 먼저 백업
# 3번처럼 새 코드를 올려 풀고 (.env 는 덮어쓰지 않게 주의)
docker compose up -d --build          # DB 구조 변경은 앱이 시작할 때 자동 적용
```

## 10. 사고 났을 때 복구

```bash
ls ~/stocksim-backup                                   # 또는 rclone copy gdrive:stocksim-backup/파일 .
bash deploy/restore.sh ~/stocksim-backup/stocksim-YYYY-MM-DD-0400.dump --yes
```

서버가 통째로 사라졌으면 1~5번으로 새 서버를 만들고, 4번에서 구글 드라이브의 최신 백업을 복원한다.

## 자주 쓰는 명령

```bash
docker compose ps                     # 상태
docker compose logs -f app            # 앱 로그 (컨테이너마다 10MB × 3개까지만 보관)
docker compose restart app            # 앱만 다시 시작
docker compose exec app python -m scripts.fetch_news    # 실제 뉴스 지금 가져오기
df -h /                               # 디스크
```
