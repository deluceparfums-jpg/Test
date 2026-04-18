"""
Motion graphics video generator for Google Stitch.
Generates voiceover via ElevenLabs API and composites animated scenes into a final MP4.
Set ELEVENLABS_API_KEY in your environment before running.
"""

import os
import sys
import math
import tempfile
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from moviepy import ImageClip, AudioFileClip, concatenate_videoclips, CompositeVideoClip
from elevenlabs.client import ElevenLabs
from elevenlabs import VoiceSettings

# ── Config ──────────────────────────────────────────────────────────────────
WIDTH, HEIGHT = 1920, 1080
FPS = 30
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_PATH_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
OUTPUT_PATH = "google_stitch_motion.mp4"
AUDIO_PATH = "voiceover.mp3"

# Google brand colours
GOOGLE_BLUE   = (66,  133, 244)
GOOGLE_RED    = (234,  67,  53)
GOOGLE_YELLOW = (251, 188,   5)
GOOGLE_GREEN  = (52,  168,  83)
BG_DARK       = (15,  15,  25)
BG_MID        = (22,  22,  40)

VOICEOVER_SCRIPT = (
    "Introducing Google Stitch — the AI-powered design tool that changes everything. "
    "Describe your vision in plain language and watch Stitch bring it to life in seconds. "
    "From wireframes to pixel-perfect UI components, Stitch handles the heavy lifting. "
    "Generate stunning mobile and web interfaces with a single prompt. "
    "Iterate in real time. Customize every detail. Export production-ready code instantly. "
    "Google Stitch — design at the speed of thought."
)

# ── Helpers ──────────────────────────────────────────────────────────────────

def load_font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    path = FONT_PATH if bold else FONT_PATH_REGULAR
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * max(0.0, min(1.0, t))


def ease_out_cubic(t: float) -> float:
    return 1 - (1 - t) ** 3


def ease_in_out(t: float) -> float:
    return t * t * (3 - 2 * t)


def blend_colors(c1, c2, t: float):
    return tuple(int(lerp(a, b, t)) for a, b in zip(c1, c2))


def draw_rounded_rect(draw: ImageDraw.ImageDraw, xy, radius: int, fill, alpha: int = 255):
    x0, y0, x1, y1 = xy
    draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=(*fill, alpha))


# ── Background ───────────────────────────────────────────────────────────────

def make_gradient_bg(w: int, h: int, t: float) -> Image.Image:
    """Animated dark-to-blue gradient background."""
    img = Image.new("RGBA", (w, h))
    draw = ImageDraw.Draw(img)
    shift = int(math.sin(t * 0.5) * 30)
    for y in range(h):
        ratio = (y + shift) / h
        color = blend_colors(BG_DARK, (20, 30, 70), ratio)
        draw.line([(0, y), (w, y)], fill=(*color, 255))
    return img


# ── Particle system ──────────────────────────────────────────────────────────

class Particle:
    def __init__(self, seed: int):
        rng = np.random.default_rng(seed)
        self.x = float(rng.integers(0, WIDTH))
        self.y = float(rng.integers(0, HEIGHT))
        self.vx = float(rng.uniform(-0.3, 0.3))
        self.vy = float(rng.uniform(-0.6, -0.1))
        self.radius = int(rng.integers(2, 5))
        palette = [GOOGLE_BLUE, GOOGLE_GREEN, GOOGLE_YELLOW, GOOGLE_RED]
        self.color = palette[seed % len(palette)]
        self.life = float(rng.uniform(0.4, 1.0))

    def position_at(self, t: float):
        x = (self.x + self.vx * t * 60) % WIDTH
        y = (self.y + self.vy * t * 60) % HEIGHT
        alpha = int(self.life * 180 * abs(math.sin(t * 0.7 + self.x * 0.01)))
        return int(x), int(y), alpha


PARTICLES = [Particle(i) for i in range(80)]


def draw_particles(draw: ImageDraw.ImageDraw, t: float):
    for p in PARTICLES:
        x, y, alpha = p.position_at(t)
        draw.ellipse(
            [x - p.radius, y - p.radius, x + p.radius, y + p.radius],
            fill=(*p.color, alpha),
        )


