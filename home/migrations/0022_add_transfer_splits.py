from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('home', '0021_withholdingcategory_monthly_target'),
    ]

    operations = [
        migrations.AddField(
            model_name='transfer',
            name='parent_transfer',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='splits',
                to='home.transfer',
                help_text='Parent transfer if this is a split'
            ),
        ),
        migrations.AddField(
            model_name='transfer',
            name='is_split_parent',
            field=models.BooleanField(
                default=False,
                help_text='True if this transfer has been split into child transfers'
            ),
        ),
        migrations.AddField(
            model_name='transfer',
            name='split_order',
            field=models.PositiveSmallIntegerField(
                blank=True, null=True,
                help_text='Order of split (1, 2, 3...) for display purposes'
            ),
        ),
        migrations.AddIndex(
            model_name='transfer',
            index=models.Index(
                fields=['parent_transfer', 'split_order'],
                name='transfer_split_idx'
            ),
        ),
    ]
