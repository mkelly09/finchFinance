from django import forms
from .models import Expense, Income, Category, IncomeCategory, BankAccount, WithholdingCategory



class TransactionForm(forms.Form):
    ENTRY_TYPE_CHOICES = [
        ('expense', 'Expense'),
        ('income', 'Income'),
    ]



    entry_type = forms.ChoiceField(choices=ENTRY_TYPE_CHOICES, initial='expense')
    date = forms.DateField(widget=forms.widgets.DateInput(attrs={'type': 'date'}))
    amount = forms.DecimalField()
    vendor_name = forms.CharField(required=False)
    category = forms.ModelChoiceField(queryset=Category.objects.none(), required=False)
    source = forms.ModelChoiceField(
        queryset=IncomeCategory.objects.all().order_by("name"),
        required=False,
        empty_label="(Select income category)",
    )

    taxable = forms.BooleanField(required=False, initial=True)
    location = forms.CharField(required=False, initial='Ottawa')
    notes = forms.CharField(widget=forms.Textarea(attrs={'rows': 2}), required=False)

    # NEW: link an expense to a withholding bucket (contribution)
    apply_to_withholding = forms.BooleanField(
        required=False,
        initial=False,
        help_text="Also add this amount to a withholding bucket.",
    )
    withholding_category = forms.ModelChoiceField(
        queryset=WithholdingCategory.objects.none(),
        required=False,
        empty_label="(Select withholding bucket)",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields['category'].queryset = Category.objects.all()

        # Only show buckets that belong to withholding accounts
        self.fields['withholding_category'].queryset = (
            WithholdingCategory.objects
            .select_related("account")
            .order_by("account__name", "name")
        )

        # Do NOT hide the fields server-side; let JS toggle visibility
        entry_type_value = (
            self.data.get('entry_type')
            or self.initial.get('entry_type')
            or 'expense'
        )
        selected_source = self.data.get('source') or self.initial.get('source')

        # Default taxable behaviour comes from IncomeCategory.taxable_default
        taxable_default = True
        if selected_source:
            try:
                # selected_source will be a pk string in POST data
                cat_obj = IncomeCategory.objects.get(pk=selected_source)
                taxable_default = cat_obj.taxable_default
            except Exception:
                taxable_default = True

        self.fields["taxable"].initial = taxable_default


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

    NEW:
    - apply_to_withholding: treat this expense as a contribution into a withholding bucket
    - is_withholding_payout: treat this row as a payout from withholding ONLY
                             (no Expense will be created)
    - withholding_category: which withholding bucket to adjust
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


    income_source = forms.ModelChoiceField(
        queryset=IncomeCategory.objects.all().order_by("name"),
        required=False,
        empty_label="(Select income category)",
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

    # NEW withholding fields
    apply_to_withholding = forms.BooleanField(
        required=False,
        label="Contribution",
        help_text="Also add this amount into a withholding bucket.",
    )
    is_withholding_payout = forms.BooleanField(
        required=False,
        label="Payout (no expense)",
        help_text="Treat this as using funds from a withholding bucket only.",
    )
    withholding_category = forms.ModelChoiceField(
        queryset=WithholdingCategory.objects.select_related("account")
        .all()
        .order_by("account__name", "name"),
        required=False,
        empty_label="(Choose withholding bucket)",
        widget=forms.Select(attrs={"class": "form-select"}),
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

        apply_to_withholding = cleaned.get("apply_to_withholding")
        is_withholding_payout = cleaned.get("is_withholding_payout")
        withholding_category = cleaned.get("withholding_category")

        # Expense vs income validations
        if entry_type == "expense":
            # For payout-only rows we do NOT require an expense_category
            if not is_withholding_payout and not expense_category:
                self.add_error("expense_category", "Please select an expense category.")
        elif entry_type == "income":
            if not income_source:
                self.add_error("income_source", "Please select an income source.")

            # For now we don't support withholding adjustments on income rows
            if apply_to_withholding or is_withholding_payout:
                self.add_error(
                    "entry_type",
                    "Withholding adjustments are only supported on expense rows.",
                )

        # Withholding-specific validations
        if (apply_to_withholding or is_withholding_payout) and not withholding_category:
            self.add_error(
                "withholding_category",
                "Select a withholding bucket when adjusting withholding.",
            )

        if apply_to_withholding and is_withholding_payout:
            self.add_error(
                "is_withholding_payout",
                "Choose either a contribution OR a payout, not both.",
            )

        return cleaned
