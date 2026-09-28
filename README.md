# 앨범 정리 도우미

사진 폴더에서 **비슷한 사진(연속 촬영·흔들린 컷)**, **완전 중복**, **스크린샷/캡처**를 찾아 정리 후보로 모읍니다.
바로 지우지 않고, 리포트로 먼저 보여주고 → 원하면 `_정리대상` 폴더로 옮기고 → 되돌리기도 됩니다.

## 사용법

```bash
pip install -r requirements.txt

# 1) 미리보기: 앨범정리_리포트.html / 앨범정리_후보목록.csv 생성 (파일은 그대로)
python album_cleanup.py ~/Pictures/폰백업

# 2) 후보를 <폴더>/_정리대상/ 으로 이동 (삭제 아님)
python album_cleanup.py ~/Pictures/폰백업 --apply

# 되돌리기
python album_cleanup.py ~/Pictures/폰백업 --undo
```

`_정리대상` 폴더를 훑어보고 괜찮으면 그 폴더만 통째로 지우면 됩니다.

## 기준

| 분류 | 판단 방법 |
|---|---|
| 완전 중복 | 파일 내용(SHA-256)이 같음 |
| 비슷한 사진 | 촬영 시각이 가까운(기본 10분) 사진 중 이미지 해시(dHash) 차이가 작음. 묶음마다 해상도·선명도가 가장 좋은 1장을 남김 |
| 스크린샷 | 파일/폴더명에 Screenshot·스크린샷·캡처 등, EXIF 소프트웨어가 Screenshot, 또는 카메라 정보 없는 PNG |

## 옵션

- `--threshold N` 비슷함 기준 (기본 6, 작을수록 엄격. 너무 많이 묶이면 3~4, 덜 잡히면 8~10)
- `--window 분` 같은 장면으로 볼 촬영 시각 차이 (기본 10분)
- `--no-screenshots` 스크린샷은 후보에서 제외

아이폰 HEIC 사진은 `pillow-heif`가 설치돼 있어야 읽힙니다.

## 네이버 뉴스 → 카카오톡 알림

키워드 뉴스 모니터링 봇은 [news_monitor/README.md](news_monitor/README.md)를 보세요.
