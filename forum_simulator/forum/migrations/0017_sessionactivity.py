from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("forum", "0016_fill_agent_speech_profile"),
    ]

    operations = [
        migrations.CreateModel(
            name="SessionActivity",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("session_key", models.CharField(max_length=64, unique=True)),
                ("acting_as_organic", models.BooleanField(default=False)),
                ("last_path", models.CharField(blank=True, max_length=255)),
                ("last_seen", models.DateTimeField(auto_now=True)),
                (
                    "agent",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="session_activity",
                        to="forum.agent",
                    ),
                ),
            ],
            options={
                "ordering": ["-last_seen"],
            },
        ),
        migrations.AddIndex(
            model_name="sessionactivity",
            index=models.Index(fields=["last_seen"], name="forum_sessi_last_se_c8e343_idx"),
        ),
        migrations.AddIndex(
            model_name="sessionactivity",
            index=models.Index(fields=["acting_as_organic"], name="forum_sessi_acting_a_d3eb72_idx"),
        ),
    ]
