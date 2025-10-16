from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("forum", "0014_post_is_placeholder"),
    ]

    operations = [
        migrations.AddField(
            model_name="agent",
            name="speech_profile",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
