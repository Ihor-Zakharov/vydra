"""Заставка интерактивной консоли: чёрная дыра в духе KOCMOC UNLEASHED и название VYDRA.

Рисуется процедурно, той же геометрией, что шейдер веб-интерфейса: поле расстояний до центра дыры → яркость
(раскалённо-белое кольцо, ореол из тонких концентрических колец с мягким спадом, графитовый диск с краем
в две ступени, световая полоса горизонта снизу, редкие звёзды) → пиксели. Единственный цвет — тонкая
радужная кайма (хроматическая аберрация) по внешней кромке кольца.

Вывод — полублоки «▀»: у символа цвет букв — верхний пиксель, цвет фона — нижний, так пиксели выходят
квадратными. Режимы: truecolor → 256 цветов (серая шкала 232–255 и куб 6×6×6) → 16 цветов (четыре серых) →
без цвета (штриховка « ·░▒▓█» по яркости, без единого управляющего кода).

Показ — только при входе в интерактивный режим, только в терминале, высота ≈ четверть экрана (8–16 строк);
выключается `vydra settings --art off` или переменной VYDRA_NO_ART=1.
"""

from __future__ import annotations

import colorsys
import math
import os

MIN_ROWS, MAX_ROWS = 8, 16
MIN_COLS = 48  # уже — без заставки
MAX_COLS = 180
RIGHT_GAP = 0.03  # доля ширины окна справа без заставки
EDGE_FADE = 0.14  # на такой доле ширины у каждого края горизонт мягко гаснет
SHADES = "  ·░▒▓█"


# --- что и когда показывать -----------------------------------------------------------------


def enabled(prefs_value: bool | None) -> bool:
    """Показывать ли заставку: VYDRA_NO_ART перекрывает настройку, по умолчанию — да."""
    env = os.environ.get("VYDRA_NO_ART", "").strip().lower()
    if env and env not in ("0", "false", "no", "нет"):
        return False
    return prefs_value is not False


