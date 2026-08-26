"""Generate the public README demo from fully synthetic screenshots.

The source screenshots are produced by ``capture_user_guide_screenshots.py``
with the repository's synthetic demo dataset. This script never reads the
candidate store, resume files, browser profile, API configuration, or backups.
"""

from __future__ import annotations

import math
import shutil
import subprocess as _subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from subprocess_utils import hidden_subprocess


subprocess = hidden_subprocess(_subprocess)

WIDTH = 1600
HEIGHT = 900
FPS = 12
TRANSITION_SECONDS = 0.65
ASSET_DIR = ROOT / ".github" / "assets"
SCREENSHOT_DIR = ROOT / "docs" / "assets" / "user-guide"
VIDEO_PATH = ASSET_DIR / "product-demo.mp4"
POSTER_PATH = ASSET_DIR / "product-demo-poster.png"
PREVIEW_GIF_PATH = ASSET_DIR / "product-demo-preview.gif"
GITHUB_GIF_LIMIT_BYTES = 10 * 1024 * 1024

NAVY = "#07111f"
SLATE = "#0f1c2e"
PANEL = "#12243a"
WHITE = "#f8fafc"
MUTED = "#a8b4c6"
BLUE = "#3b82f6"
CYAN = "#22d3ee"
GREEN = "#34d399"
AMBER = "#fbbf24"


@dataclass(frozen=True)
class Scene:
    """One deterministic segment of the public demo."""

    title: str
    subtitle: str
    duration: float
    screenshot: str | None = None
    accent: str = CYAN
    kind: str = "step"


SCENES = (
    Scene(
        "40 秒看懂完整招聘处理流程",
        "岗位配置 → 获取候选人 → 筛选 → 复核 → 联系跟进",
        5.0,
        "01-home.png",
        CYAN,
        "cover",
    ),
    Scene(
        "1. 把岗位标准写清楚",
        "硬条件、评分权重与沟通策略集中配置",
        5.5,
        "02-job-config-full.png",
        BLUE,
    ),
    Scene(
        "2. 获取候选人并执行筛选",
        "浏览器状态、规则进度与异常在同一页可见",
        5.5,
        "04-run-full.png",
        CYAN,
    ),
    Scene(
        "3. 先看可解释结果",
        "分数、命中证据和淘汰原因可追溯",
        6.0,
        "05-results.png",
        GREEN,
    ),
    Scene(
        "4. 人工复核再做判断",
        "对临界候选人补充反馈，修正规则偏差",
        6.0,
        "11-review-workbench.png",
        "#a78bfa",
    ),
    Scene(
        "5. 联系、暂停与跟进",
        "发送前复核；结果不明确时进入待核实",
        6.0,
        "12-contact-workbench.png",
        AMBER,
    ),
    Scene(
        "从扫描到复盘，一条可核验的招聘工作流",
        "默认本机处理 · AI 可选 · 发送前人工确认",
        4.5,
        None,
        CYAN,
        "end",
    ),
)


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Load a Chinese-capable system font without introducing a dependency."""
    candidates = (
        Path("C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("/System/Library/Fonts/PingFang.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    )
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size, index=0)
    raise RuntimeError("No Chinese-capable system font was found")


def _gradient_background() -> Image.Image:
    """Build the shared dark background used by every scene."""
    image = Image.new("RGB", (WIDTH, HEIGHT), NAVY)
    pixels = image.load()
    for y in range(HEIGHT):
        for x in range(WIDTH):
            blue_glow = max(0.0, 1.0 - math.dist((x, y), (1_350, 50)) / 1_100)
            cyan_glow = max(0.0, 1.0 - math.dist((x, y), (250, 850)) / 1_000)
            pixels[x, y] = (
                int(7 + blue_glow * 7),
                int(17 + blue_glow * 12 + cyan_glow * 3),
                int(31 + blue_glow * 25 + cyan_glow * 12),
            )
    return image


def _draw_brand(draw: ImageDraw.ImageDraw) -> None:
    """Draw the compact product identifier used across the demo."""
    draw.rounded_rectangle((70, 52, 128, 110), radius=15, fill=BLUE)
    draw.text((99, 81), "B", font=_font(30, bold=True), fill=WHITE, anchor="mm")
    draw.text((150, 81), "BOSS 简历筛选器", font=_font(26, bold=True), fill=WHITE, anchor="lm")
    draw.text((1_530, 81), "全程合成数据", font=_font(22), fill=MUTED, anchor="rm")


def _load_screenshot(name: str) -> Image.Image:
    """Load one allow-listed synthetic screenshot."""
    source = SCREENSHOT_DIR / name
    if source.parent != SCREENSHOT_DIR or not source.is_file():
        raise FileNotFoundError(f"Synthetic screenshot not found: {source}")
    return Image.open(source).convert("RGB")


def _rounded_screenshot(source: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Fit a screenshot into a bordered rounded viewport."""
    fitted = ImageOps.fit(source, size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=20, fill=255)
    layer = Image.new("RGB", size, PANEL)
    layer.paste(fitted, mask=mask)
    return layer


