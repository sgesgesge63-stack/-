#!/usr/bin/env python3
"""앨범 정리 도우미: 중복/비슷한 사진과 스크린샷(캡처)을 찾아 정리 후보로 모읍니다.

바로 삭제하지 않습니다. 기본은 '미리보기'(리포트만 생성)이고,
--apply 를 주면 후보를 <앨범>/_정리대상/ 폴더로 옮깁니다. --undo 로 되돌릴 수 있습니다.

사용 예:
    python album_cleanup.py ~/Pictures/phone_backup            # 리포트만 생성
    python album_cleanup.py ~/Pictures/phone_backup --apply    # 후보를 _정리대상 으로 이동
    python album_cleanup.py ~/Pictures/phone_backup --undo     # 이동한 파일 원위치
"""

import argparse
import base64
import csv
import hashlib
import html
import io
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime

try:
    from PIL import Image, ImageFilter, ImageOps, ImageStat
except ImportError:
    sys.exit("Pillow 가 필요합니다: pip install pillow pillow-heif")

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:
    pillow_heif = None  # 아이폰 HEIC 사진은 pillow-heif 가 있어야 읽힙니다

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".bmp", ".gif"}
REVIEW_DIR = "_정리대상"
MOVE_LOG = "_move_log.json"

SCREENSHOT_NAME = re.compile(
    r"screenshot|screen[ _-]?shot|screen[ _-]?capture|스크린샷|캡처|캡쳐|screen_recording|kakaotalk_capture",
    re.IGNORECASE,
)
EXIF_MAKE, EXIF_MODEL, EXIF_DATETIME, EXIF_SOFTWARE = 0x010F, 0x0110, 0x0132, 0x0131
EXIF_DATETIME_ORIGINAL, EXIF_IFD = 0x9003, 0x8769


@dataclass
class Photo:
    path: str
    size: int
    width: int = 0
    height: int = 0
    taken: float = 0.0
    camera: str = ""
    software: str = ""
    sha256: str = ""
    dhash: int = 0
    sharpness: float = 0.0
    thumb: str = ""
    reasons: list = field(default_factory=list)

    @property
    def quality(self):
        return (self.width * self.height, self.sharpness, self.size)


def iter_images(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != REVIEW_DIR and not d.startswith(".")]
        for name in filenames:
            if os.path.splitext(name)[1].lower() in IMAGE_EXTS:
                yield os.path.join(dirpath, name)


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def dhash(img, size=8):
    """차이 해시(dHash): 크기·압축이 달라도 비슷한 사진은 비슷한 값이 나옵니다."""
    small = img.convert("L").resize((size + 1, size), Image.LANCZOS)
    px = small.tobytes()
    bits = 0
    for row in range(size):
        for col in range(size):
            left = px[row * (size + 1) + col]
            right = px[row * (size + 1) + col + 1]
            bits = (bits << 1) | (left > right)
    return bits


def parse_exif_time(value):
    try:
        return datetime.strptime(str(value).strip("\x00 "), "%Y:%m:%d %H:%M:%S").timestamp()
    except (ValueError, TypeError):
        return 0.0


def analyze(path):
    photo = Photo(path=path, size=os.path.getsize(path))
    photo.sha256 = file_sha256(path)
    with Image.open(path) as img:
        exif = img.getexif()
        photo.camera = f"{exif.get(EXIF_MAKE, '')} {exif.get(EXIF_MODEL, '')}".strip()
        photo.software = str(exif.get(EXIF_SOFTWARE, ""))
        photo.taken = parse_exif_time(exif.get_ifd(EXIF_IFD).get(EXIF_DATETIME_ORIGINAL)) or parse_exif_time(
            exif.get(EXIF_DATETIME)
        )
        img = ImageOps.exif_transpose(img)
        img.draft("RGB", (512, 512))  # JPEG 는 축소 디코딩으로 빠르게
        photo.width, photo.height = img.size
        work = img.convert("RGB")
        work.thumbnail((512, 512))
        photo.dhash = dhash(work)
        photo.sharpness = ImageStat.Stat(work.convert("L").filter(ImageFilter.FIND_EDGES)).var[0]
        work.thumbnail((160, 160))
        buf = io.BytesIO()
        work.save(buf, "JPEG", quality=70)
        photo.thumb = base64.b64encode(buf.getvalue()).decode()
    if not photo.taken:
        photo.taken = os.path.getmtime(path)
    return photo


