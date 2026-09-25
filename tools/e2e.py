#!/usr/bin/env python3
"""выдра — живой e2e-прогон интерфейса через настоящий Edge (CDP) и настоящий сервер.

Гоняет реальные сценарии (живые ссылки на видео + маленький локальный клип для конвертера)
через UI выдры на сервере e2e (:8798 по умолчанию, изолированная библиотека — см. скилл dev-server)
и через Edge на CDP :9333 (скилл ui-rig). Не мокает API: это живой прогон, а не стенд состояний.

Запуск:
    uv run --with playwright python3 tools/e2e.py --label baseline
    uv run --with playwright python3 tools/e2e.py --label mech --compare .sprint/baseline/e2e.json

Вывод:
    .sprint/<label>/e2e.json — машиночитаемый список сценариев (name, ok, kind, detail, seconds)
    .sprint/<label>/e2e.md   — отчёт человеку, с разделением сбоев сети/сайта и сбоев интерфейса
Последняя строка вывода: `report: <путь к .md>` (успех) или сообщение об ошибке с ненулевым кодом выхода.

Сценарии: youtube_mp4, tiktok_mp3, instagram_both, clip_trim, open_reveal, library_move, converter.
Ссылки — как в tests/test_live.py (ролики NASA, общественное достояние).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_BASE_URL = "http://localhost:8798"
DEFAULT_CDP = "http://localhost:9333"

URLS = {
    "youtube": "https://www.youtube.com/shorts/fUrlyCjL8JA",
    "tiktok": "https://www.tiktok.com/@nasa/video/7686148096895569165",
    "instagram": "https://www.instagram.com/reel/DW-IhhKjdrC/",
}

# сбой распознаём как «сеть/сайт», если сообщение об ошибке задачи похоже на одно из этих —
# отличаем от сбоя интерфейса (не тот сценарий: элемент не найден, неверный расчёт и т. п.)
NETWORK_HINTS = (
    "сет", "network", "timeout", "соедин", "connection", "resolve", "недоступ",
    "cookie", "войти", "вход", "18+", "бот", "login", "sign in", "private", "закрыт",
    "видео недоступно", "unavailable", "geo", "регион", "not found", "404", "removed",
)


@dataclass
class Result:
    scenario: str
    ok: bool
    kind: str  # "ui" | "network" | "skip"
    detail: str
    seconds: float = 0.0


@dataclass
class Ctx:
    base_url: str
    page: object
    request: object
    work_dir: Path
    results: list = field(default_factory=list)


def looks_like_network_failure(message: str) -> bool:
    m = (message or "").lower()
    return any(h in m for h in NETWORK_HINTS)


async def api_get(ctx: Ctx, path: str) -> dict:
    r = await ctx.request.get(ctx.base_url + path, timeout=15000)
    return await r.json()


async def api_post(ctx: Ctx, path: str, **kw) -> tuple:
    r = await ctx.request.post(ctx.base_url + path, timeout=15000, **kw)
    try:
        data = await r.json()
    except Exception:
        data = None
    return r, data


async def wait_job(ctx: Ctx, predicate, timeout: float = 240.0):
    """Ждёт, пока в /api/jobs не найдётся задача, для которой predicate(job) вернёт статус done/error,
    или пока не выйдет время. Возвращает найденную задачу или None."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        jobs = await api_get(ctx, "/api/jobs")
        for j in jobs:
            if predicate(j):
                last = j
                if j["status"] in ("done", "error", "cancelled"):
                    return j
        await asyncio.sleep(2)
    return last


async def submit_via_ui(ctx: Ctx, url: str, mode: str, *, clip: tuple[str, str] | None = None):
    page = ctx.page
    await page.goto(ctx.base_url + "/?motion=0", wait_until="domcontentloaded")
    await page.wait_for_selector("#url", timeout=15000)
    await page.click(f'.pills.formats [data-value="{mode}"]')
    box = page.locator("#url")
    await box.fill(url)
    await page.wait_for_timeout(600)  # даёт onUrlInput/детекции платформы отработать
    if clip:
        start, end = clip
        # если превью загрузилось с длительностью, у него своя полоса отрезка (.pv-start/.pv-end) —
        # ручной ряд #clip прячется (updateTuners); иначе — ручной ввод в #clip-start/#clip-end.
        try:
            await page.wait_for_selector(".preview .pv-start", timeout=6000)
            await page.fill(".pv-start", start)
            await page.fill(".pv-end", end)
        except Exception:
            is_open = await page.locator("#controls").get_attribute("data-open")
            if is_open != "true":
                await page.click("#controls-toggle")
                await page.wait_for_selector("#clip-on", state="visible", timeout=5000)
            await page.check("#clip-on")
            await page.fill("#clip-start", start)
            await page.fill("#clip-end", end)
    before = {j["id"] for j in await api_get(ctx, "/api/jobs")}
    await page.click("#go")
    # находим новую задачу с этим source
    deadline = time.monotonic() + 20
    new_job = None
    while time.monotonic() < deadline:
        jobs = await api_get(ctx, "/api/jobs")
        cands = [j for j in jobs if j["id"] not in before and j.get("source") == url]
        if cands:
            new_job = cands[0]
            break
        await asyncio.sleep(1)
    return new_job