def _draw_progress(draw: ImageDraw.ImageDraw, active_index: int, accent: str) -> None:
    """Show progress through the five user-facing workflow steps."""
    start_x = 1_260
    for index in range(5):
        x = start_x + index * 54
        fill = accent if index == active_index else "#334155"
        draw.rounded_rectangle((x, 128, x + 34, 136), radius=4, fill=fill)


def _build_step(scene: Scene, step_index: int) -> Image.Image:
    """Render a screenshot-led workflow scene."""
    image = _gradient_background()
    draw = ImageDraw.Draw(image)
    _draw_brand(draw)
    draw.text((92, 157), scene.title, font=_font(44, bold=True), fill=WHITE, anchor="lm")
    draw.text((92, 211), scene.subtitle, font=_font(25), fill=MUTED, anchor="lm")
    _draw_progress(draw, step_index, scene.accent)

    viewport = (92, 256, 1_508, 832)
    draw.rounded_rectangle(
        (viewport[0] - 4, viewport[1] - 4, viewport[2] + 4, viewport[3] + 4),
        radius=24,
        fill=scene.accent,
    )
    screenshot = _rounded_screenshot(
        _load_screenshot(scene.screenshot or ""),
        (viewport[2] - viewport[0], viewport[3] - viewport[1]),
    )
    image.paste(screenshot, (viewport[0], viewport[1]))

    draw.rounded_rectangle((92, 847, 425, 878), radius=15, fill="#172a42")
    draw.ellipse((108, 857, 118, 867), fill=scene.accent)
    draw.text((132, 862), "公开演示不含真实候选人信息", font=_font(18), fill=MUTED, anchor="lm")
    return image


