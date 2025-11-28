from django import forms
from .models import Expense, Income, Category

from django import forms
from .models import Expense, Income, Category

class TransactionForm(forms.Form):
    ENTRY_TYPE_CHOICES = [
        ('expense', 'Expense'),
        ('income', 'Income'),
    ]

    INCOME_SOURCE_CHOICES = [
        ('Arnprior Rental Income (MAIN)', 'Arnprior Rental Income (MAIN)'),
        ('Arnprior Rental Income (LOFT)', 'Arnprior Rental Income (LOFT)'),
        ('Employment Income', 'Employment Income'),
        ('Investment Income', 'Investment Income'),
    ]

    entry_type = forms.ChoiceField(choices=ENTRY_TYPE_CHOICES, initial='expense')
    date = forms.DateField(widget=forms.widgets.DateInput(attrs={'type': 'date'}))
    amount = forms.DecimalField()
    vendor_name = forms.CharField(required=False)
    category = forms.ModelChoiceField(queryset=Category.objects.none(), required=False)
    source = forms.ChoiceField(choices=INCOME_SOURCE_CHOICES, required=False)
    taxable = forms.BooleanField(required=False, initial=True)
    location = forms.CharField(required=False, initial='Ottawa')
    notes = forms.CharField(widget=forms.Textarea(attrs={'rows': 2}), required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields['category'].queryset = Category.objects.all()

        # Do NOT hide the fields server-side; let JS toggle visibility
        entry_type_value = self.data.get('entry_type') or self.initial.get('entry_type') or 'expense'
        selected_source = self.data.get('source') or self.initial.get('source')

        self.fields['taxable'].initial = (selected_source != 'Employment Income')
