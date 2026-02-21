from django import forms
from django.utils import timezone
from .models import (
    Expense,
    ExpenseAttachment,
    Income,
    Category,
    IncomeCategory,
    BankAccount,
    WithholdingCategory,
    RentalUnit,
    CRARentalExpenseCategory,
    Transfer,
)




class MultipleFileInput(forms.FileInput):
    allow_multiple_selected = True





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
        ("transfer", "Transfer"),
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

    income_rental_unit = forms.ModelChoiceField(
        queryset=RentalUnit.objects.select_related("property").order_by("property__name", "name"),
        required=False,
        empty_label="(Auto from income source)",
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    expense_rental_unit = forms.ModelChoiceField(
        queryset=RentalUnit.objects.select_related("property").order_by("property__name", "name"),
        required=False,
        empty_label="(None)",
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    from_account = forms.ModelChoiceField(
        queryset=BankAccount.objects.all().order_by("institution", "name"),
        required=False,
        empty_label="(From account)",
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    to_account = forms.ModelChoiceField(
        queryset=BankAccount.objects.all().order_by("institution", "name"),
        required=False,
        empty_label="(To account)",
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

        elif entry_type == "transfer":
            from_account = cleaned.get("from_account")
            to_account = cleaned.get("to_account")

            # At least one account required
            if not from_account and not to_account:
                self.add_error(None, "Transfer requires at least one account (from or to).")

            # Prevent same-account transfers
            if from_account and to_account and from_account == to_account:
                self.add_error(None, "From and To accounts cannot be the same.")

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

class ExpenseEditForm(forms.ModelForm):
    class Meta:
        model = Expense
        fields = [
            "date",
            "vendor_name",
            "category",
            "amount",
            "location",
            "notes",
            "bank_account",
            "rental_unit",
            "cra_category",
            "rental_business_use_pct",
        ]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "vendor_name": forms.TextInput(attrs={"class": "form-control"}),
            "category": forms.Select(attrs={"class": "form-select"}),
            "amount": forms.NumberInput(attrs={"class": "form-control"}),
            "location": forms.TextInput(attrs={"class": "form-control"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "bank_account": forms.Select(attrs={"class": "form-select"}),
            "rental_unit": forms.Select(attrs={"class": "form-select"}),
            "cra_category": forms.Select(attrs={"class": "form-select"}),
            "rental_business_use_pct": forms.NumberInput(attrs={"class": "form-control"}),
        }

class IncomeEditForm(forms.ModelForm):
    class Meta:
        model = Income
        fields = [
            "date",
            "amount",
            "category",
            "income_category",
            "rental_unit",
            "taxable",
            "notes",
            "bank_account",
        ]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "amount": forms.NumberInput(attrs={"class": "form-control"}),
            "category": forms.Select(attrs={"class": "form-select"}),
            "income_category": forms.Select(attrs={"class": "form-select"}),
            "rental_unit": forms.Select(attrs={"class": "form-select"}),
            "taxable": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "bank_account": forms.Select(attrs={"class": "form-select"}),
        }

class TransferEditForm(forms.ModelForm):
    class Meta:
        model = Transfer
        fields = [
            "date",
            "amount",
            "description",
            "notes",
            "from_account",
            "to_account",
            "withholding_category",
        ]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "amount": forms.NumberInput(attrs={"class": "form-control"}),
            "description": forms.TextInput(attrs={"class": "form-control"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "from_account": forms.Select(attrs={"class": "form-select"}),
            "to_account": forms.Select(attrs={"class": "form-select"}),
            "withholding_category": forms.Select(attrs={"class": "form-select"}),
        }

class ExpenseAttachmentUploadForm(forms.Form):
    files = forms.FileField(
        required=False,
        widget=MultipleFileInput(attrs={
            "multiple": True,
            "class": "form-control",
        }),
    )

class WithholdingPayoutForm(forms.Form):
    date = forms.DateField(
        initial=timezone.now().date,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
        label="Date",
    )

    withholding_category = forms.ModelChoiceField(
        queryset=WithholdingCategory.objects
            .select_related("account")
            .order_by("account__name", "name"),
        label="Withholding bucket",
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    amount = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=0.01,
        label="Payout amount",
        widget=forms.NumberInput(attrs={"class": "form-control"}),
        help_text="Enter a positive number. This will reduce the bucket balance.",
    )

    note = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
        label="Note",
    )

class TransactionForm(forms.Form):
    ENTRY_TYPE_CHOICES = [
        ("expense", "Expense"),
        ("income", "Income"),
        ("transfer", "Transfer"),
    ]

    # Common
    entry_type = forms.ChoiceField(choices=ENTRY_TYPE_CHOICES, initial="expense")
    date = forms.DateField(widget=forms.widgets.DateInput(attrs={"type": "date"}))
    amount = forms.DecimalField()

    # Expense fields
    vendor_name = forms.CharField(required=False)
    category = forms.ModelChoiceField(queryset=Category.objects.none(), required=False)
    location = forms.CharField(required=False, initial="Ottawa")

    rental_unit = forms.ModelChoiceField(
        queryset=RentalUnit.objects.select_related("property").order_by(
            "property__name", "name"
        ),
        required=False,
    )
    cra_category = forms.ModelChoiceField(
        queryset=CRARentalExpenseCategory.objects.filter(is_active=True).order_by(
            "sort_order", "name"
        ),
        required=False,
    )
    rental_business_use_pct = forms.DecimalField(
        required=False,
        max_digits=5,
        decimal_places=2,
        min_value=0,
        max_value=100,
    )

    # Income fields
    source = forms.ModelChoiceField(
        queryset=IncomeCategory.objects.all().order_by("name"),
        required=False,
        empty_label="(Select income category)",
    )
    income_rental_unit = forms.ModelChoiceField(
        queryset=RentalUnit.objects.select_related("property").order_by(
            "property__name", "name"
        ),
        required=False,
        empty_label="(Auto from source)",
    )
    taxable = forms.BooleanField(required=False, initial=True)

    # Transfer fields
    from_account = forms.ModelChoiceField(
        queryset=BankAccount.objects.all().order_by("institution", "name"),
        required=False,
        empty_label="(From account)",
    )
    to_account = forms.ModelChoiceField(
        queryset=BankAccount.objects.all().order_by("institution", "name"),
        required=False,
        empty_label="(To account)",
    )

    # NEW: Common bank account for income/expense
    bank_account = forms.ModelChoiceField(
        queryset=BankAccount.objects.all().order_by("institution", "name"),
        required=False,
        empty_label="(Select bank account)",
    )

    # Withholding linkage (used by expense + transfer, but with different semantics)
    apply_to_withholding = forms.BooleanField(
        required=False,
        initial=False,
        help_text="If this expense is funded from a withholding bucket, tick this and choose the bucket.",
    )
    withholding_category = forms.ModelChoiceField(
        queryset=WithholdingCategory.objects.none(),
        required=False,
        empty_label="(Select withholding bucket)",
    )

    # Common
    notes = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 2}),
        required=False,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Expense categories
        self.fields["category"].queryset = Category.objects.all().order_by("name")

        # Withholding buckets – only those attached to withholding accounts
        self.fields["withholding_category"].queryset = (
            WithholdingCategory.objects.select_related("account")
            .order_by("account__name", "name")
        )

        # Bank accounts for transfers and income/expense
        self.fields["from_account"].queryset = BankAccount.objects.all().order_by(
            "institution", "name"
        )
        self.fields["to_account"].queryset = BankAccount.objects.all().order_by(
            "institution", "name"
        )
        self.fields["bank_account"].queryset = BankAccount.objects.all().order_by(
            "institution", "name"
        )

        # Default taxable behaviour comes from IncomeCategory.taxable_default
        entry_type_value = (
            self.data.get("entry_type")
            or self.initial.get("entry_type")
            or "expense"
        )
        selected_source = self.data.get("source") or self.initial.get("source")

        taxable_default = True
        if selected_source:
            try:
                cat_obj = IncomeCategory.objects.get(pk=selected_source)
                if hasattr(cat_obj, "taxable_default"):
                    taxable_default = cat_obj.taxable_default
            except IncomeCategory.DoesNotExist:
                taxable_default = True

        if entry_type_value == "income":
            self.fields["taxable"].initial = taxable_default

    def clean(self):
        cleaned_data = super().clean()
        entry_type = cleaned_data.get("entry_type")
        amount = cleaned_data.get("amount")
        bank_account = cleaned_data.get("bank_account")

        if amount is not None and amount <= 0:
            self.add_error("amount", "Amount must be a positive number.")

        # Require a bank account for income and expense (but not transfer)
        if entry_type in ("expense", "income") and not bank_account:
            self.add_error(
                "bank_account",
                "Please select a bank account for this transaction.",
            )
            # Also raise a non-field error to be absolutely sure form.is_valid() is false
            raise forms.ValidationError(
                "Bank account is required for income and expense entries."
            )

        # Transfer-specific validation
        if entry_type == "transfer":
            from_account = cleaned_data.get("from_account")
            to_account = cleaned_data.get("to_account")
            bucket = cleaned_data.get("withholding_category")

            if not from_account and not to_account:
                raise forms.ValidationError(
                    "For a transfer, at least one of From account or To account must be specified."
                )

            if from_account and to_account and from_account == to_account:
                raise forms.ValidationError(
                    "From account and To account cannot be the same for a transfer."
                )

            if bucket:
                # Enforce: the chosen bucket must live on either the From or To account
                if not (
                    (from_account and from_account == bucket.account)
                    or (to_account and to_account == bucket.account)
                ):
                    raise forms.ValidationError(
                        "When selecting a withholding bucket, either the From account "
                        "or the To account must be that bucket's account."
                    )

        # Expense funded from bucket sanity check
        if entry_type == "expense":
            apply_to_withholding = cleaned_data.get("apply_to_withholding")
            bucket = cleaned_data.get("withholding_category")
            if apply_to_withholding and not bucket:
                self.add_error(
                    "withholding_category",
                    "Select a withholding bucket if this expense is funded from one.",
                )

        return cleaned_data