def _build_cover(scene: Scene) -> Image.Image:
    """Render the opening poster and first video scene."""
    screenshot = _load_screenshot(scene.screenshot or "")
    screenshot = ImageOps.fit(screenshot, (WIDTH, HEIGHT), Image.Resampling.LANCZOS)
    screenshot = ImageEnhance.Contrast(screenshot).enhance(0.82).filter(ImageFilter.GaussianBlur(1.3))
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (2, 10, 23, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    for x in range(WIDTH):
        alpha = int(225 - 80 * (x / WIDTH))
        overlay_draw.line((x, 0, x, HEIGHT), fill=(2, 10, 23, alpha))
    image = Image.alpha_composite(screenshot.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(image)
    _draw_brand(draw)
    draw.rounded_rectangle((92, 241, 360, 283), radius=21, fill="#0e7490")
    draw.text((226, 262), "40 秒产品演示", font=_font(21, bold=True), fill=WHITE, anchor="mm")
    draw.text((92, 350), scene.title, font=_font(62, bold=True), fill=WHITE, anchor="lm")
    draw.text((92, 430), scene.subtitle, font=_font(30), fill="#d6e1ef", anchor="lm")
    draw.rounded_rectangle((92, 505, 500, 559), radius=27, fill=BLUE)
    draw.text((296, 532), "岗位规则与人工判断共同把关", font=_font(23, bold=True), fill=WHITE, anchor="mm")
    draw.rounded_rectangle((92, 732, 535, 786), radius=27, fill="#132c40")
    draw.ellipse((116, 751, 130, 765), fill=GREEN)
    draw.text((150, 759), "全程使用合成数据，无真实候选人信息", font=_font(21), fill="#d6e1ef", anchor="lm")
    return image


def _build_end(scene: Scene) -> Image.Image:
    """Render the closing local-first trust message."""
    image = _gradient_background()
    draw = ImageDraw.Draw(image)
    _draw_brand(draw)
    draw.text((800, 236), scene.title, font=_font(49, bold=True), fill=WHITE, anchor="mm")
    draw.text((800, 304), scene.subtitle, font=_font(27), fill=MUTED, anchor="mm")

    cards = (
        ("本地优先", "解析、筛选、候选人状态", CYAN),
        ("AI 可选", "开启后才按界面范围发送", "#a78bfa"),
        ("人工确认", "联系结果不明确时立即停下", GREEN),
    )
    for index, (title, body, accent) in enumerate(cards):
        left = 92 + index * 500
        right = left + 448
        draw.rounded_rectangle((left, 400, right, 625), radius=24, fill="#102238", outline="#29405c", width=2)
        draw.rounded_rectangle((left + 30, 432, left + 90, 492), radius=16, fill=accent)
        draw.text((left + 60, 462), str(index + 1), font=_font(28, bold=True), fill=NAVY, anchor="mm")
        draw.text((left + 30, 535), title, font=_font(31, bold=True), fill=WHITE, anchor="lm")
        draw.text((left + 30, 585), body, font=_font(21), fill=MUTED, anchor="lm")

    draw.rounded_rectangle((545, 716, 1_055, 776), radius=30, fill=BLUE)
    draw.text((800, 746), "查看 README，了解安装、边界与完整功能", font=_font(23, bold=True), fill=WHITE, anchor="mm")
    draw.text((800, 835), "github.com/yaoyouzhong/boss-resume-filter", font=_font(20), fill="#7dd3fc", anchor="mm")
    return image


def _build_scenes() -> list[Image.Image]:
    """Render all static scene masters before encoding."""
    rendered: list[Image.Image] = []
    step_index = 0
    for scene in SCENES:
        if scene.kind == "cover":
            rendered.append(_build_cover(scene))
        elif scene.kind == "end":
            rendered.append(_build_end(scene))
        else:
            rendered.append(_build_step(scene, step_index))
            step_index += 1
    return rendered


def _animate(image: Image.Image, progress: float) -> Image.Image:
    """Apply a restrained camera push without obscuring UI details."""
    scale = 1.0 + 0.012 * progress
    resized = image.resize(
        (round(WIDTH * scale), round(HEIGHT * scale)),
        Image.Resampling.LANCZOS,
    )
    left = (resized.width - WIDTH) // 2
    top = (resized.height - HEIGHT) // 2
    return resized.crop((left, top, left + WIDTH, top + HEIGHT))


def _smoothstep(value: float) -> float:
    value = min(1.0, max(0.0, value))
    return value * value * (3.0 - 2.0 * value)


def _encode_video(scene_images: list[Image.Image]) -> None:
    """Pipe deterministic RGB frames to FFmpeg using the project subprocess policy."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg is required to regenerate the README demo")

    command = [
        ffmpeg,
        "-y",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{WIDTH}x{HEIGHT}",
        "-r",
        str(FPS),
        "-i",
        "-",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(VIDEO_PATH),
    ]
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    if process.stdin is None or process.stderr is None:
        raise RuntimeError("FFmpeg pipes were not created")

    transition_frames = round(TRANSITION_SECONDS * FPS)
    try:
        for scene_index, scene in enumerate(SCENES):
            frame_count = round(scene.duration * FPS)
            for frame_index in range(frame_count):
                progress = frame_index / max(1, frame_count - 1)
                frame = _animate(scene_images[scene_index], progress)
                if scene_index + 1 < len(scene_images) and frame_index >= frame_count - transition_frames:
                    transition_index = frame_index - (frame_count - transition_frames)
                    alpha = _smoothstep((transition_index + 1) / transition_frames)
                    next_frame = _animate(scene_images[scene_index + 1], 0.0)
                    frame = Image.blend(frame, next_frame, alpha)
                process.stdin.write(frame.tobytes())
    except BrokenPipeError as exc:
        error = process.stderr.read().decode(errors="replace")
        raise RuntimeError(f"FFmpeg stopped while encoding: {error}") from exc
    finally:
        process.stdin.close()

    error = process.stderr.read().decode(errors="replace")
    return_code = process.wait()
    if return_code:
        raise RuntimeError(f"FFmpeg failed with exit code {return_code}: {error}")


def _encode_preview_gif() -> None:
    """Generate a GitHub-compatible inline preview under the 10 MB limit."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg is required to regenerate the README demo")

    with tempfile.TemporaryDirectory(prefix="boss-readme-demo-") as temp_dir:
        palette_path = Path(temp_dir) / "palette.png"
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(VIDEO_PATH),
                "-vf",
                "fps=4,scale=960:-1:flags=lanczos,"
                "palettegen=max_colors=64:stats_mode=diff",
                str(palette_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(VIDEO_PATH),
                "-i",
                str(palette_path),
                "-lavfi",
                "fps=4,scale=960:-1:flags=lanczos[x];"
                "[x][1:v]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle",
                "-loop",
                "0",
                str(PREVIEW_GIF_PATH),
            ],
            check=True,
            capture_output=True,
            text=True,
        )

    gif_size = PREVIEW_GIF_PATH.stat().st_size
    if gif_size > GITHUB_GIF_LIMIT_BYTES:
        raise RuntimeError(
            "README preview GIF exceeds GitHub's 10 MB limit: "
            f"{gif_size / 1024 / 1024:.2f} MB"
        )


def main() -> int:
    """Generate the README poster, MP4, and inline GIF demo."""
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    scene_images = _build_scenes()
    scene_images[0].save(POSTER_PATH, format="PNG", optimize=True)
    _encode_video(scene_images)
    _encode_preview_gif()
    duration = sum(scene.duration for scene in SCENES)
    print(f"Generated {POSTER_PATH.relative_to(ROOT)}")
    print(f"Generated {VIDEO_PATH.relative_to(ROOT)} ({duration:.1f}s, {WIDTH}x{HEIGHT}, {FPS} fps)")
    print(
        f"Generated {PREVIEW_GIF_PATH.relative_to(ROOT)} "
        f"({duration:.1f}s, 960x540, 4 fps)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
