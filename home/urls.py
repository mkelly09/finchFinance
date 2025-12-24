from django.urls import path
from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("category-progress/", views.category_progress, name="category_progress"),

    # Expense category drilldown (existing)
    path("category-expenses/<str:category_name>/", views.category_expense_list, name="category_expense_list"),

    # NEW: Income category drilldown
    path("income-category/<int:pk>/", views.income_category_income_list, name="income_category_income_list"),

    path("categories/", views.category_list, name="category_list"),
    path("update-expense/", views.update_expense, name="update_expense"),
    path("bank-accounts/", views.bank_accounts, name="bank_accounts"),
    path("import-transactions/", views.import_transactions, name="import_transactions"),
    path("import-batch/<int:batch_id>/", views.import_batch_detail, name="import_batch_detail"),

    path("withholdings/", views.withholding_overview, name="withholding_overview"),
    path("withholdings/category/<int:pk>/", views.withholding_category_detail, name="withholding_category_detail"),
    path("withholdings/transaction/<int:pk>/update/", views.update_withholding_transaction, name="update_withholding_transaction"),
]