def size_for(cols: int, rows: int) -> tuple[int, int] | None:
    """(ширина, высота) заставки в символах или None — окно слишком маленькое."""
    if cols < MIN_COLS or rows < 16:
        return None
    # справа оставляем ~3 % окна пустыми: заставка не упирается в край (просьба пользователя по скриншоту)
    return min(cols - max(2, round(cols * RIGHT_GAP)), MAX_COLS), max(MIN_ROWS, min(MAX_ROWS, rows // 4))


def color_mode(color_system: str | None) -> str | None:
    """Режим rich (truecolor / 256 / standard / windows / None) → наш."""
    return {"truecolor": "truecolor", "256": "256", "standard": "16", "windows": "16"}.get(color_system or "")


# --- сцена ----------------------------------------------------------------------------------


def _smooth(e0: float, e1: float, x: float) -> float:
    t = min(1.0, max(0.0, (x - e0) / (e1 - e0)))
    return t * t * (3 - 2 * t)


def _hash(x: int, y: int) -> float:
    h = (x * 374761393 + y * 668265263) & 0xFFFFFFFF
    h = ((h ^ (h >> 13)) * 1274126177) & 0xFFFFFFFF
    return ((h ^ (h >> 16)) & 0xFFFF) / 65535.0


def _hsv(h: float, s: float, v: float) -> tuple[float, float, float]:
    i = int(h * 6) % 6
    f = h * 6 - int(h * 6)
    p, q, t = v * (1 - s), v * (1 - f * s), v * (1 - (1 - f) * s)
    return [(v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q)][i]


class Scene:
    """Поле цвета в «пикселях» (w × h, пиксель квадратный: половина символа по высоте)."""

    def __init__(self, w: int, h: int, title: bool = True):
        self.w, self.h = w, h
        self.r = max(4.5, min(h * 0.33, w * 0.12))  # радиус кольца
        self.cx = w * 0.72 if title else w * 0.5
        self.cy = h * 0.46
        self.horizon = h * 0.86
        self.core = max(0.75, self.r * 0.085)  # полуширина раскалённой кромки, px
        self.text = _Title(self, w, h) if title else None
        # то, что зависит только от строки или только от столбца, считаем один раз (укладываемся в ~30 мс)
        ey, eh = self.cy + self.r * 0.62, max(0.7, self.r * 0.07)
        self.rows = []
        for yi in range(h):
            y = yi + 0.5
            hz = math.exp(-(((y - self.horizon) / (h * 0.055)) ** 2))
            streak = 0.75 + 0.25 * math.sin(y * 2.7 + _hash(yi, 3) * 6.0)
            wide = 0.18 * math.exp(-(((y - self.horizon) / (h * 0.25)) ** 2)) * (y > self.horizon * 0.7)
            self.rows.append((hz * streak + wide, math.exp(-(((y - ey) / eh) ** 2))))
        self.cols = []
        for xi in range(w):
            dx = xi + 0.5 - self.cx
            under = 0.1 + 0.9 * math.exp(-((dx / (w * 0.2)) ** 2))
            fade = max(3.0, w * EDGE_FADE)  # концы полосы не обрываются, а гаснут к обоим краям
            under *= _smooth(0.0, fade, xi + 0.5) * _smooth(0.0, fade, w - xi - 0.5)
            spread = math.exp(-((dx / (self.r * 2.6)) ** 2)) * (0.6 + 0.4 * _hash(int((xi + 0.5) * 0.5), 7))
            self.cols.append((under, spread))
        self.step = max(1.6, self.r * 0.2)
        self.ring_w = max(0.45, self.r * 0.035)

    def pixel(self, xi: int, yi: int) -> tuple[float, float, float]:
        x, y = xi + 0.5, yi + 0.5
        dx, dy = x - self.cx, y - self.cy
        dist = math.hypot(dx, dy)
        off = dist - self.r  # расстояние до кромки кольца, px
        ang = math.atan2(-dy, dx) if abs(off) < max(6.0, self.r * 0.6) else 0.0  # 0 — вправо, π/2 — вверх

        # фон: почти чёрный, с едва заметной графитовой дымкой вокруг дыры
        lum = 0.012 + 0.05 * math.exp(-max(0.0, off) / (self.r * 1.4))
        # диск не мёртво-чёрный: графит, к краю светлеет в две ступени
        if off < 0:
            inner = dist / self.r
            lum = 0.07 + 0.04 * _smooth(0.45, 0.8, inner) + 0.07 * _smooth(0.82, 0.97, inner)
        # раскалённое кольцо: Доплер — левая сторона ярче правой
        if abs(off) < self.core * 4:
            doppler = 0.82 + 0.18 * math.cos(ang - math.pi * 0.85)
            lum += 1.25 * doppler * math.exp(-((off / self.core) ** 2))
        # ореол: мягкий спад и несколько тонких колец с убывающей яркостью
        if 0 < off < self.step * 6:
            lum += 0.34 * math.exp(-off / (self.r * 0.30))
            for n, amp in ((1, 0.20), (2, 0.12), (3, 0.07), (4, 0.04)):
                lum += amp * math.exp(-(((off - n * self.step) / self.ring_w) ** 2))
        # аккреционный диск ребром (тонкий светлый эллипс через низ дыры) и горизонт — широкая полоса света
        # снизу, ярче всего под дырой, с горизонтальными прожилками
        horizon, band = self.rows[yi]
        under, spread = self.cols[xi]
        lum += 0.55 * band * spread + horizon * under
        # редкие звёзды — вдали от кольца, горизонта и названия
        star = _hash(int(x), int(y))
        in_title = self.text is not None and self.text.covers(x, y)
        if star > 0.9935 and off > self.r * 0.7 and y < self.horizon - self.h * 0.12 and not in_title:
            lum += 0.12 + 0.35 * _hash(int(y), int(x))
        # название
        if self.text is not None:
            lum = max(lum, self.text.lum(x, y))

        gray = 1.0 - math.exp(-lum * 1.35)  # мягкая кривая: белое не «выгорает» резко
        r = g = b = gray
        # хроматическая аберрация: радужная кайма по внешней кромке, сверху-слева
        width = max(1.3, self.r * 0.1)
        fringe_off = off - self.core * 1.6 - width * 0.4
        if -width < fringe_off < width * 1.6:
            arc = _smooth(0.25, 1.1, ang) * (1 - _smooth(2.55, 3.3, ang))  # от ~20° до ~180°
            env = math.exp(-(((fringe_off - width * 0.3) / (width * 1.1)) ** 2)) * arc
            if env > 0.02:
                t = min(1.0, max(0.0, (fringe_off + width) / (width * 2.6)))
                hue = 0.7 * (1 - t) ** 1.25  # синий внутри → красный снаружи, как в объективе
                cr, cg, cb = _hsv(hue, 0.5, 1.0)
                k = 0.5 * env
                r, g, b = r * (1 - k) + cr * k, g * (1 - k) + cg * k, b * (1 - k) + cb * k
        return min(1.0, r), min(1.0, g), min(1.0, b)


class _Title:
    """VYDRA — строгие монументальные буквы из тонких линий (с мягким краем), широкая разрядка."""

    # буквы на сетке 0..1 × 0..1 (x вправо, y вниз): список отрезков
    GLYPHS = {
        "V": [((0, 0), (0.5, 1)), ((0.5, 1), (1, 0))],
        "Y": [((0, 0), (0.5, 0.5)), ((1, 0), (0.5, 0.5)), ((0.5, 0.5), (0.5, 1))],
        "D": [((0, 0), (0, 1)), ((0, 0), (0.62, 0)), ((0.62, 0), (1, 0.3)), ((1, 0.3), (1, 0.7)), ((1, 0.7), (0.62, 1)),
              ((0.62, 1), (0, 1))],
        "R": [((0, 0), (0, 1)), ((0, 0), (0.75, 0)), ((0.75, 0), (1, 0.22)), ((1, 0.22), (0.75, 0.46)),
              ((0.75, 0.46), (0, 0.46)), ((0.48, 0.46), (1, 1))],
        "A": [((0, 1), (0.5, 0)), ((0.5, 0), (1, 1)), ((0.24, 0.66), (0.76, 0.66))],
    }  # fmt: skip

    def __init__(self, scene: Scene, w: int, h: int):
        room = scene.cx - scene.r * 2.3 - w * 0.05  # место слева от ореола
        self.size = min(h * 0.34, room / 5.6)  # высота букв, px: ширина букв 0.84, разрядка 0.35
        self.ok = self.size >= 5
        self.x0 = w * 0.05 + max(0.0, (room - self.size * 5.6) * 0.35)
        self.y0 = scene.cy - self.size * 0.5
        self.stroke = max(0.55, self.size * 0.055)
        self.segments = []
        x = self.x0
        for ch in "VYDRA":
            for (ax, ay), (bx, by) in self.GLYPHS[ch]:
                self.segments.append((x + ax * self.size * 0.84, self.y0 + ay * self.size,
                                      x + bx * self.size * 0.84, self.y0 + by * self.size))  # fmt: skip
            x += self.size * (0.84 + 0.35)
        self.box = (self.x0 - 3, self.y0 - 3, x + 3, self.y0 + self.size + 3)

    def covers(self, x: float, y: float) -> bool:
        return self.ok and self.box[0] <= x <= self.box[2] and self.box[1] <= y <= self.box[3]

    def lum(self, x: float, y: float) -> float:
        if not self.covers(x, y):
            return 0.0
        d = min(_seg_dist(x, y, *s) for s in self.segments)
        body = 1 - _smooth(self.stroke * 0.5 - 0.1, self.stroke * 0.5 + 0.55, d)  # край — на пиксель мягкий
        glow = (0.1 if self.size >= 9 else 0.04) * math.exp(-d / 1.4)  # мелкие буквы — без ореола, чётче
        shade = 1.15 - 0.45 * ((y - self.y0) / self.size)  # серебро: сверху светлее
        return max(body * shade * 0.95, glow)


def _seg_dist(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    vx, vy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / (vx * vx + vy * vy or 1)))
    return math.hypot(px - ax - vx * t, py - ay - vy * t)


# --- вывод ----------------------------------------------------------------------------------


def render(cols: int, rows: int, mode: str | None) -> list[str]:
    """Строки заставки cols × rows символов. mode: truecolor | 256 | 16 | None (без цвета)."""
    w, h = cols, rows * 2
    scene = Scene(w, h)
    if scene.text is not None and not scene.text.ok:  # узко: без названия, дыра по центру
        scene = Scene(w, h, title=False)
    px = [[scene.pixel(x, y) for x in range(w)] for y in range(h)]
    lines = []
    for row in range(rows):
        top, bottom = px[row * 2], px[row * 2 + 1]
        if mode is None:
            line = "".join(_shade(t, b) for t, b in zip(top, bottom, strict=True))
        else:
            line = _cells(top, bottom, mode) + "\x1b[0m"
        lines.append(line)
    return lines


def _shade(top, bottom) -> str:
    lum = (sum(top) + sum(bottom)) / 6
    return SHADES[min(len(SHADES) - 1, int(lum ** 0.8 * len(SHADES)))]


CLEAR = 0.06  # темнее — «прозрачно»: фон самого терминала, чтобы заставка не была чёрным прямоугольником


def _clear(rgb) -> bool:
    return max(rgb) < CLEAR


def _cells(top, bottom, mode: str) -> str:
    out, last = [], None
    for t, b in zip(top, bottom, strict=True):
        clear_t, clear_b = _clear(t), _clear(b)
        if clear_t and clear_b:
            seq, ch = "\x1b[39;49m", " "
        elif clear_t:  # только нижняя половинка
            seq, ch = _code(b, mode, False)[0] + "\x1b[49m", "▄"
        elif clear_b:
            seq, ch = _code(t, mode, False)[0] + "\x1b[49m", "▀"
        else:
            fg, bg = _code(t, mode, False), _code(b, mode, True)
            seq, ch = (bg[0], " ") if fg[1] == bg[1] else (fg[0] + bg[0], "▀")
        out.append(seq + ch if seq != last else ch)
        last = seq
    return "".join(out)


def _code(rgb, mode: str, background: bool) -> tuple[str, tuple]:
    r, g, b = (max(0, min(255, round(c * 255))) for c in rgb)
    if mode == "truecolor":
        key = (r, g, b)
        return f"\x1b[{48 if background else 38};2;{r};{g};{b}m", key
    if mode == "256":
        index = _xterm256(r, g, b)
        return f"\x1b[{48 if background else 38};5;{index}m", (index,)
    gray = (r + g + b) / 3  # 16 цветов: чёрный, тёмно-серый, светло-серый, белый
    level = 0 if gray < 40 else 1 if gray < 110 else 2 if gray < 190 else 3
    code = [30, 90, 37, 97][level] + (10 if background else 0)
    return f"\x1b[{code}m", (level,)


def _xterm256(r: int, g: int, b: int) -> int:
    if max(r, g, b) - min(r, g, b) < 34:  # почти серое — точная шкала 232–255 (плюс чистые чёрный и белый)
        gray = (r + g + b) / 3
        if gray < 5:
            return 16
        if gray > 246:
            return 231
        return 232 + min(23, max(0, round((gray - 8) / 10)))
    # цветное здесь — только радужная кайма: берём мягкие пастельные ячейки куба по оттенку, а не ближайшие
    # по каналам (те выходят кислотно-зелёными)
    hue = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)[0]
    return min(_PASTELS, key=lambda item: min(abs(item[0] - hue), 1 - abs(item[0] - hue)))[1]


_PASTELS = [(0.0, 174), (0.08, 180), (0.15, 187), (0.33, 151), (0.5, 152), (0.6, 153), (0.75, 183)]
