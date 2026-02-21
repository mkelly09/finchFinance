from django.urls import path
from . import views
from django.conf import settings
from django.conf.urls.static import static

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
    path("accounts/<int:account_id>/", views.bank_account_detail, name="bank_account_detail"),

    path("import-transactions/", views.import_transactions, name="import_transactions"),
    path("import-batch/<int:batch_id>/", views.import_batch_detail, name="import_batch_detail"),

    path("withholdings/", views.withholding_overview, name="withholding_overview"),
    path("withholdings/category/<int:pk>/", views.withholding_category_detail, name="withholding_category_detail"),
    path("withholdings/transaction/<int:pk>/update/", views.update_withholding_transaction, name="update_withholding_transaction"),
    # Rental Properties
    path("rental-properties/", views.rental_properties, name="rental_properties"),
    path("rental-properties/<int:property_id>/", views.rental_property_detail, name="rental_property_detail"),
    path("rental-properties/<int:property_id>/tax-summary/", views.rental_tax_summary, name="rental_tax_summary"),
    path(
        "rental-properties/<int:property_id>/tax-summary/<int:cra_category_id>/",
        views.rental_tax_category_detail,
        name="rental_tax_category_detail",
    ),
    path("expenses/<int:expense_id>/edit/", views.expense_edit, name="expense_edit"),
    path("income/<int:income_id>/edit/", views.income_edit, name="income_edit"),
    path("transfers/<int:transfer_id>/edit/", views.transfer_edit, name="transfer_edit"),
    path("unassigned-transactions/", views.unassigned_transactions, name="unassigned_transactions",),

    # Transfer API
    path('api/transfer/<int:transfer_id>/', views.get_transfer_api, name='get_transfer_api'),


]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