# ── Google colour dots logo ───────────────────────────────────────────────────

def draw_google_dots(draw: ImageDraw.ImageDraw, cx: int, cy: int, r: int, t: float):
    colors = [GOOGLE_BLUE, GOOGLE_RED, GOOGLE_YELLOW, GOOGLE_GREEN]
    for i, color in enumerate(colors):
        angle = math.pi * 2 * i / 4 + t * 0.6
        x = cx + int(math.cos(angle) * r)
        y = cy + int(math.sin(angle) * r)
        draw.ellipse([x - 12, y - 12, x + 12, y + 12], fill=(*color, 230))


# ── Text utilities ────────────────────────────────────────────────────────────

def centered_text(draw: ImageDraw.ImageDraw, text: str, font, y: int,
                  color=(255, 255, 255), alpha: int = 255, w: int = WIDTH):
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    x = (w - tw) // 2
    draw.text((x, y), text, font=font, fill=(*color, alpha))


def draw_glowing_text(img: Image.Image, text: str, font, y: int,
                      color=(255, 255, 255), glow_color=None, alpha: int = 255):
    """Text with a soft glow halo."""
    if glow_color is None:
        glow_color = GOOGLE_BLUE
    # glow layer
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    centered_text(gd, text, font, y - 2, glow_color, min(alpha, 180))
    glow = glow.filter(ImageFilter.GaussianBlur(radius=12))
    img.alpha_composite(glow)
    # sharp text
    td = ImageDraw.Draw(img)
    centered_text(td, text, font, y, color, alpha)


# ── Scene builders ────────────────────────────────────────────────────────────

