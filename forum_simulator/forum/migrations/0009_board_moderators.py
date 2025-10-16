from __future__ import annotations

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("forum", "0008_board_parent"),
    ]

    operations = [
        migrations.AddField(
            model_name="board",
            name="moderators",
            field=models.ManyToManyField(blank=True, related_name="moderated_boards", to="forum.agent"),
        ),
    ]