def is_screenshot(photo):
    name = os.path.basename(photo.path)
    if SCREENSHOT_NAME.search(name) or SCREENSHOT_NAME.search(photo.path.replace(os.sep, "/")):
        return "파일명/폴더명이 스크린샷"
    if "screenshot" in photo.software.lower():
        return "EXIF 소프트웨어가 스크린샷"
    if not photo.camera and name.lower().endswith(".png"):
        return "카메라 정보 없는 PNG"
    return ""


class UnionFind:
    def __init__(self, n):
        self.parent = list(range(n))

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        self.parent[self.find(a)] = self.find(b)


def group_similar(photos, threshold, window_sec):
    """촬영 시각이 가까운(window_sec 이내) 사진끼리만 비교해 비슷한 묶음을 만듭니다."""
    order = sorted(range(len(photos)), key=lambda i: photos[i].taken)
    uf = UnionFind(len(photos))
    for pos, i in enumerate(order):
        for j in order[pos + 1 :]:
            if photos[j].taken - photos[i].taken > window_sec:
                break
            same_file = photos[i].sha256 == photos[j].sha256
            if same_file or (photos[i].dhash ^ photos[j].dhash).bit_count() <= threshold:
                uf.union(i, j)
    # 완전히 같은 파일은 촬영 시각과 상관없이 묶기
    by_hash = {}
    for i, p in enumerate(photos):
        if p.sha256 in by_hash:
            uf.union(i, by_hash[p.sha256])
        by_hash[p.sha256] = i
    groups = {}
    for i in range(len(photos)):
        groups.setdefault(uf.find(i), []).append(photos[i])
    return [g for g in groups.values() if len(g) > 1]


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def write_report(root, groups, screenshots, candidates, out_html, out_csv):
    total = sum(p.size for p, _ in candidates)
    parts = [
        "<!doctype html><meta charset=utf-8><title>앨범 정리 리포트</title>",
        "<style>body{font-family:sans-serif;margin:16px;max-width:1100px}"
        ".row{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0 20px}"
        "figure{margin:0;width:170px;font-size:12px;word-break:break-all}"
        "img{width:160px;height:160px;object-fit:cover;border:4px solid #3a3;border-radius:6px}"
        ".del img{border-color:#d33;opacity:.75}</style>",
        f"<h1>앨범 정리 리포트</h1><p>{html.escape(root)}<br>"
        f"정리 후보 <b>{len(candidates)}장</b>, 확보 가능 용량 <b>{human(total)}</b>. "
        "초록 테두리 = 남길 사진, 빨강 = 정리 후보</p>",
        f"<h2>비슷한/중복 사진 묶음 ({len(groups)}개)</h2>",
    ]

    def fig(p, cls, note):
        return (
            f"<figure class={cls}><img src='data:image/jpeg;base64,{p.thumb}'>"
            f"<figcaption>{html.escape(os.path.relpath(p.path, root))}<br>"
            f"{p.width}×{p.height}, {human(p.size)}<br>{html.escape(note)}</figcaption></figure>"
        )

    for g in groups:
        keep = max(g, key=lambda p: p.quality)
        parts.append("<div class=row>")
        parts += [fig(p, "keep" if p is keep else "del", "남김" if p is keep else "후보") for p in g]
        parts.append("</div>")
    parts.append(f"<h2>스크린샷/캡처 ({len(screenshots)}장)</h2><div class=row>")
    parts += [fig(p, "del", reason) for p, reason in screenshots]
    parts.append("</div>")
    with open(out_html, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["파일", "사유", "크기(bytes)", "해상도"])
        for p, reason in candidates:
            w.writerow([os.path.relpath(p.path, root), reason, p.size, f"{p.width}x{p.height}"])


