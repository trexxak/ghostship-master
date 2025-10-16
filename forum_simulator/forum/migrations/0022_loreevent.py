from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("forum", "0021_board_is_hidden_board_visibility_roles_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="LoreEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.CharField(max_length=128, unique=True)),
                ("kind", models.CharField(max_length=32)),
                ("tick", models.PositiveIntegerField(db_index=True)),
                ("meta", models.JSONField(blank=True, default=dict)),
                ("window", models.JSONField(blank=True, default=dict)),
                ("processed_tick", models.PositiveIntegerField(blank=True, null=True)),
                ("processed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["tick", "key"],
            },
        ),
    ]

