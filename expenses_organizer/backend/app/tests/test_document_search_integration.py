"""Integration test for the free-text document search, against the real Supabase
Postgres. Uses vendor/category names with a unique token so results are scoped to
this test's rows, and cleans up after itself."""
from datetime import date
from uuid import uuid4

from app.db.seed import seed_test_company_and_user
from app.db.session import SessionLocal
from app.models.document import Document
from app.models.expense_category import ExpenseCategory
from app.models.vendor import Vendor
from app.services import document_service


def test_list_documents_search_matches_vendor_category_amount_and_date():
    token = uuid4().hex[:8]
    with SessionLocal() as db:
        company, _ = seed_test_company_and_user(db)
        company_id = company.id
        vendor = Vendor(company_id=company_id, name=f"Panadería Búsqueda {token}")
        other_vendor = Vendor(company_id=company_id, name=f"Ferreteria Busqueda {token}")
        category = ExpenseCategory(company_id=company_id, name=f"Energía {token}")
        db.add_all([vendor, other_vendor, category])
        db.flush()

        def make(vendor_id, amount, doc_date, category_id=None):
            doc = Document(
                company_id=company_id,
                original_filename="factura.pdf",
                source_format="pdf",
                status="ai_extraction_completed",
                mime_type="application/pdf",
                storage_path="uploads/factura.pdf",
                vendor_id=vendor_id,
                expense_category_id=category_id,
                total_amount=amount,
                currency="COP",
                document_date=doc_date,
            )
            db.add(doc)
            return doc

        bakery = make(vendor.id, 186900, date(2026, 3, 15), category.id)
        hardware = make(other_vendor.id, 45250.50, date(2026, 4, 2))
        db.commit()
        ids = {"bakery": bakery.id, "hardware": hardware.id}
        vendor_ids = [vendor.id, other_vendor.id]
        category_id = category.id

    def found(search):
        with SessionLocal() as db:
            items, total = document_service.list_documents(db, company_id, search=search, limit=200)
        assert total == len(items)
        return {item.id for item in items} & set(ids.values())

    try:
        # Vendor name, case- and accent-insensitive.
        assert found(f"panaderia {token}") == {ids["bakery"]}
        assert found(f"BUSQUEDA {token}") == {ids["bakery"], ids["hardware"]}
        # Category name (accent-insensitive).
        assert found(f"energia {token}") == {ids["bakery"]}
        # Amount: Colombian thousands format, plain digits, prefix, and exact cents.
        assert found(f"186.900 {token}") == {ids["bakery"]}
        assert found(f"186900 {token}") == {ids["bakery"]}
        assert found(f"186 {token}") == {ids["bakery"]}
        assert found(f"45.250,50 {token}") == {ids["hardware"]}
        # Document date in ISO and DD/MM/YYYY, full or partial.
        assert found(f"2026-03 {token}") == {ids["bakery"]}
        assert found(f"02/04/2026 {token}") == {ids["hardware"]}
        # Every word must match: vendor + date that don't belong together.
        assert found(f"ferreteria 2026-03 {token}") == set()
        # LIKE wildcards are treated literally.
        assert found(f"% {token}") == set()
    finally:
        with SessionLocal() as db:
            db.query(Document).filter(Document.id.in_(list(ids.values()))).delete(synchronize_session=False)
            db.query(Vendor).filter(Vendor.id.in_(vendor_ids)).delete(synchronize_session=False)
            db.query(ExpenseCategory).filter(ExpenseCategory.id == category_id).delete()
            db.commit()
