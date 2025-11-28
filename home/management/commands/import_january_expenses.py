from datetime import datetime
from django.core.management.base import BaseCommand
from home.models import Expense, Category

CATEGORY_MAPPINGS = {
    "Misc": "Miscellaneous",
    "Mobile Phone": "Cell Phone",
    "Extra RRSP Cont": "RRSP Contributions",
    "Arnprior Rental Tax Withholding 1": "Arnprior Rental Tax Withholding (MAIN)",
    "Arnprior Rental Tax Withholding 2": "Arnprior Rental Tax Withholding (LOFT)",
}

RESTAURANT_KEYWORDS = {"restaurant"}

RAW_EXPENSES = [
    ("2024-12-31", "Starting Balance", "Misc", "Starting Balance", 718.31),
    ("2024-12-31", "Nordik", "Misc", "Gift", 125.91),
    ("2025-01-02", "Ultramar", "Gas", None, 98.61),
    ("2025-01-02", "Enbridge", "Arnprior Heat", None, 170.00),
    ("2025-01-03", "Reolink Camera", "Misc", None, 92.65),
    ("2025-01-03", "CYS", "Arnprior Snow Removal", None, 180.80),
    ("2025-01-03", "Bell Canada", "Arnprior Internet", None, 79.10),
    ("2025-01-04", "Metro", "Groceries", None, 84.01),
    ("2025-01-04", "KS On the keys", "Misc", "Restaurant", 23.01),
    ("2025-01-04", "Spotify", "Misc", "Spotify", 14.34),
    ("2025-01-04", "PrimeVideo", "Misc", "Video Rental", 28.24),
    ("2025-01-06", "Costco", "Groceries", None, 213.75),
    ("2025-01-06", "Hydro One", "Arnprior Hydro", None, 128.94),
    ("2025-01-08", "Amazon", "Misc", "Amazon", 65.33),
    ("2025-01-08", "Purewater", "Misc", "Hot Tub", 184.98),
    ("2025-01-09", "Loblaws", "Groceries", None, 25.49),
    ("2025-01-09", "Davis Agency", "Misc", "Gift", 8.23),
    ("2025-01-09", "Taing Jewellers", "Misc", "Gift", 507.37),
    ("2025-01-08", "Foodland", "Groceries", None, 81.57),
    ("2025-01-10", "Amazon", "Misc", "Amazon", 14.68),
    ("2025-01-10", "Amazon", "Misc", "Amazon", 37.27),
    ("2025-01-11", "Starbucks", "Misc", "Coffee", 26.80),
    ("2025-01-11", "Nordik", "Misc", "Gift", 263.84),
    ("2025-01-11", "Buvette Daphnee", "Misc", "Restaurant", 255.55),
    ("2025-01-12", "Foodland", "Groceries", None, 36.19),
    ("2025-01-15", "Transfer to WS", "Arnprior Property Tax", None, 215.00),
    ("2025-01-15", "Transfer to WS", "Foxview Insurance", None, 250.00),
    ("2025-01-15", "Transfer to WS", "Extra RRSP Cont", None, 1775.00),
    ("2025-01-17", "eTransfer to Marlene", "Foxview Property Tax", None, 474.00),
    ("2025-01-13", "LinkedIn Premium", "Misc", None, 26.89),
    ("2025-01-14", "Timmies", "Misc", "Coffee", 3.58),
    ("2025-01-14", "Ultramar", "Gas", None, 103.70),
    ("2025-01-17", "Foodlane", "Groceries", None, 182.07),
    ("2025-01-23", "TD Insurance", "Arnprior Insurance", None, 106.78),
    ("2025-01-19", "IKEA Ottawa", "Misc", "Restaurant", 24.97),
    ("2025-01-19", "Kindle Services", "Misc", "Book", 19.20),
    ("2025-01-19", "Foodland", "Groceries", None, 70.93),
    ("2025-01-20", "Amazon", "Misc", None, 20.33),
    ("2025-01-21", "Fido Mobile", "Mobile Phone", None, 107.35),
    ("2025-01-25", "Transfer to WS", "Arnprior Rental Tax Withholding 2", None, 400.00),
    ("2025-01-30", "TD Insurance", "Arnprior Insurance", None, 179.71),
    ("2025-01-24", "Foodlane", "Groceries", None, 39.23),
    ("2025-01-25", "Black Dog Bistro", "Misc", "Restaurant", 206.83),
    ("2025-01-25", "YIG JONSSONS 806", "Groceries", None, 81.90),
    ("2025-01-25", "Netflix", "Misc", "Netflix", 18.63),
    ("2025-01-25", "TODOIST", "Misc", "TODOIST", 60.00),
    ("2025-01-27", "Open AI Chatgpt", "Misc", "Chat GPT", 33.33),
    ("2025-01-31", "MCAP", "Arnprior Mortgage Principal", None, 980.17),
    ("2025-01-31", "MCAP", "Arnprior Mortgage Interest", None, 1464.47),
    ("2025-01-31", "Transfer to WS", "Arnprior Property Tax", None, 215.00),
    ("2025-01-31", "Transfer to WS", "Foxview Insurance", None, 250.00),
    ("2025-01-31", "Milano pizza", "Misc", None, 40.91),
    ("2025-02-07", "WS Investments", "Arnprior Rental Tax Withholding 1", None, 625.00),
]

class Command(BaseCommand):
    help = "Import cleaned January expenses"

    def handle(self, *args, **options):
        for date_str, vendor, category, sub_category, amount in RAW_EXPENSES:
            original_category = category.strip()
            sub_cat_lower = (sub_category or "").lower()

            if "restaurant" in sub_cat_lower or "restaurant" in original_category.lower():
                category_name = "Restaurants"
            else:
                category_name = CATEGORY_MAPPINGS.get(original_category, original_category)

            try:
                category_obj = Category.objects.get(name__iexact=category_name)
            except Category.DoesNotExist:
                self.stderr.write(f"❌ Category not found: '{category_name}' (from '{original_category}')")
                continue

            notes = sub_category.strip() if sub_category else ""

            Expense.objects.create(
                date=datetime.strptime(date_str, "%Y-%m-%d").date(),
                vendor_name=vendor.strip(),
                category=category_obj,
                amount=amount,
                location="Ottawa",
                notes=notes
            )

        self.stdout.write(self.style.SUCCESS("✅ January expenses imported successfully."))