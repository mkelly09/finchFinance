from django import forms
from .models import Expense, Income, Category, BankAccount


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


class CSVUploadForm(forms.Form):
    """
    Simple form to upload a CSV file from the bank and select which bank account it belongs to.
    """
    csv_file = forms.FileField(label="Bank CSV file")
    bank_account = forms.ModelChoiceField(
        queryset=BankAccount.objects.all(),
        label="Bank account",
        widget=forms.Select(attrs={"class": "form-select"}),
    )


class TransactionImportForm(forms.Form):
    """
    Represents ONE row from the CSV during the review step.

    - entry_type: income or expense
    - For income: choose one of the 4 Income sources
    - For expense: choose a Category (FK)
    - skip: internal flag so a row can be 'deleted' in the UI and ignored on save
    - split_group_id: ID so multiple rows can represent parts of one original transaction
    - is_split_child: marker to indicate a row was created by splitting
    """
    ENTRY_TYPE_CHOICES = [
        ("expense", "Expense"),
        ("income", "Income"),
    ]

    # Hidden flag used when you click "Skip" in the UI (wired in the template)
    skip = forms.BooleanField(required=False, widget=forms.HiddenInput())

    # Hidden split metadata (used by JS & potentially the backend later)
    split_group_id = forms.CharField(required=False, widget=forms.HiddenInput())
    is_split_child = forms.BooleanField(required=False, widget=forms.HiddenInput())

    entry_type = forms.ChoiceField(
        choices=ENTRY_TYPE_CHOICES,
        widget=forms.Select(attrs={
            "class": "form-select",
            # make the type box wider so you can see the full text
            "style": "min-width: 10rem;",
        }),
    )

    date = forms.DateField(
        widget=forms.DateInput(attrs={
            "type": "date",
            "class": "form-control",
        })
    )

    vendor_name = forms.CharField(
        max_length=255,
        widget=forms.TextInput(attrs={"class": "form-control"}),
        help_text="Description / payee as shown on the bank statement.",
    )

    amount = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        widget=forms.NumberInput(attrs={"class": "form-control"}),
        help_text="Use positive numbers; we'll treat income vs expense based on entry type.",
    )

    # Expense side: FK to Category
    expense_category = forms.ModelChoiceField(
        queryset=Category.objects.all(),
        required=False,
        empty_label="(Select expense category)",
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    # Income side: one of the 4 sources from Income model
    income_source = forms.ChoiceField(
        choices=[("", "(Select income source)")] + list(Income.CATEGORY_CHOICES),
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    location = forms.CharField(
        max_length=100,
        required=False,
        initial="Ottawa",
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )

    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            "rows": 1,
            "class": "form-control",
        }),
    )

    def clean(self):
        """
        Ensure that depending on entry_type, the right category/source is provided.

        If skip is True, we don't enforce any of these constraints because the row
        is going to be ignored by the view.
        """
        cleaned = super().clean()

        if cleaned.get("skip"):
            # Row is being skipped; don't validate category/source
            return cleaned

        entry_type = cleaned.get("entry_type")
        expense_category = cleaned.get("expense_category")
        income_source = cleaned.get("income_source")

        if entry_type == "expense" and not expense_category:
            self.add_error("expense_category", "Please select an expense category.")
        if entry_type == "income" and not income_source:
            self.add_error("income_source", "Please select an income source.")

        return cleaned