async def sc_download(ctx: Ctx, name: str, platform: str, mode: str, expect_types: list[str], *, clip=None):
    t0 = time.monotonic()
    url = URLS[platform]
    try:
        job = await submit_via_ui(ctx, url, mode, clip=clip)
        if not job:
            return Result(name, False, "ui", "Задача не появилась в очереди после клика «Скачать»", time.monotonic() - t0)
        job = await wait_job(ctx, lambda j: j["id"] == job["id"], timeout=300)
        if not job:
            return Result(name, False, "network", "Не дождались завершения задачи (таймаут 300 с)", time.monotonic() - t0)
        if job["status"] != "done":
            msg = job.get("error") or "неизвестная ошибка"
            kind = "network" if looks_like_network_failure(msg) else "ui"
            return Result(name, False, kind, f"Задача завершилась статусом «{job['status']}»: {msg}", time.monotonic() - t0)
        got_types = sorted(f["type"] for f in job.get("files", []))
        if got_types != sorted(expect_types):
            return Result(name, False, "ui", f"Ожидались файлы {expect_types}, получены {got_types}", time.monotonic() - t0)
        if clip and not job.get("clip"):
            return Result(name, False, "ui", "Отрезок не сохранился в задаче (job.clip пуст)", time.monotonic() - t0)
        return Result(name, True, "ui", f"Готово: {got_types}, {job.get('title') or job['source']}", time.monotonic() - t0)
    except Exception as exc:  # noqa: BLE001
        return Result(name, False, "ui", f"Исключение сценария: {exc}", time.monotonic() - t0)


async def sc_open_reveal(ctx: Ctx):
    """Проверяем API «Открыть» / «Показать в папке» — в headless Проводник не виден, смотрим на ответ."""
    t0 = time.monotonic()
    try:
        lib = await api_get(ctx, "/api/library")
        items = lib.get("items") or []
        if not items:
            return Result("open_reveal", False, "skip", "В библиотеке нет файлов — сценарии загрузок должны были её наполнить", time.monotonic() - t0)
        item_id = items[0]["id"]
        r_open, d_open = await api_post(ctx, f"/api/library/{item_id}/open")
        r_reveal, d_reveal = await api_post(ctx, f"/api/library/{item_id}/reveal")
        ok = r_open.status < 300 and r_reveal.status < 300 and (d_open or {}).get("ok") and (d_reveal or {}).get("ok")
        detail = f"open→{r_open.status} {d_open}; reveal→{r_reveal.status} {d_reveal} (Проводник не виден в headless, проверен только ответ API)"
        return Result("open_reveal", bool(ok), "ui", detail, time.monotonic() - t0)
    except Exception as exc:  # noqa: BLE001
        return Result("open_reveal", False, "ui", f"Исключение: {exc}", time.monotonic() - t0)


