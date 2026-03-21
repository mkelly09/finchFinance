import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('home', '0028_add_balance_adjustment_model'),
    ]

    operations = [
        migrations.CreateModel(
            name='MonthEndExpenseCategorySnapshot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('monthly_limit', models.DecimalField(decimal_places=2, max_digits=10)),
                ('actual_spent', models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ('category', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='monthly_snapshots',
                    to='home.category',
                )),
                ('month_close', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='expense_snapshots',
                    to='home.monthendclose',
                )),
            ],
            options={
                'ordering': ['category__name'],
            },
        ),
        migrations.AlterUniqueTogether(
            name='monthendexpensecategorysnapshot',
            unique_together={('month_close', 'category')},
        ),
        migrations.CreateModel(
            name='MonthEndWithholdingCategorySnapshot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('monthly_target', models.DecimalField(decimal_places=2, max_digits=12)),
                ('actual_contributed', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('withholding_category', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='monthly_snapshots',
                    to='home.withholdingcategory',
                )),
                ('month_close', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='withholding_snapshots',
                    to='home.monthendclose',
                )),
            ],
            options={
                'ordering': ['withholding_category__name'],
            },
        ),
        migrations.AlterUniqueTogether(
            name='monthendwithholdingcategorysnapshot',
            unique_together={('month_close', 'withholding_category')},
        ),
        migrations.CreateModel(
            name='MonthEndIncomeCategorySnapshot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('monthly_target', models.DecimalField(decimal_places=2, max_digits=10)),
                ('actual_received', models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ('income_category', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='monthly_snapshots',
                    to='home.incomecategory',
                )),
                ('month_close', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='income_snapshots',
                    to='home.monthendclose',
                )),
            ],
            options={
                'ordering': ['income_category__name'],
            },
        ),
        migrations.AlterUniqueTogether(
            name='monthendincomecategorysnapshot',
            unique_together={('month_close', 'income_category')},
        ),
    ]
