from __future__ import annotations

import asyncio
import html
import json
import subprocess
import time
import urllib.request
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent

from playwright.async_api import async_playwright

import bangumi_archive
import bangumi_client as bgm


CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
PROFILE = ROOT / "work" / "chrome-r18-covers-profile"
PROGRESS_PATH = ROOT / "data" / "bangumi_r18_cover_sync_state.json"
LOCK_PATH = ROOT / "data" / "bangumi_r18_cover_sync.lock"
FALLBACK_COVER_DIR = ROOT / "static" / "r18_fallback_covers"
PORT = 9227
BATCH_SIZE = 320
CONCURRENCY = 32
MAX_PASSES = 3


def wait_for_cdp() -> None:
    endpoint = f"http://127.0.0.1:{PORT}/json/version"
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(endpoint, timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.25)
    raise RuntimeError("official Chrome CDP endpoint did not start")


def load_progress() -> dict:
    try:
        payload = json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        payload = {}
    return payload if isinstance(payload, dict) else {}


def save_progress(
    *, total: int, no_cover: set[int], missing_subject: set[int], failed: set[int]
) -> None:
    payload = {
        "version": 1,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "total": total,
        "official_no_cover": sorted(no_cover),
        "missing_subject": sorted(missing_subject),
        "failed": sorted(failed),
    }
    PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = PROGRESS_PATH.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(PROGRESS_PATH)


def _fallback_title_lines(value: str, width: int = 12, maximum: int = 5) -> list[str]:
    text = " ".join(str(value or "").split()) or "未命名条目"
    lines = [text[index : index + width] for index in range(0, len(text), width)]
    if len(lines) > maximum:
        lines = lines[:maximum]
        lines[-1] = lines[-1][:-1] + "…"
    return lines


def generate_fallback_covers(rows_by_id: dict[int, dict], subject_ids: set[int]) -> int:
    """Create labeled local covers only when Bangumi has no official image."""
    FALLBACK_COVER_DIR.mkdir(parents=True, exist_ok=True)
    palette = {
        1: ("#20172a", "#7e4d86", "书籍"),
        2: ("#281720", "#a14e70", "动画"),
        4: ("#14232b", "#3f8195", "游戏"),
    }
    written = 0
    for subject_id in sorted(subject_ids):
        row = rows_by_id.get(subject_id) or {}
        title = str(row.get("name_cn") or row.get("name") or f"Bangumi #{subject_id}")
        dark, accent, label = palette.get(int(row.get("type") or 0), ("#222128", "#777281", "条目"))
        title_nodes = "".join(
            f'<text x="300" y="{315 + index * 64}" text-anchor="middle" class="title">'
            f"{html.escape(line)}</text>"
            for index, line in enumerate(_fallback_title_lines(title))
        )
        svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="600" height="840" viewBox="0 0 600 840">
<defs>
  <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop stop-color="{dark}"/><stop offset="1" stop-color="#111216"/></linearGradient>
  <radialGradient id="glow"><stop stop-color="{accent}" stop-opacity=".72"/><stop offset="1" stop-color="{accent}" stop-opacity="0"/></radialGradient>
