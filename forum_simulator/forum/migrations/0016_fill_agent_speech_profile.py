from __future__ import annotations

from django.db import migrations


SPEECH_LOOKUP = {
    "Hothead": {"min_words": 16, "max_words": 36, "mean_words": 24, "sentence_range": [1, 3], "burst_chance": 0.22, "burst_range": [6, 14]},
    "Contrarian": {"min_words": 20, "max_words": 44, "mean_words": 30, "sentence_range": [2, 4], "burst_chance": 0.12, "burst_range": [10, 18]},
    "Helper": {"min_words": 14, "max_words": 32, "mean_words": 22, "sentence_range": [1, 3], "burst_chance": 0.18, "burst_range": [8, 14]},
    "Lorekeeper": {"min_words": 24, "max_words": 52, "mean_words": 34, "sentence_range": [2, 4], "burst_chance": 0.08, "burst_range": [14, 24]},
    "Memetic": {"min_words": 12, "max_words": 26, "mean_words": 18, "sentence_range": [1, 2], "burst_chance": 0.3, "burst_range": [5, 12]},
    "Watcher": {"min_words": 13, "max_words": 28, "mean_words": 20, "sentence_range": [1, 2], "burst_chance": 0.2, "burst_range": [6, 12]},
    "Organic Interface": {"min_words": 18, "max_words": 36, "mean_words": 24, "sentence_range": [1, 3], "burst_chance": 0.16, "burst_range": [8, 16]},
}
DEFAULT_PROFILE = {"min_words": 16, "max_words": 34, "mean_words": 22, "sentence_range": [1, 3], "burst_chance": 0.18, "burst_range": [7, 14]}


def apply_profiles(apps, schema_editor) -> None:
    Agent = apps.get_model("forum", "Agent")
    for agent in Agent.objects.all():
        if agent.speech_profile:
            continue
        profile = SPEECH_LOOKUP.get(agent.archetype) or DEFAULT_PROFILE
        agent.speech_profile = profile
        agent.save(update_fields=["speech_profile"])


def noop_reverse(apps, schema_editor) -> None:
    # Leave speech profiles intact when rolling back; they can be regenerated if needed.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("forum", "0015_agent_speech_profile"),
    ]

    operations = [
        migrations.RunPython(apply_profiles, noop_reverse),
    ]