def move_candidates(root, candidates):
    review = os.path.join(root, REVIEW_DIR)
    log = []
    for p, reason in candidates:
        sub = "스크린샷" if reason.startswith("스크린샷") else "비슷한사진"
        rel = os.path.relpath(p.path, root)
        dest = os.path.join(review, sub, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.move(p.path, dest)
        log.append({"from": p.path, "to": dest})
    with open(os.path.join(review, MOVE_LOG), "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=1)
    return review


def undo(root):
    log_path = os.path.join(root, REVIEW_DIR, MOVE_LOG)
    if not os.path.exists(log_path):
        sys.exit("되돌릴 기록이 없습니다.")
    with open(log_path, encoding="utf-8") as f:
        log = json.load(f)
    for entry in log:
        if os.path.exists(entry["to"]):
            os.makedirs(os.path.dirname(entry["from"]), exist_ok=True)
            shutil.move(entry["to"], entry["from"])
    os.remove(log_path)
    print(f"{len(log)}개 파일을 원래 위치로 되돌렸습니다.")


def main():
    ap = argparse.ArgumentParser(description="비슷한 사진·중복·스크린샷을 찾아 정리 후보로 모읍니다.")
    ap.add_argument("folder", help="사진 폴더 (폰에서 PC로 백업한 폴더 등)")
    ap.add_argument("--threshold", type=int, default=6, help="비슷함 기준(0~64, 작을수록 엄격). 기본 6")
    ap.add_argument("--window", type=int, default=10, help="비슷한 사진으로 볼 촬영 시각 차이(분). 기본 10")
    ap.add_argument("--no-screenshots", action="store_true", help="스크린샷은 후보에서 제외")
    ap.add_argument("--apply", action="store_true", help="후보를 _정리대상 폴더로 이동 (삭제 아님)")
    ap.add_argument("--undo", action="store_true", help="--apply 로 옮긴 파일을 원위치")
    args = ap.parse_args()

    root = os.path.abspath(os.path.expanduser(args.folder))
    if not os.path.isdir(root):
        sys.exit(f"폴더가 없습니다: {root}")
    if args.undo:
        return undo(root)
    if pillow_heif is None:
        print("참고: pillow-heif 가 없어 HEIC(아이폰) 사진은 건너뜁니다. pip install pillow-heif", file=sys.stderr)

    paths = list(iter_images(root))
    photos = []
    for n, path in enumerate(paths, 1):
        try:
            photos.append(analyze(path))
        except Exception as e:  # 깨진 파일 등은 건너뜀
            print(f"  건너뜀: {path} ({e})", file=sys.stderr)
        if n % 100 == 0 or n == len(paths):
            print(f"\r분석 중 {n}/{len(paths)}", end="", file=sys.stderr)
    print(file=sys.stderr)

    screenshots = []
    if not args.no_screenshots:
        screenshots = [(p, r) for p in photos if (r := is_screenshot(p))]
    shot_paths = {p.path for p, _ in screenshots}
    groups = group_similar([p for p in photos if p.path not in shot_paths], args.threshold, args.window * 60)

    candidates = [(p, f"스크린샷: {r}") for p, r in screenshots]
    for g in groups:
        keep = max(g, key=lambda p: p.quality)
        for p in g:
            if p is not keep:
                exact = p.sha256 == keep.sha256
                candidates.append((p, f"{'완전 중복' if exact else '비슷한 사진'} (남김: {os.path.basename(keep.path)})"))

    out_html = os.path.join(root, "앨범정리_리포트.html")
    out_csv = os.path.join(root, "앨범정리_후보목록.csv")
    write_report(root, groups, screenshots, candidates, out_html, out_csv)

    total = sum(p.size for p, _ in candidates)
    print(f"사진 {len(photos)}장 분석 → 비슷한 묶음 {len(groups)}개, 스크린샷 {len(screenshots)}장")
    print(f"정리 후보 {len(candidates)}장, 확보 가능 {human(total)}")
    print(f"리포트: {out_html}")
    if args.apply:
        review = move_candidates(root, candidates)
        print(f"후보를 {review} 로 옮겼습니다. 확인 후 직접 삭제하세요. (되돌리기: --undo)")
    else:
        print("리포트를 확인한 뒤 --apply 로 후보를 _정리대상 폴더로 옮길 수 있습니다.")


if __name__ == "__main__":
    main()
