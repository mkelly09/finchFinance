from django.db import migrations


def seed_rentals_and_cra(apps, schema_editor):
    RentalProperty = apps.get_model("home", "RentalProperty")
    RentalUnit = apps.get_model("home", "RentalUnit")
    CRACategory = apps.get_model("home", "CRARentalExpenseCategory")

    # --- Properties ---
    arnprior, _ = RentalProperty.objects.get_or_create(
        name="Arnprior",
        defaults={"notes": "Duplex rental property (MAIN + LOFT).", "is_active": True},
    )

    foxview_basement, _ = RentalProperty.objects.get_or_create(
        name="Foxview (Basement)",
        defaults={"notes": "Basement apartment rental unit.", "is_active": True},
    )

    # --- Units ---
    # Arnprior: MAIN / LOFT / Shared
    RentalUnit.objects.get_or_create(
        property=arnprior,
        name="MAIN",
        defaults={"unit_type": "UNIT", "is_active": True},
    )
    RentalUnit.objects.get_or_create(
        property=arnprior,
        name="LOFT",
        defaults={"unit_type": "UNIT", "is_active": True},
    )
    RentalUnit.objects.get_or_create(
        property=arnprior,
        name="Shared/Common",
        defaults={"unit_type": "SHARED", "is_active": True},
    )

    # Foxview (Basement): Basement Apartment + optional Shared/Common
    RentalUnit.objects.get_or_create(
        property=foxview_basement,
        name="Basement Apartment",
        defaults={"unit_type": "UNIT", "is_active": True},
    )
    RentalUnit.objects.get_or_create(
        property=foxview_basement,
        name="Shared/Common",
        defaults={"unit_type": "SHARED", "is_active": True},
    )

    # --- CRA Rental Expense Categories ---
    cra_names_in_order = [
        "Advertising",
        "Insurance",
        "Interest & bank charges",
        "Management & administration fees",
        "Maintenance & repairs",
        "Office expenses",
        "Professional fees (including legal and accounting)",
        "Property taxes",
        "Salaries, wages & benefits (including employer contributions)",
        "Travel",
        "Utilities",
        "Motor vehicle expenses",
        "Other expenses",
    ]

    for i, name in enumerate(cra_names_in_order, start=1):
        CRACategory.objects.get_or_create(
            name=name,
            defaults={"sort_order": i, "is_active": True},
        )


def unseed_rentals_and_cra(apps, schema_editor):
    RentalProperty = apps.get_model("home", "RentalProperty")
    RentalUnit = apps.get_model("home", "RentalUnit")
    CRACategory = apps.get_model("home", "CRARentalExpenseCategory")

    cra_names = [
        "Advertising",
        "Insurance",
        "Interest & bank charges",
        "Management & administration fees",
        "Maintenance & repairs",
        "Office expenses",
        "Professional fees (including legal and accounting)",
        "Property taxes",
        "Salaries, wages & benefits (including employer contributions)",
        "Travel",
        "Utilities",
        "Motor vehicle expenses",
        "Other expenses",
    ]
    CRACategory.objects.filter(name__in=cra_names).delete()

    # Only remove units with no linked expenses/incomes
    for prop_name, unit_name in [
        ("Arnprior", "MAIN"),
        ("Arnprior", "LOFT"),
        ("Arnprior", "Shared/Common"),
        ("Foxview (Basement)", "Basement Apartment"),
        ("Foxview (Basement)", "Shared/Common"),
    ]:
        prop = RentalProperty.objects.filter(name=prop_name).first()
        if not prop:
            continue
        unit = RentalUnit.objects.filter(property=prop, name=unit_name).first()
        if not unit:
            continue
        if unit.expenses.count() == 0 and unit.incomes.count() == 0:
            unit.delete()

    # Only remove properties with no remaining units
    for prop_name in ["Arnprior", "Foxview (Basement)"]:
        prop = RentalProperty.objects.filter(name=prop_name).first()
        if prop and prop.units.count() == 0:
            prop.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("home", "0013_rental_properties_cra_attachments"),
    ]

    operations = [
        migrations.RunPython(seed_rentals_and_cra, unseed_rentals_and_cra),
    ]
