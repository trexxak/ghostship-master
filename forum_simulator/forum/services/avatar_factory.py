from __future__ import annotations

import random
import uuid
from pathlib import Path

from django.conf import settings

try:
    from PIL import Image, ImageEnhance
except ImportError:  # pragma: no cover - fallback if Pillow missing
    Image = None  # type: ignore

AVATAR_ROOT = Path(settings.BASE_DIR) / "forum" / "static" / "forum"
BASE_AVATAR_DIR = AVATAR_ROOT / "avatars"
GENERATED_DIR = AVATAR_ROOT / "avatars_generated"
GENERATED_DIR.mkdir(parents=True, exist_ok=True)

BACKGROUND_PALETTE = [
    (17, 24, 39),
    (30, 41, 59),
    (45, 55, 72),
    (59, 130, 246),
    (14, 116, 144),
    (99, 102, 241),
    (147, 51, 234),
    (234, 88, 12),
]
TINT_VARIANTS = [
    (244, 255, 255),
    (233, 255, 251),
    (248, 245, 255),
    (240, 252, 255),
    (255, 245, 235),
]


def ensure_agent_avatar(agent) -> str:
    """Guarantee the agent has an avatar slug, generating one if needed."""
    if getattr(agent, "avatar_slug", None):
        return agent.avatar_slug
    slug = _generate_avatar()
    agent.avatar_slug = slug
    agent.save(update_fields=["avatar_slug", "updated_at"])
    return slug


def _generate_avatar() -> str:
    if Image is None or not BASE_AVATAR_DIR.exists():
        return "forum/avatars/ghost_001.png"
    base_files = list(BASE_AVATAR_DIR.glob("ghost_*.png")) or list(BASE_AVATAR_DIR.glob("*.png"))
    if not base_files:
        return "forum/avatars/ghost_001.png"
    base_path = random.choice(base_files)
    image = Image.open(base_path).convert("RGBA")

    background = Image.new("RGBA", image.size, random.choice(BACKGROUND_PALETTE) + (255,))
    tint = Image.new("RGBA", image.size, random.choice(TINT_VARIANTS) + (0,))

    enhancer = ImageEnhance.Color(image)
    image = enhancer.enhance(random.uniform(0.8, 1.4))
    tinted = Image.blend(image, tint, alpha=0.35)
    composite = Image.alpha_composite(background, tinted)

    slug = f"avatars_generated/ghost_{uuid.uuid4().hex[:10]}.png"
    target = GENERATED_DIR / Path(slug).name
    composite.convert("RGB").save(target, format="PNG")
    return f"forum/{slug}"
