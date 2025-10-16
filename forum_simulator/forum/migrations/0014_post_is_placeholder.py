from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("forum", "0013_post_authored_by_operator_post_operator_ip_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="post",
            name="is_placeholder",
            field=models.BooleanField(default=False),
        ),
    ]
