"""Builds the Excel export of a company's documents, honoring the same filters as the
documents table so "Exportar" downloads exactly what the user is looking at."""
from __future__ import annotations

import io
import uuid
from datetime import date

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy.orm import Session

from app.models.document import Document
from app.models.expense_category import ExpenseCategory
from app.models.vendor import Vendor
from app.services.ai_extraction_service import resolve_amount
from app.services.amount_validation_service import latest_final_extractions, load_amount_warnings
from app.services.document_service import document_query

UNCATEGORIZED_LABEL = "Sin categoria"
UNKNOWN_VENDOR_LABEL = "Sin proveedor"

_HEADER_FONT = Font(bold=True, color="FFFFFF")
_HEADER_FILL = PatternFill("solid", fgColor="061C40")  # CostOad navy
_TOTAL_FONT = Font(bold=True)
_MONEY_FORMAT = "#,##0.00"
_DATE_FORMAT = "yyyy-mm-dd"

_DOCUMENT_COLUMNS = [
    ("Fecha documento", 16),
    ("Proveedor", 34),
    ("NIT", 16),
    ("Categoria", 22),
    ("IVA", 16),
    ("Total", 18),
    ("Moneda", 9),
    ("Archivo", 34),
    ("Fecha de carga", 16),
    ("Alertas de valor", 48),
]


def _write_header(sheet, columns: list[tuple[str, int]], row: int = 1) -> None:
    for index, (title, width) in enumerate(columns, start=1):
        cell = sheet.cell(row=row, column=index, value=title)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = Alignment(vertical="center")
        sheet.column_dimensions[get_column_letter(index)].width = width


def _write_totals_table(sheet, start_row: int, title: str, totals: dict[tuple[str, str], dict]) -> int:
    sheet.cell(row=start_row, column=1, value=title).font = Font(bold=True, size=12)
    _write_header(sheet, [("Nombre", 34), ("Moneda", 10), ("Documentos", 13), ("Total", 18)], row=start_row + 1)
    row = start_row + 2
    for (name, currency), bucket in sorted(totals.items(), key=lambda item: item[1]["total"], reverse=True):
        sheet.cell(row=row, column=1, value=name)
        sheet.cell(row=row, column=2, value=currency)
        sheet.cell(row=row, column=3, value=bucket["count"])
        sheet.cell(row=row, column=4, value=round(bucket["total"], 2)).number_format = _MONEY_FORMAT
        row += 1
    return row + 1


def build_documents_workbook(
    db: Session,
    company_id: uuid.UUID,
    vendor_id: uuid.UUID | None = None,
    expense_category_id: uuid.UUID | None = None,
    document_date_from: date | None = None,
    document_date_to: date | None = None,
    missing_info: bool = False,
    amount_alert: bool = False,
    search: str | None = None,
) -> bytes:
    documents = (
        document_query(
            db,
            company_id,
            vendor_id=vendor_id,
            expense_category_id=expense_category_id,
            document_date_from=document_date_from,
            document_date_to=document_date_to,
            missing_info=missing_info,
            search=search,
        )
        .order_by(Document.document_date.desc().nulls_last(), Document.created_at.desc())
        .all()
    )
    warnings_by_id = load_amount_warnings(db, documents)
    if amount_alert:
        documents = [d for d in documents if d.id in warnings_by_id]

    vendor_ids = {d.vendor_id for d in documents if d.vendor_id is not None}
    category_ids = {d.expense_category_id for d in documents if d.expense_category_id is not None}
    vendors = {v.id: v for v in db.query(Vendor).filter(Vendor.id.in_(vendor_ids))} if vendor_ids else {}
    categories = (
        {c.id: c for c in db.query(ExpenseCategory).filter(ExpenseCategory.id.in_(category_ids))}
        if category_ids
        else {}
    )
    extractions = latest_final_extractions(db, [d.id for d in documents])

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Egresos"
    _write_header(sheet, _DOCUMENT_COLUMNS)
    sheet.freeze_panes = "A2"

    by_category: dict[tuple[str, str], dict] = {}
    by_vendor: dict[tuple[str, str], dict] = {}

    for row, document in enumerate(documents, start=2):
        vendor = vendors.get(document.vendor_id)
        category = categories.get(document.expense_category_id)
        total = float(document.total_amount) if document.total_amount is not None else None
        tax = resolve_amount(extractions.get(document.id, {}), "tax_amount")
        currency = document.currency or ""

        values = [
            document.document_date,
            vendor.name if vendor else None,
            vendor.tax_id if vendor else None,
            category.name if category else None,
            tax,
            total,
            currency or None,
            document.original_filename,
            document.created_at.date() if document.created_at else None,
            " | ".join(warnings_by_id.get(document.id, [])) or None,
        ]
        for column, value in enumerate(values, start=1):
            cell = sheet.cell(row=row, column=column, value=value)
            if column in (1, 9) and value is not None:
                cell.number_format = _DATE_FORMAT
            elif column in (5, 6) and value is not None:
                cell.number_format = _MONEY_FORMAT

        if total is None:
            continue
        for totals, name in (
            (by_category, category.name if category else UNCATEGORIZED_LABEL),
            (by_vendor, vendor.name if vendor else UNKNOWN_VENDOR_LABEL),
        ):
            bucket = totals.setdefault((name, currency), {"total": 0.0, "count": 0})
            bucket["total"] += total
            bucket["count"] += 1

    last_row = len(documents) + 1
    if documents:
        sheet.auto_filter.ref = f"A1:{get_column_letter(len(_DOCUMENT_COLUMNS))}{last_row}"
        total_row = last_row + 1
        sheet.cell(row=total_row, column=5, value="Total").font = _TOTAL_FONT
        currencies = {d.currency for d in documents if d.total_amount is not None}
        if len(currencies) <= 1:
            # SUBTOTAL ignores rows hidden by the autofilter, so it follows Excel filtering.
            total_cell = sheet.cell(row=total_row, column=6, value=f"=SUBTOTAL(9,F2:F{last_row})")
            total_cell.number_format = _MONEY_FORMAT
        else:
            # Adding COP and USD together would be meaningless.
            total_cell = sheet.cell(row=total_row, column=6, value="Varias monedas: ver hoja Resumen")
        total_cell.font = _TOTAL_FONT

    summary = workbook.create_sheet("Resumen")
    summary.cell(row=1, column=1, value="Resumen de egresos").font = Font(bold=True, size=14)
    summary.cell(row=2, column=1, value=f"Documentos exportados: {len(documents)}")
    next_row = _write_totals_table(summary, 4, "Por categoria", by_category)
    _write_totals_table(summary, next_row, "Por proveedor", by_vendor)

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