</defs>
<rect width="600" height="840" rx="22" fill="url(#bg)"/>
<circle cx="510" cy="120" r="240" fill="url(#glow)"/>
<circle cx="70" cy="760" r="220" fill="url(#glow)" opacity=".45"/>
<path d="M70 174h460M70 666h460" stroke="#fff" stroke-opacity=".16"/>
<text x="70" y="110" fill="#fff" fill-opacity=".64" font-size="24" font-family="Microsoft YaHei, sans-serif" letter-spacing="3">YANG·GUMI</text>
<text x="70" y="150" fill="#fff" font-size="20" font-family="Microsoft YaHei, sans-serif">{label} · BANGUMI #{subject_id}</text>
<style>.title{{fill:#fff;font-size:38px;font-weight:700;font-family:'Microsoft YaHei','Noto Sans CJK SC',sans-serif}}</style>
{title_nodes}
<text x="300" y="720" text-anchor="middle" fill="#fff" fill-opacity=".72" font-size="20" font-family="Microsoft YaHei, sans-serif">Bangumi 官方暂无封面</text>
<text x="300" y="758" text-anchor="middle" fill="#fff" fill-opacity=".42" font-size="17" font-family="Microsoft YaHei, sans-serif">本地标题补位封面</text>
</svg>'''
        path = FALLBACK_COVER_DIR / f"{subject_id}.svg"
        if not path.exists() or path.read_text(encoding="utf-8") != svg:
            path.write_text(svg, encoding="utf-8")
            written += 1
    return written


async def fetch_batch(page, token: str, subject_ids: list[int]) -> dict:
    return await page.evaluate(
        """
        async ({token, ids, concurrency}) => {
          const state = {next: 0, covers: {}, noCover: [], missing: [], failed: []};
          const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
          const coverOf = item => {
            const images = item?.images || {};
            const url = String(images.large || images.common || images.medium || images.small || images.grid || '');
            if (!url.includes('lain.bgm.tv/') || url.includes('no_icon_subject')) return '';
            return url.startsWith('//') ? `https:${url}` : url;
          };
          async function one(id) {
            let lastStatus = 0;
            for (let attempt = 0; attempt < 5; attempt += 1) {
              const controller = new AbortController();
              const timer = setTimeout(() => controller.abort(), 25000);
              try {
                const response = await fetch(`https://api.bgm.tv/v0/subjects/${id}`, {
                  headers: {Authorization: `Bearer ${token}`},
                  signal: controller.signal,
                });
                lastStatus = response.status;
                if (response.status === 200) {
                  const item = await response.json();
                  const cover = coverOf(item);
                  if (cover) state.covers[String(id)] = cover;
                  else state.noCover.push(id);
                  return;
                }
                if (response.status === 404) {
                  state.missing.push(id);
                  return;
                }
                if (response.status === 401 || response.status === 403) {
                  throw new Error(`AUTH_${response.status}`);
                }
                const retryAfter = Number(response.headers.get('Retry-After') || 0);
                await sleep(Math.max(retryAfter * 1000, 600 * (attempt + 1)));
              } catch (error) {
                if (String(error).includes('AUTH_')) throw error;
                await sleep(600 * (attempt + 1));
              } finally {
                clearTimeout(timer);
              }
            }
            state.failed.push({id, status: lastStatus});
          }
          async function worker() {
            while (true) {
              const index = state.next++;
              if (index >= ids.length) return;
              await one(ids[index]);
            }
          }
          await Promise.all(Array.from({length: concurrency}, worker));
          return state;
        }
        """,
        {"token": token, "ids": subject_ids, "concurrency": CONCURRENCY},
    )


async def fetch_fallback_batch(page, token: str, subject_ids: list[int]) -> dict:
    """Retry image-less API records through the official image and web pages."""
    return await page.evaluate(
        """
        async ({token, ids}) => {
          const covers = {};
          const unresolved = [];
          const normalize = value => {
            let url = String(value || '').trim();
            if (url.startsWith('//')) url = `https:${url}`;
            if (url.startsWith('/')) url = new URL(url, location.origin).href;
            if (!url.includes('lain.bgm.tv/') || url.includes('no_icon_subject')) return '';
            return url;
          };
          async function one(id) {
            try {
              const image = await fetch(
                `https://api.bgm.tv/v0/subjects/${id}/image?type=large`,
                {headers: {Authorization: `Bearer ${token}`}},
              );
              const redirected = normalize(image.url);
              if (image.ok && redirected) {
                covers[String(id)] = redirected;
                return;
              }
            } catch (_) {}
            try {
              const response = await fetch(`/subject/${id}`, {credentials: 'include'});
              if (response.ok) {
                const html = await response.text();
                const doc = new DOMParser().parseFromString(html, 'text/html');
                const image = doc.querySelector(
                  '#bangumiInfo img.cover, #bangumiInfo .cover img, img.cover, meta[property="og:image"]'
                );
                const url = normalize(image?.getAttribute('src') || image?.getAttribute('content'));
                if (url) {
                  covers[String(id)] = url;
                  return;
                }
              }
            } catch (_) {}
            unresolved.push(id);
          }
          for (let offset = 0; offset < ids.length; offset += 8) {
            await Promise.all(ids.slice(offset, offset + 8).map(one));
          }
          return {covers, unresolved};
        }
        """,
        {"token": token, "ids": subject_ids},
    )


async def run_sync() -> None:
    token, account = bgm.load_readonly_connection()
    if not token:
        raise RuntimeError("encrypted Bangumi connection is missing")
    if not CHROME.is_file():
        raise RuntimeError("official Google Chrome is missing")

    rows = bangumi_archive.archive_subjects()
    rows_by_id = {int(row["id"]): row for row in rows if int(row.get("id") or 0) > 0}
    archive_ids = set(rows_by_id)
    total = len(archive_ids)
    progress = load_progress()
    no_cover = {
        int(value) for value in progress.get("official_no_cover", []) if int(value) in archive_ids
    }
    missing_subject = {
        int(value) for value in progress.get("missing_subject", []) if int(value) in archive_ids
    }
    cached = bgm._load_cover_cache()
    covered_ids = {int(value) for value in cached if int(value) in archive_ids}
    queue = sorted(archive_ids - covered_ids - no_cover - missing_subject)

    print(
        f"START account={account.get('username', '')} total={total} "
        f"covered={len(covered_ids)} official_no_cover={len(no_cover)} "
        f"missing_subject={len(missing_subject)} remaining={len(queue)}",
        flush=True,
    )
    if not queue and not no_cover:
        print("COMPLETE nothing_remaining", flush=True)
        return

    PROFILE.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        [
            str(CHROME),
            "--headless=new",
            f"--remote-debugging-port={PORT}",
            f"--user-data-dir={PROFILE}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-gpu",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    failed: set[int] = set()
    started = time.monotonic()
    try:
        wait_for_cdp()
        async with async_playwright() as playwright:
            browser = await playwright.chromium.connect_over_cdp(
                f"http://127.0.0.1:{PORT}"
            )
            context = browser.contexts[0]
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto("https://bgm.tv/", wait_until="domcontentloaded", timeout=30_000)
            verification = await page.evaluate(
                """
                async token => {
                  const response = await fetch('https://api.bgm.tv/v0/me', {
                    headers: {Authorization: `Bearer ${token}`},
                  });
                  return response.status;
                }
                """,
                token,
            )
            if verification != 200:
                raise RuntimeError(f"Bangumi token verification failed: HTTP {verification}")

            pass_queue = queue
            for pass_number in range(1, MAX_PASSES + 1):
                if not pass_queue:
                    break
                next_pass: set[int] = set()
                for offset in range(0, len(pass_queue), BATCH_SIZE):
                    batch = pass_queue[offset : offset + BATCH_SIZE]
                    result = await fetch_batch(page, token, batch)
                    covers = {
                        int(subject_id): str(url)
                        for subject_id, url in (result.get("covers") or {}).items()
                    }
                    bgm._remember_cover_urls(covers)
                    covered_ids.update(covers)
                    no_cover.update(int(value) for value in result.get("noCover") or [])
                    missing_subject.update(int(value) for value in result.get("missing") or [])
                    batch_failed = {
                        int(item.get("id") or 0)
                        for item in result.get("failed") or []
                        if int(item.get("id") or 0) > 0
                    }
                    next_pass.update(batch_failed)
                    save_progress(
                        total=total,
                        no_cover=no_cover,
                        missing_subject=missing_subject,
                        failed=next_pass,
                    )
                    finished = (
                        len(covered_ids) + len(no_cover) + len(missing_subject)
                    )
                    elapsed = int(time.monotonic() - started)
                    print(
                        f"PROGRESS pass={pass_number} resolved={finished}/{total} "
                        f"covered={len(covered_ids)} official_no_cover={len(no_cover)} "
                        f"missing_subject={len(missing_subject)} retry={len(next_pass)} "
                        f"elapsed_s={elapsed}",
                        flush=True,
                    )
                pass_queue = sorted(next_pass)
                failed = next_pass

            fallback_ids = sorted(no_cover)
            for offset in range(0, len(fallback_ids), 40):
                batch = fallback_ids[offset : offset + 40]
                result = await fetch_fallback_batch(page, token, batch)
                covers = {
                    int(subject_id): str(url)
                    for subject_id, url in (result.get("covers") or {}).items()
                }
                bgm._remember_cover_urls(covers)
                covered_ids.update(covers)
                no_cover.difference_update(covers)
                save_progress(
                    total=total,
                    no_cover=no_cover,
                    missing_subject=missing_subject,
                    failed=failed,
                )
                print(
                    f"FALLBACK checked={min(offset + len(batch), len(fallback_ids))}/"
                    f"{len(fallback_ids)} recovered={len(fallback_ids) - len(no_cover)} "
                    f"remaining={len(no_cover)}",
                    flush=True,
                )
            await browser.close()
    finally:
        generated = generate_fallback_covers(rows_by_id, no_cover)
        if no_cover:
            print(
                f"LOCAL_FALLBACK total={len(no_cover)} written={generated}",
                flush=True,
            )
        save_progress(
            total=total,
            no_cover=no_cover,
            missing_subject=missing_subject,
            failed=failed,
        )
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()

    cached = bgm._load_cover_cache()
    covered_ids = {int(value) for value in cached if int(value) in archive_ids}
    unresolved = archive_ids - covered_ids - no_cover - missing_subject
    print(
        f"FINAL total={total} covered={len(covered_ids)} "
        f"official_no_cover={len(no_cover)} missing_subject={len(missing_subject)} "
        f"local_fallback={len(no_cover)} unresolved={len(unresolved)}",
        flush=True,
    )
    if unresolved:
        raise SystemExit(2)


def acquire_process_lock():
    import msvcrt

    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    handle = LOCK_PATH.open("a+b")
    if handle.tell() == 0:
        handle.write(b"\\0")
        handle.flush()
    handle.seek(0)
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        return None
    return handle


if __name__ == "__main__":
    process_lock = acquire_process_lock()
    if process_lock is None:
        raise SystemExit(0)
    try:
        asyncio.run(run_sync())
    finally:
        process_lock.close()
