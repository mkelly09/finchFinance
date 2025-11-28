from django.db import migrations

def load_categories(apps, schema_editor):
    Category = apps.get_model('home', 'Category')

    category_data = [
        {"name": "Arnprior Hydro", "monthly_limit": 130},
        {"name": "Arnprior Heat", "monthly_limit": 170},
        {"name": "Arnprior Internet", "monthly_limit": 80},
        {"name": "Arnprior Insurance", "monthly_limit": 175},
        {"name": "Arnprior Property Tax", "monthly_limit": 430, "savings_target": 215},
        {"name": "Arnprior Snow Removal", "monthly_limit": 180},
        {"name": "Arnprior Rental Tax Withholding (MAIN)", "monthly_limit": 625},
        {"name": "Arnprior Rental Tax Withholding (LOFT)", "monthly_limit": 400},
        {"name": "Arnprior Mortgage Principal", "monthly_limit": 1068.4},
        {"name": "Arnprior Mortgage Interest", "monthly_limit": 1319.5},
        {"name": "Foxview Hydro", "monthly_limit": 475},
        {"name": "Foxview Heat", "monthly_limit": 600},
        {"name": "Foxview Internet", "monthly_limit": 160},
        {"name": "Foxview Insurance", "monthly_limit": 160, "savings_target": 250},
        {"name": "Foxview Property Tax", "monthly_limit": 500},
        {"name": "Groceries", "monthly_limit": 1000},
        {"name": "Restaurants", "monthly_limit": 350},
        {"name": "Gas", "monthly_limit": 250},
        {"name": "Miscellaneous", "monthly_limit": 1500},
        {"name": "RRSP Contributions", "monthly_limit": 1355.9, "savings_target": 677.95},
        {"name": "Cell Phone", "monthly_limit": 85},
        {"name": "Vacation Savings", "monthly_limit": 1500, "savings_target": 750},
        {"name": "Foxview Down Payment Savings", "monthly_limit": 1500, "savings_target": 750},
    ]

    for data in category_data:
        Category.objects.get_or_create(
            name=data["name"],
            defaults={
                "monthly_limit": data["monthly_limit"],
                "savings_target_per_paycheque": data.get("savings_target")
            }
        )

class Migration(migrations.Migration):

    dependencies = [
        ('home', '0001_initial'),  # Replace with the actual latest migration filename
    ]

    operations = [
        migrations.RunPython(load_categories),
    ]