def make_frame_intro(t: float, duration: float) -> np.ndarray:
    """Scene 1 – Title splash with animated Google dots."""
    progress = t / duration
    img = make_gradient_bg(WIDTH, HEIGHT, t)
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    draw_particles(draw, t)
    draw_google_dots(draw, WIDTH // 2, HEIGHT // 2 - 180, 60, t)

    title_alpha = int(255 * ease_out_cubic(min(progress * 3, 1.0)))
    sub_alpha   = int(255 * ease_out_cubic(max(0, progress * 3 - 0.8)))

    title_font = load_font(110)
    sub_font   = load_font(48, bold=False)
    tag_font   = load_font(32, bold=False)

    draw_glowing_text(overlay, "Google Stitch", title_font,
                      HEIGHT // 2 - 60, (255, 255, 255), GOOGLE_BLUE, title_alpha)

    centered_text(draw, "AI-Powered UI Design", sub_font,
                  HEIGHT // 2 + 80, GOOGLE_BLUE[::-1][:3], sub_alpha)

    tag_alpha = int(255 * ease_out_cubic(max(0, progress * 3 - 1.5)))
    centered_text(draw, "Design at the speed of thought", tag_font,
                  HEIGHT // 2 + 150, (180, 200, 255), tag_alpha)

    img.alpha_composite(overlay)
    return np.array(img.convert("RGB"))


def make_frame_describe(t: float, duration: float) -> np.ndarray:
    """Scene 2 – 'Describe your vision' with typing-prompt animation."""
    progress = t / duration
    img = make_gradient_bg(WIDTH, HEIGHT, t + 10)
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    draw_particles(draw, t + 5)

    # card
    card_alpha = int(220 * ease_out_cubic(min(progress * 2, 1.0)))
    card_x, card_y = WIDTH // 2 - 600, HEIGHT // 2 - 200
    draw_rounded_rect(draw, [card_x, card_y, card_x + 1200, card_y + 400],
                      radius=24, fill=(30, 40, 80), alpha=card_alpha)

    prompt_text = "Create a modern e-commerce checkout screen"
    chars = int(len(prompt_text) * ease_out_cubic(min(progress * 2.5, 1.0)))
    typed = prompt_text[:chars] + ("|" if int(t * 2) % 2 == 0 else "")

    head_font  = load_font(52)
    prompt_font = load_font(40, bold=False)
    body_font  = load_font(30, bold=False)

    head_alpha = int(255 * ease_out_cubic(min(progress * 3, 1.0)))
    draw_glowing_text(overlay, "Describe your vision", head_font,
                      HEIGHT // 2 - 310, (255, 255, 255), GOOGLE_GREEN, head_alpha)

    draw.text((card_x + 40, card_y + 40), "Prompt:", font=body_font,
              fill=(*GOOGLE_YELLOW, card_alpha))
    draw.text((card_x + 40, card_y + 100), typed, font=prompt_font,
              fill=(220, 235, 255, card_alpha))

    # animated output dots
    if progress > 0.6:
        dot_alpha = int(255 * ease_out_cubic((progress - 0.6) / 0.4))
        for i, col in enumerate([GOOGLE_BLUE, GOOGLE_GREEN, GOOGLE_RED]):
            pulse = abs(math.sin(t * 3 + i * 1.2)) * 10
            r = int(16 + pulse)
            bx = card_x + 200 + i * 120
            by = card_y + 300
            draw.ellipse([bx - r, by - r, bx + r, by + r], fill=(*col, dot_alpha))
        draw.text((card_x + 560, card_y + 282), "Generating…", font=body_font,
                  fill=(180, 200, 255, dot_alpha))

    img.alpha_composite(overlay)
    return np.array(img.convert("RGB"))


def make_frame_features(t: float, duration: float) -> np.ndarray:
    """Scene 3 – Four feature cards fly in."""
    progress = t / duration
    img = make_gradient_bg(WIDTH, HEIGHT, t + 20)
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    draw_particles(draw, t + 10)

    features = [
        ("Instant Wireframes",     GOOGLE_BLUE,   "⚡"),
        ("Production Code",        GOOGLE_GREEN,  "💻"),
        ("Real-time Iteration",    GOOGLE_YELLOW, "🔄"),
        ("Multi-platform Export",  GOOGLE_RED,    "📦"),
    ]

    head_font = load_font(60)
    head_alpha = int(255 * ease_out_cubic(min(progress * 2, 1.0)))
    draw_glowing_text(overlay, "Everything you need", head_font,
                      80, (255, 255, 255), GOOGLE_BLUE, head_alpha)

    card_w, card_h = 380, 320
    cols, rows_start = 2, 220
    gap_x = (WIDTH - cols * card_w) // (cols + 1)

    feat_font  = load_font(34)
    emoji_font = load_font(54)

    for i, (label, color, icon) in enumerate(features):
        delay = i * 0.15
        card_progress = ease_out_cubic(max(0, progress * 3 - delay))
        alpha = int(220 * card_progress)
        if alpha <= 0:
            continue

        col_i = i % 2
        row_i = i // 2
        cx = gap_x + col_i * (card_w + gap_x)
        cy = rows_start + row_i * (card_h + 30)
        slide = int((1 - card_progress) * 80)
        cy += slide

        draw_rounded_rect(draw,
                          [cx, cy, cx + card_w, cy + card_h],
                          radius=20, fill=(25, 35, 70), alpha=alpha)
        # coloured accent bar
        draw_rounded_rect(draw,
                          [cx, cy, cx + card_w, cy + 8],
                          radius=4, fill=color, alpha=alpha)

        draw.text((cx + card_w // 2 - 30, cy + 40), icon,
                  font=emoji_font, fill=(*color, alpha))
        bbox = draw.textbbox((0, 0), label, font=feat_font)
        tw = bbox[2] - bbox[0]
        draw.text((cx + (card_w - tw) // 2, cy + 180),
                  label, font=feat_font, fill=(255, 255, 255, alpha))

    img.alpha_composite(overlay)
    return np.array(img.convert("RGB"))


def make_frame_cta(t: float, duration: float) -> np.ndarray:
    """Scene 4 – Call-to-action with pulsing button."""
    progress = t / duration
    img = make_gradient_bg(WIDTH, HEIGHT, t + 30)
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    draw_particles(draw, t + 15)
    draw_google_dots(draw, WIDTH // 2, HEIGHT // 2 - 200, 50, -t * 0.8)

    title_font = load_font(90)
    sub_font   = load_font(42, bold=False)
    btn_font   = load_font(38)

    title_alpha = int(255 * ease_out_cubic(min(progress * 2, 1.0)))
    draw_glowing_text(overlay, "Google Stitch", title_font,
                      HEIGHT // 2 - 130, (255, 255, 255), GOOGLE_BLUE, title_alpha)

    sub_alpha = int(255 * ease_out_cubic(max(0, progress * 2 - 0.4)))
    centered_text(draw, "Design at the speed of thought", sub_font,
                  HEIGHT // 2, (180, 210, 255), sub_alpha)

    btn_alpha = int(255 * ease_out_cubic(max(0, progress * 2 - 0.8)))
    if btn_alpha > 0:
        pulse = abs(math.sin(t * 2)) * 6
        bx, by = WIDTH // 2 - 200, HEIGHT // 2 + 100
        bw, bh = 400, 80
        glow_overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow_overlay)
        draw_rounded_rect(gd,
                          [bx - int(pulse), by - int(pulse / 2),
                           bx + bw + int(pulse), by + bh + int(pulse / 2)],
                          radius=40, fill=GOOGLE_BLUE,
                          alpha=int(60 * btn_alpha / 255))
        glow_overlay = glow_overlay.filter(ImageFilter.GaussianBlur(radius=20))
        overlay.alpha_composite(glow_overlay)

        draw = ImageDraw.Draw(overlay)
        draw_rounded_rect(draw, [bx, by, bx + bw, by + bh],
                          radius=40, fill=GOOGLE_BLUE, alpha=btn_alpha)
        bbox = draw.textbbox((0, 0), "Try Stitch Now", font=btn_font)
        tw = bbox[2] - bbox[0]
        draw.text((bx + (bw - tw) // 2, by + 18), "Try Stitch Now",
                  font=btn_font, fill=(255, 255, 255, btn_alpha))

    img.alpha_composite(overlay)
    return np.array(img.convert("RGB"))


# ── Scene schedule ────────────────────────────────────────────────────────────

SCENES = [
    ("intro",    make_frame_intro,    5.0),
    ("describe", make_frame_describe, 7.0),
    ("features", make_frame_features, 8.0),
    ("cta",      make_frame_cta,      5.0),
]


def build_clip(name: str, frame_fn, duration: float) -> ImageClip:
    print(f"  Rendering scene: {name} ({duration}s)")

    def make_frame(t):
        return frame_fn(t, duration)

    return ImageClip(make_frame, duration=duration).with_fps(FPS)


# ── ElevenLabs voiceover ──────────────────────────────────────────────────────

def generate_voiceover(api_key: str) -> str:
    print("Generating voiceover via ElevenLabs…")
    client = ElevenLabs(api_key=api_key)

    audio_bytes = client.text_to_speech.convert(
        voice_id="21m00Tcm4TlvDq8ikWAM",  # Rachel – clear, professional
        text=VOICEOVER_SCRIPT,
        model_id="eleven_multilingual_v2",
        voice_settings=VoiceSettings(
            stability=0.55,
            similarity_boost=0.80,
            style=0.20,
            use_speaker_boost=True,
        ),
    )

    with open(AUDIO_PATH, "wb") as f:
        for chunk in audio_bytes:
            f.write(chunk)

    print(f"  Saved voiceover → {AUDIO_PATH}")
    return AUDIO_PATH


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        print("ERROR: Set the ELEVENLABS_API_KEY environment variable before running.")
        sys.exit(1)

    audio_path = generate_voiceover(api_key)

    print("Building video clips…")
    clips = [build_clip(name, fn, dur) for name, fn, dur in SCENES]
    video = concatenate_videoclips(clips)

    print("Attaching audio…")
    audio = AudioFileClip(audio_path)

    # trim audio to match video length if needed
    if audio.duration > video.duration:
        audio = audio.subclipped(0, video.duration)
    elif audio.duration < video.duration:
        video = video.subclipped(0, audio.duration)

    final = video.with_audio(audio)

    print(f"Exporting → {OUTPUT_PATH}")
    final.write_videofile(
        OUTPUT_PATH,
        fps=FPS,
        codec="libx264",
        audio_codec="aac",
        preset="fast",
        threads=4,
    )
    print(f"\nDone! Video saved to: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
