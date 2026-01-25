from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("home", "0015_normalize_foxview_property"),
    ]

    operations = [
        migrations.AddField(
            model_name="expense",
            name="rental_business_use_pct",
            field=models.DecimalField(
                max_digits=5,
                decimal_places=2,
                null=True,
                blank=True,
                help_text="Optional: percent (0–100) of this expense that is attributable to rental use (useful for mixed-use properties like Foxview).",
            ),
        ),
    ]
