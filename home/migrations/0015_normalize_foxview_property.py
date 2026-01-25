from django.db import migrations


def normalize_foxview_property(apps, schema_editor):
    RentalProperty = apps.get_model("home", "RentalProperty")
    RentalUnit = apps.get_model("home", "RentalUnit")

    old_prop = RentalProperty.objects.filter(name="Foxview (Basement)").first()
    if not old_prop:
        # Nothing to do
        return

    foxview, _ = RentalProperty.objects.get_or_create(
        name="Foxview",
        defaults={"notes": "Primary residence property with one or more rental units.", "is_active": True},
    )

    # Move all units from old property -> Foxview
    for unit in RentalUnit.objects.filter(property=old_prop):
        # If a unit with same name already exists under Foxview, don't collide:
        existing = RentalUnit.objects.filter(property=foxview, name=unit.name).first()
        if existing:
            # If there's a collision, keep existing and move tagged data manually later if needed.
            # But typically you won't have duplicates.
            continue
        unit.property_id = foxview.id
        unit.save(update_fields=["property"])

    # Delete old property if it has no remaining units
    if RentalUnit.objects.filter(property=old_prop).count() == 0:
        old_prop.delete()


def reverse_normalize_foxview_property(apps, schema_editor):
    """
    Conservative reverse:
    - Recreate 'Foxview (Basement)' if needed
    - Move 'Basement Apartment' and 'Shared/Common' units back if they exist under Foxview
    - Only delete 'Foxview' if it has no units afterwards
    """
    RentalProperty = apps.get_model("home", "RentalProperty")
    RentalUnit = apps.get_model("home", "RentalUnit")

    foxview = RentalProperty.objects.filter(name="Foxview").first()
    if not foxview:
        return

    old_prop, _ = RentalProperty.objects.get_or_create(
        name="Foxview (Basement)",
        defaults={"notes": "Basement apartment rental unit.", "is_active": True},
    )

    # Move the two seeded units back if present
    for unit_name in ["Basement Apartment", "Shared/Common"]:
        unit = RentalUnit.objects.filter(property=foxview, name=unit_name).first()
        if unit:
            # Avoid collisions if unit already exists under old_prop
            if not RentalUnit.objects.filter(property=old_prop, name=unit_name).exists():
                unit.property_id = old_prop.id
                unit.save(update_fields=["property"])

    # Delete Foxview if it has no units left (rare)
    if RentalUnit.objects.filter(property=foxview).count() == 0:
        foxview.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("home", "0014_seed_rentals_and_cra"),
    ]

    operations = [
        migrations.RunPython(normalize_foxview_property, reverse_normalize_foxview_property),
    ]
