from django.db import models
from datetime import date as dt_date

class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    monthly_limit = models.DecimalField(max_digits=10, decimal_places=2)
    savings_target_per_paycheque = models.DecimalField(
        max_digits=10, decimal_places=2,
        null=True, blank=True, help_text="Leave blank if not applicable."
    )

    def __str__(self):
        return self.name


class Expense(models.Model):
    date = models.DateField(default=dt_date.today)
    vendor_name = models.CharField(max_length=100, default="Unknown Vendor")
    category = models.ForeignKey(Category, on_delete=models.CASCADE)
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    location = models.CharField(max_length=100, default="Ottawa")
    notes = models.TextField(blank=True, default='')

    def __str__(self):
        return f"{self.date} | {self.vendor_name} | {self.amount}"


# models.py

from django.db import models

class Income(models.Model):
    CATEGORY_CHOICES = [
        ('Arnprior Rental Income (MAIN)', 'Arnprior Rental Income (MAIN)'),
        ('Arnprior Rental Income (LOFT)', 'Arnprior Rental Income (LOFT)'),
        ('Employment Income', 'Employment Income'),
        ('Investment Income', 'Investment Income'),
    ]

    date = models.DateField()
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES, null=True, blank=True)
    taxable = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    def save(self, *args, **kwargs):
        # Automatically set taxable = True for all categories except Employment Income
        if self.category != 'Employment Income':
            self.taxable = True
        super().save(*args, **kwargs)
