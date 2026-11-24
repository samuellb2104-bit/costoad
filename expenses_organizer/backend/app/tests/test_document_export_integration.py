"""Integration test for GET /documents/export against the real Supabase Postgres:
the .xlsx honors the table's filters (here, the search) and its totals add up."""
import io
from datetime import date
from uuid import uuid4

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app.api.deps import get_auth_context
from app.db.seed import seed_test_company_and_user
from app.db.session import SessionLocal
from app.models.document import Document
from app.models.expense_category import ExpenseCategory
from app.models.vendor import Vendor
from app.services.auth_service import AuthContext
from main import app

client = TestClient(app)


def test_export_returns_filtered_workbook_with_totals():
    token = uuid4().hex[:8]
    with SessionLocal() as db:
        company, user = seed_test_company_and_user(db)
        auth = AuthContext(user_id=user.id, company_id=company.id, email=user.email)
        vendor = Vendor(company_id=company.id, name=f"Proveedor Export {token}", tax_id="900123456-7")
        category = ExpenseCategory(company_id=company.id, name=f"Categoria Export {token}")
        db.add_all([vendor, category])
        db.flush()
        docs = [
            Document(
                company_id=company.id,
                original_filename=f"factura-{i}.pdf",
                source_format="pdf",
                status="ai_extraction_completed",
                mime_type="application/pdf",
                storage_path=f"uploads/factura-{i}.pdf",
                vendor_id=vendor.id,
                expense_category_id=category.id,
                total_amount=amount,
                currency="COP",
                document_date=date(2026, 5, i + 1),
            )
            for i, amount in enumerate([100000, 250500])
        ]
        db.add_all(docs)
        db.commit()
        doc_ids = [d.id for d in docs]
        vendor_id, category_id = vendor.id, category.id

    app.dependency_overrides[get_auth_context] = lambda: auth
    try:
        response = client.get("/documents/export", params={"search": token})
        assert response.status_code == 200
        assert response.headers["content-type"].startswith(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        assert "costoad-egresos-" in response.headers["content-disposition"]

        workbook = load_workbook(io.BytesIO(response.content))
        assert workbook.sheetnames == ["Egresos", "Resumen"]

        sheet = workbook["Egresos"]
        header = [cell.value for cell in sheet[1]]
        assert header[:7] == ["Fecha documento", "Proveedor", "NIT", "Categoria", "IVA", "Total", "Moneda"]
        rows = [row for row in sheet.iter_rows(min_row=2, values_only=True) if row[1]]
        assert len(rows) == 2  # only this test's documents match the search
        # Newest document date first; real dates and numbers, not text.
        assert rows[0][0].date() == date(2026, 5, 2)
        assert rows[0][1] == f"Proveedor Export {token}"
        assert rows[0][2] == "900123456-7"
        assert rows[0][5] == 250500
        assert sheet.cell(row=4, column=6).value == "=SUBTOTAL(9,F2:F3)"

        summary_values = [row for row in workbook["Resumen"].iter_rows(values_only=True)]
        category_row = next(r for r in summary_values if r[0] == f"Categoria Export {token}")
        assert category_row[1:] == ("COP", 2, 350500)
    finally:
        app.dependency_overrides.pop(get_auth_context, None)
        with SessionLocal() as db:
            db.query(Document).filter(Document.id.in_(doc_ids)).delete(synchronize_session=False)
            db.query(Vendor).filter(Vendor.id == vendor_id).delete()
            db.query(ExpenseCategory).filter(ExpenseCategory.id == category_id).delete()
            db.commit()
