from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('home', '0034_userprofile_pinned_income_categories_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='ForecastWorksheet',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('month', models.DateField(help_text='First day of the forecast month', unique=True)),
                ('state', models.JSONField(default=dict, help_text='Delta state: overrides, excluded, new_rows')),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['-month'],
            },
        ),
    ]