async def sc_library_move(ctx: Ctx):
    """Смена папки хранилища с переносом уже скачанного — через настройки в UI."""
    t0 = time.monotonic()
    page = ctx.page
    try:
        info_before = await api_get(ctx, "/api/settings")
        if info_before.get("library", {}).get("fixed"):
            r409, _ = await api_post(ctx, "/api/settings/library", data=json.dumps({"path": str(ctx.work_dir)}), headers={"Content-Type": "application/json"})
            return Result(
                "library_move", r409.status == 409, "skip",
                f"На этом сервере папка задана VD_LIBRARY_DIR (см. скилл dev-server) — из интерфейса не меняется. "
                f"Проверено через API: POST /api/settings/library → {r409.status} (ожидался 409).",
                time.monotonic() - t0,
            )
        old_path = info_before.get("library", {}).get("path")
        stats_before = await api_get(ctx, "/api/library")
        old_count = stats_before.get("stats", {}).get("count", 0)
        if old_count == 0:
            return Result("library_move", False, "skip", "Библиотека пуста — переносить нечего (запусти после сценариев загрузки)", time.monotonic() - t0)
        new_dir = ctx.work_dir / "e2e-lib-moved"
        new_dir.mkdir(parents=True, exist_ok=True)
        await page.goto(ctx.base_url + "/?motion=0", wait_until="domcontentloaded")
        await page.click("#open-settings")
        await page.wait_for_selector("#path-input", state="visible", timeout=10000)
        await page.fill("#path-input", str(new_dir))
        await page.click('#path-form button[type="submit"]')
        try:
            await page.wait_for_selector("#confirm-dialog[open]", timeout=8000)
            await page.click("#confirm-yes")
        except Exception:
            pass  # если библиотека была пуста, диалога переноса не будет
        await page.wait_for_timeout(1500)
        moved = list(new_dir.rglob("*"))
        moved_files = [p for p in moved if p.is_file() and not p.name.startswith(".")]
        info_after = await api_get(ctx, "/api/settings")
        new_path = info_after.get("library", {}).get("path")
        ok = new_path == str(new_dir) and len(moved_files) >= old_count
        detail = f"было {old_count} файлов в «{old_path}», теперь {len(moved_files)} в «{new_path}»"
        # возвращаем библиотеку на место, чтобы не путать следующий прогон
        await api_post(ctx, "/api/settings/library", data=json.dumps({"path": old_path, "move": True}), headers={"Content-Type": "application/json"})
        return Result("library_move", ok, "ui", detail, time.monotonic() - t0)
    except Exception as exc:  # noqa: BLE001
        return Result("library_move", False, "ui", f"Исключение: {exc}", time.monotonic() - t0)


def make_test_clip(dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=15:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p", str(dest)],
        check=True, capture_output=True, timeout=30,
    )
    return dest


async def sc_converter(ctx: Ctx):
    t0 = time.monotonic()
    page = ctx.page
    try:
        clip_path = ctx.work_dir / "e2e-clip.mp4"
        try:
            make_test_clip(clip_path)
        except Exception as exc:  # noqa: BLE001
            return Result("converter", False, "skip", f"Не смог собрать тестовый клип ffmpeg: {exc}", time.monotonic() - t0)
        await page.goto(ctx.base_url + "/?motion=0&tab=file", wait_until="domcontentloaded")
        await page.wait_for_selector("#file-input", timeout=10000)
        before = {j["id"] for j in await api_get(ctx, "/api/jobs")}
        data = clip_path.read_bytes()
        await page.set_input_files("#file-input", {"name": "e2e-clip.mp4", "mimeType": "video/mp4", "buffer": data})
        deadline = time.monotonic() + 20
        new_job = None
        while time.monotonic() < deadline:
            jobs = await api_get(ctx, "/api/jobs")
            cands = [j for j in jobs if j["id"] not in before and j.get("kind") == "file"]
            if cands:
                new_job = cands[0]
                break
            await asyncio.sleep(1)
        if not new_job:
            return Result("converter", False, "ui", "Файл выбран, но задача конвертации не появилась в очереди", time.monotonic() - t0)
        job = await wait_job(ctx, lambda j: j["id"] == new_job["id"], timeout=120)
        if not job or job["status"] != "done":
            msg = (job or {}).get("error") or "таймаут"
            return Result("converter", False, "ui", f"Конвертация не завершилась: {msg}", time.monotonic() - t0)
        return Result("converter", True, "ui", f"Сконвертировано: {[f['type'] for f in job.get('files', [])]}", time.monotonic() - t0)
    except Exception as exc:  # noqa: BLE001
        return Result("converter", False, "ui", f"Исключение: {exc}", time.monotonic() - t0)


