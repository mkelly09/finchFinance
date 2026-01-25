from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("home", "0012_backfill_income_categories"),
    ]

    operations = [
        migrations.CreateModel(
            name="RentalProperty",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100, unique=True)),
                ("notes", models.TextField(blank=True, default="")),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={
                "ordering": ["name"],
            },
        ),
        migrations.CreateModel(
            name="CRARentalExpenseCategory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100, unique=True)),
                ("sort_order", models.PositiveIntegerField(default=0)),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={
                "ordering": ["sort_order", "name"],
            },
        ),
        migrations.CreateModel(
            name="RentalUnit",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100)),
                ("unit_type", models.CharField(choices=[("UNIT", "Unit"), ("SHARED", "Shared/Common")], default="UNIT", max_length=10)),
                ("is_active", models.BooleanField(default=True)),
                ("property", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="units", to="home.rentalproperty")),
            ],
            options={
                "ordering": ["property__name", "name"],
            },
        ),
        migrations.AddConstraint(
            model_name="rentalunit",
            constraint=models.UniqueConstraint(fields=("property", "name"), name="uniq_rentalunit_per_property"),
        ),
        migrations.AddField(
            model_name="expense",
            name="cra_category",
            field=models.ForeignKey(
                blank=True,
                help_text="Optional: CRA rental expense classification for tax reporting.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="expenses",
                to="home.crarentalexpensecategory",
            ),
        ),
        migrations.AddField(
            model_name="expense",
            name="rental_unit",
            field=models.ForeignKey(
                blank=True,
                help_text="Optional: tag this expense to a rental unit (including Shared/Common).",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="expenses",
                to="home.rentalunit",
            ),
        ),
        migrations.AddField(
            model_name="income",
            name="rental_unit",
            field=models.ForeignKey(
                blank=True,
                help_text="Optional: tag this income to a rental unit (e.g. Arnprior MAIN/LOFT).",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="incomes",
                to="home.rentalunit",
            ),
        ),
        migrations.CreateModel(
            name="ExpenseAttachment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("file", models.FileField(upload_to="expense_attachments/%Y/%m/")),
                ("original_name", models.CharField(blank=True, default="", max_length=255)),
                ("uploaded_at", models.DateTimeField(auto_now_add=True)),
                ("expense", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="attachments", to="home.expense")),
            ],
            options={
                "ordering": ["-uploaded_at", "-id"],
            },
        ),
    ]