async def run_all(base_url: str, cdp: str, work_dir: Path, only: list[str] | None) -> list[Result]:
    from playwright.async_api import async_playwright

    scenarios = {
        "youtube_mp4": lambda ctx: sc_download(ctx, "youtube_mp4", "youtube", "mp4", ["mp4"]),
        "tiktok_mp3": lambda ctx: sc_download(ctx, "tiktok_mp3", "tiktok", "mp3", ["mp3"]),
        "instagram_both": lambda ctx: sc_download(ctx, "instagram_both", "instagram", "both", ["mp4", "mp3"]),
        "clip_trim": lambda ctx: sc_download(ctx, "clip_trim", "youtube", "mp4", ["mp4"], clip=("0:00", "0:05")),
        "open_reveal": sc_open_reveal,
        "library_move": sc_library_move,
        "converter": sc_converter,
    }
    names = only or list(scenarios)
    results: list[Result] = []
    async with async_playwright() as p:
        try:
            browser = await asyncio.wait_for(p.chromium.connect_over_cdp(cdp), timeout=10)
        except Exception as exc:  # noqa: BLE001
            print(f"error: не смог подключиться к Edge по CDP {cdp}: {exc}", file=sys.stderr)
            sys.exit(2)
        context = await browser.new_context(
            viewport={"width": 1536, "height": 830}, color_scheme="dark", service_workers="block",
        )
        page = await context.new_page()
        request = context.request
        ctx = Ctx(base_url=base_url, page=page, request=request, work_dir=work_dir)
        for name in names:
            fn = scenarios.get(name)
            if not fn:
                results.append(Result(name, False, "skip", "неизвестный сценарий", 0.0))
                continue
            print(f"… {name}", file=sys.stderr)
            res = await fn(ctx)
            print(f"  {'ok' if res.ok else 'FAIL'} ({res.kind}) {res.detail}", file=sys.stderr)
            results.append(res)
        await context.close()
    return results


def write_report(results: list[Result], out_dir: Path, base_url: str, compare_path: Path | None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    js = out_dir / "e2e.json"
    js.write_text(json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [f"# e2e — {out_dir.name}", "", f"Сервер: `{base_url}` · {time.strftime('%Y-%m-%d %H:%M:%S')}", ""]
    ok_n = sum(1 for r in results if r.ok)
    lines.append(f"**{ok_n}/{len(results)} сценариев прошли.**")
    lines.append("")
    lines.append("| сценарий | статус | причина | вид сбоя | время |")
    lines.append("|---|---|---|---|---|")
    for r in results:
        status = "OK" if r.ok else "СБОЙ"
        kind = {"ui": "интерфейс", "network": "сеть/сайт", "skip": "пропущен"}.get(r.kind, r.kind)
        lines.append(f"| {r.scenario} | {status} | {r.detail} | {kind} | {r.seconds:.1f}с |")

    if compare_path and compare_path.exists():
        base = {x["scenario"]: x for x in json.loads(compare_path.read_text(encoding="utf-8"))}
        lines += ["", f"## Сравнение с `{compare_path}`", "", "| сценарий | было | стало |", "|---|---|---|"]
        for r in results:
            b = base.get(r.scenario)
            was = ("OK" if b["ok"] else "СБОЙ") if b else "—"
            now = "OK" if r.ok else "СБОЙ"
            mark = "" if was == now else " ⚠ изменилось"
            lines.append(f"| {r.scenario} | {was} | {now}{mark} |")

    md = out_dir / "e2e.md"
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--label", required=True, help="метка прогона — пишет в .sprint/<label>/")
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"сервер e2e (по умолчанию {DEFAULT_BASE_URL})")
    ap.add_argument("--cdp", default=DEFAULT_CDP, help=f"адрес Edge CDP (по умолчанию {DEFAULT_CDP})")
    ap.add_argument("--only", help="через запятую: только эти сценарии")
    ap.add_argument("--compare", help="путь к другому e2e.json — сравнить статусы")
    args = ap.parse_args()

    out_dir = REPO / ".sprint" / args.label
    work_dir = REPO / ".sprint" / f"{args.label}-work"
    only = [s.strip() for s in args.only.split(",")] if args.only else None
    compare_path = Path(args.compare) if args.compare else None

    try:
        results = asyncio.run(run_all(args.base_url, args.cdp, work_dir, only))
    except KeyboardInterrupt:
        print("error: прервано пользователем", file=sys.stderr)
        sys.exit(130)

    md_path = write_report(results, out_dir, args.base_url, compare_path)
    failed = [r for r in results if not r.ok and r.kind != "skip"]
    if failed:
        print(f"error: {len(failed)} сценариев провалились — смотри {md_path}", file=sys.stderr)
        print(f"report: {md_path}")
        sys.exit(1)
    print(f"report: {md_path}")


if __name__ == "__main__":
    main()
